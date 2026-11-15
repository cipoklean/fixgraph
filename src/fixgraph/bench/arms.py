"""The four arms.

Each arm gets the same episode history and the same candidate fixes, and may
write to its own memory after each attempt. One `lookup` per scenario returns
an ordered candidate list plus the context text an agent would be handed
(that string is what the token metric counts).

- NoMemoryArm: fixed default order, no context.
- VectorArm: every episode embedded as text; retrieve top-k nearest to the
  query (error + env), try the fixes of successful records in rank order.
  sentence-transformers/all-MiniLM-L6-v2 — a real embedding model.
- VectorFilteredArm: the same, restricted to records whose env matches.
  The strongest fair baseline.
- FixGraphArm: the library's own lookup + report.
"""

from __future__ import annotations

import random
import zlib

import numpy as np

from .scenarios import Scenario
from ..signatures import node_id

TOP_K = 10


class Arm:
    name = "arm"

    def lookup(self, sc: Scenario) -> tuple[list[int], str]:
        """Return (ordered fix indices, context text for the agent)."""
        raise NotImplementedError

    def report(self, sc: Scenario, fix_idx: int, outcome: str,
               session: list[int]) -> None:
        """Record an attempt outcome. `session` = indices tried so far."""

    def consolidate(self) -> None:
        pass


def _dedupe_order(indices):
    seen, out = set(), []
    for i in indices:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


class NoMemoryArm(Arm):
    name = "no-memory"

    def lookup(self, sc: Scenario):
        return list(range(len(sc.fixes))), ""


class _VectorBase(Arm):
    _model = None

    @classmethod
    def model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer("all-MiniLM-L6-v2")
        return cls._model

    def __init__(self):
        self.records: list[dict] = []
        self._emb_parts: list[np.ndarray] = []

    def report(self, sc, fix_idx, outcome, session):
        text = (f"{sc.error_text} | env:{sc.env_label} "
                f"[{' '.join(sc.env)}] | fix:{sc.fixes[fix_idx]} | "
                f"outcome:{outcome}")
        self.records.append({
            "text": text,
            "env_label": sc.env_label,
            "action": sc.fixes[fix_idx],
            "outcome": outcome,
        })
        # embed once when recorded — the record text never changes
        self._emb_parts.append(self.model().encode(
            [text], normalize_embeddings=True))

    def filter_idx(self, sc: Scenario) -> list[int]:
        return list(range(len(self.records)))

    def lookup(self, sc: Scenario):
        pool = self.filter_idx(sc)
        if not pool:
            return list(range(len(sc.fixes))), ""
        self._emb = np.vstack(self._emb_parts)
        q = self.model().encode(
            [f"{sc.error_text} | env:{sc.env_label} [{' '.join(sc.env)}]"],
            normalize_embeddings=True)[0]
        sims = self._emb[pool] @ q
        order = [pool[i] for i in np.argsort(-sims)[:TOP_K]]
        by_action = {a: i for i, a in enumerate(sc.fixes)}
        ranked = [by_action[self.records[i]["action"]] for i in order
                  if self.records[i]["outcome"] == "ok"
                  and self.records[i]["action"] in by_action]
        ranked += list(range(len(sc.fixes)))
        ctx = "\n---\n".join(self.records[i]["text"] for i in order)
        return _dedupe_order(ranked), ctx


class VectorArm(_VectorBase):
    name = "vector"


class VectorFilteredArm(_VectorBase):
    name = "vector+env-filter"

    def filter_idx(self, sc: Scenario) -> list[int]:
        return [i for i, r in enumerate(self.records)
                if r["env_label"] == sc.env_label]


class FixGraphArm(Arm):
    name = "fixgraph"

    def __init__(self, fg, run_ns: str = "run"):
        self.fg = fg
        self.run_ns = run_ns
        self._ep_counter = 0
        # (sig_text, env, fix_action) triples observed to fail this run —
        # env-keyed so a fix failing in one environment and succeeding in
        # another can't fabricate a prerequisite
        self._failed_before: set[tuple[str, tuple, str]] = set()

    def lookup(self, sc: Scenario):
        res = self.fg.lookup(sc.error_text, sc.env)
        by_action = {a: i for i, a in enumerate(sc.fixes)}
        by_fid = {c.fix_id: c for c in res.candidates}
        # deterministically shuffle score-tied candidates per scenario so
        # unexplored fixes get coverage instead of a fixed alphabetical walk
        rng = random.Random(zlib.crc32((sc.sid + self.run_ns).encode()))
        groups: dict[float, list] = {}
        for c in res.candidates:
            groups.setdefault(round(c.score, 6), []).append(c)
        ordered = []
        for score in sorted(groups, reverse=True):
            g = groups[score]
            rng.shuffle(g)
            ordered.extend(g)
        ranked: list[int] = []
        for c in ordered:
            # expand REQUIRES chains: prerequisites go before the fix itself
            for rid in c.requires:
                rc = by_fid.get(rid)
                if rc and rc.action in by_action:
                    ranked.append(by_action[rc.action])
            if c.action in by_action:
                ranked.append(by_action[c.action])
        ranked += list(range(len(sc.fixes)))
        return _dedupe_order(ranked), res.context_text()

    def report(self, sc, fix_idx, outcome, session):
        self._ep_counter += 1
        ep_id = node_id("episode",
                        f"{self.run_ns}:{sc.sid}:{fix_idx}:{self._ep_counter}")
        self.fg.report(sc.error_text, sc.env, sc.fixes[fix_idx], outcome,
                       episode_id=ep_id, seq=self._ep_counter)
        # chain learning: a fix earns a REQUIRES edge only if it previously
        # FAILED on this signature and now works right after another fix was
        # applied — evidence the earlier fix was a prerequisite, not just a
        # wrong guess. session already includes fix_idx, so the prerequisite
        # candidate is session[-2].
        if outcome == "ok" and len(session) >= 2:
            key = (sc.error_text, tuple(sorted(sc.env)), sc.fixes[fix_idx])
            if key in self._failed_before:
                self.fg.add_fix(sc.fixes[fix_idx],
                                requires=[sc.fixes[session[-2]]])
        elif outcome == "fail":
            self._failed_before.add(
                (sc.error_text, tuple(sorted(sc.env)), sc.fixes[fix_idx]))
        return ep_id

    def consolidate(self):
        self.fg.consolidate()
