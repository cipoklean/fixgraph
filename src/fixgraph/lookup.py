"""Lookup: signature -> traversal -> ranked fixes with evidence.

Three anchored traversals (each is a query the narrow dialect accepts):

    episodes+facets: (s:Signature)<-[:HIT]-(e:Episode)-[:IN]->(f:Facet)
    successes:       (s:Signature)<-[:HIT]-(e:Episode)-[:TRIED_OK]->(x:Fix)
    failures:        (s:Signature)<-[:HIT]-(e:Episode)-[:TRIED_FAIL]->(x:Fix)

Scoring happens in this file, in plain Python, because the dialect's RETURN
is too narrow for it — and because the ranking rule is part of the product:

- each episode is weighted by how much of its environment the current
  environment shares (environment discrimination),
- outcomes decay over the *event sequence*, not wall-clock time: the k-th
  most recent outcome for a fix weighs DECAY_BASE**k, so a fix that used to
  work and just started failing drops out within an outcome or two,
- fixes are ranked by the lower bound of a Wilson score interval on the
  weighted counts, so one lucky success does not outrank ten-of-twelve.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .adapter import HydraAdapter
from .signatures import signature_id

DECAY_BASE = 0.6           # weight of the k-th most recent outcome for a fix
MIN_SHARED_FACETS = 1
PRIOR_WEIGHT = 0.15        # env-irrelevant episodes still inform, weakly


def wilson_lower_bound(ok: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = ok / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


@dataclass
class FixCandidate:
    fix_id: int
    action: str
    kind: str
    title: str
    score: float
    ok_w: float
    fail_w: float
    n_episodes: int
    last_ts: float
    shared_facets: list[str] = field(default_factory=list)
    requires: list[int] = field(default_factory=list)
    superseded_by: int | None = None


@dataclass
class LookupResult:
    signature_id: int
    signature_text: str
    seen: bool
    candidates: list[FixCandidate]
    deciding_facets: list[str]
    next_signatures: list[tuple[int, str]]
    episode_count: int

    def context_text(self, top: int = 3) -> str:
        """What an agent would actually be handed. Sized for tokens."""
        if not self.seen or not self.candidates:
            return ""
        lines = [f"Known failure: {self.signature_text[:120]}"]
        for c in self.candidates[:top]:
            lines.append(
                f"- {c.title or c.action[:80]} [{c.kind}] "
                f"score={c.score:.2f} ok={c.ok_w:.1f} fail={c.fail_w:.1f} "
                f"episodes={c.n_episodes}"
            )
            lines.append(f"  fix: {c.action[:200]}")
        if self.deciding_facets:
            lines.append("deciding facets: " + ", ".join(self.deciding_facets[:5]))
        return "\n".join(lines)


def lookup(db: HydraAdapter, error_text: str, env: list[str],
           now: float | None = None) -> LookupResult:
    now = now or time.time()
    sid, sig = signature_id(error_text)
    env_keys = {k.strip().lower() for k in env}

    ep_facets: dict[int, set[str]] = {}
    ep_seq: dict[int, tuple[float, float]] = {}   # eid -> (seq, ts)
    fix_meta: dict[int, tuple[str, str, str]] = {}
    ok_map: dict[int, dict[int, float]] = {}      # fix_id -> {eid: order}
    fail_map: dict[int, dict[int, float]] = {}

    for r in db.query(
        "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:IN]->(f:Facet) "
        "RETURN e.id AS eid, e.ts AS ts, e.seq AS seq, f.key AS fkey",
        {"sig": sid},
    ):
        eid = r["eid"]
        ep_facets.setdefault(eid, set()).add(r["fkey"])
        # seq is the logical order when callers supply it; ts is the fallback
        ep_seq[eid] = (r.get("seq") or 0.0, r.get("ts") or 0.0)

    for etype, sink in (("TRIED_OK", ok_map), ("TRIED_FAIL", fail_map)):
        for r in db.query(
            f"MATCH (s:Signature {{id: $sig}})<-[:HIT]-(e:Episode)-[:{etype}]->(x:Fix) "
            "RETURN e.id AS eid, x.id AS fid, x.action AS action, "
            "x.kind AS kind, x.title AS title",
            {"sig": sid},
        ):
            seq, ts = ep_seq.get(r["eid"], (0.0, 0.0))
            sink.setdefault(r["fid"], {})[r["eid"]] = seq or ts
            fix_meta[r["fid"]] = (r.get("action") or "", r.get("kind") or "",
                                  r.get("title") or "")

    episode_count = len(ep_facets)
    all_fix_ids = set(ok_map) | set(fail_map)

    superseded: dict[int, int] = {}
    if all_fix_ids:
        for r in db.query(
            "MATCH (x:Fix)-[:SUPERSEDED_BY]->(n:Fix) "
            "RETURN x.id AS src, n.id AS dst"
        ):
            if r["src"] in all_fix_ids:
                superseded[r["src"]] = r["dst"]

    def env_weight(eid: int) -> float:
        facets = ep_facets.get(eid, set())
        if not facets:
            return PRIOR_WEIGHT
        shared = len(facets & env_keys)
        if shared < MIN_SHARED_FACETS:
            return PRIOR_WEIGHT
        return max(PRIOR_WEIGHT, shared / len(facets | env_keys))

    def decayed_sums(fid: int) -> tuple[float, float]:
        """Rank over the MERGED ok+fail stream, newest-first: the k-th most
        recent outcome for this fix weighs DECAY_BASE**k times the episode's
        environment relevance. An old pile of successes cannot outvote fresh
        failures — that is what makes a stale fix drop out in a round or two."""
        events = ([(o, e, True) for e, o in ok_map.get(fid, {}).items()] +
                  [(o, e, False) for e, o in fail_map.get(fid, {}).items()])
        events.sort(key=lambda x: x[0], reverse=True)
        ok_w = fail_w = 0.0
        for k, (_, eid, is_ok) in enumerate(events):
            w = env_weight(eid) * DECAY_BASE ** k
            if is_ok:
                ok_w += w
            else:
                fail_w += w
        return ok_w, fail_w

    candidates: list[FixCandidate] = []
    for fid in all_fix_ids:
        ok_w, fail_w = decayed_sums(fid)
        n = ok_w + fail_w
        score = wilson_lower_bound(ok_w, n)
        if fid in superseded:
            score *= 0.25
        shared = sorted({f for e in ok_map.get(fid, {})
                         for f in ep_facets.get(e, set()) & env_keys})
        action, kind, title = fix_meta[fid]
        candidates.append(FixCandidate(
            fix_id=fid, action=action, kind=kind, title=title,
            score=score, ok_w=ok_w, fail_w=fail_w,
            n_episodes=len(ok_map.get(fid, {})) + len(fail_map.get(fid, {})),
            last_ts=max(ep_seq.get(e, (0, 0))[1]
                        for e in list(ok_map.get(fid, {})) + list(fail_map.get(fid, {}))) if (ok_map.get(fid) or fail_map.get(fid)) else 0.0,
            shared_facets=shared, superseded_by=superseded.get(fid),
        ))
    candidates.sort(key=lambda c: (-c.score, c.title))

    # deciding facets: facet keys whose episodes split ok/fail — the learned
    # "what matters about this environment"
    ok_eids = {e for f in ok_map.values() for e in f}
    facet_ok: dict[str, float] = {}
    facet_n: dict[str, float] = {}
    for eid, facets in ep_facets.items():
        for fk in facets:
            facet_n[fk] = facet_n.get(fk, 0) + 1
            if eid in ok_eids:
                facet_ok[fk] = facet_ok.get(fk, 0) + 1
    deciding = [fk for fk, n in facet_n.items()
                if n >= 2 and 0.15 < facet_ok.get(fk, 0) / n < 0.85]
    deciding.sort(key=lambda fk: -facet_n[fk])

    for c in candidates[:5]:
        chain = db.query(
            "MATCH (x:Fix {id: $id})-[:REQUIRES*1..4]->(r:Fix) "
            "RETURN r.id AS rid", {"id": c.fix_id}
        )
        seen, ordered = set(), []
        for r in chain:
            if r["rid"] not in seen and r["rid"] != c.fix_id:
                seen.add(r["rid"])
                ordered.append(r["rid"])
        c.requires = ordered

    next_sigs = [
        (r["nid"], r.get("text") or "")
        for r in db.query(
            "MATCH (s:Signature {id: $sig})-[:LEADS_TO*1..2]->(n:Signature) "
            "RETURN n.id AS nid, n.text AS text", {"sig": sid}
        )
        if r["nid"] != sid
    ]

    return LookupResult(
        signature_id=sid, signature_text=sig,
        seen=episode_count > 0,
        candidates=candidates, deciding_facets=deciding[:8],
        next_signatures=next_sigs, episode_count=episode_count,
    )


def explain_cypher() -> list[str]:
    """The exact statements lookup runs — shown on the results page."""
    return [
        "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:IN]->(f:Facet) "
        "RETURN e.id AS eid, e.ts AS ts, e.seq AS seq, f.key AS fkey",
        "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:TRIED_OK]->(x:Fix) "
        "RETURN e.id AS eid, x.id AS fid, x.action AS action, x.kind AS kind, x.title AS title",
        "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:TRIED_FAIL]->(x:Fix) "
        "RETURN e.id AS eid, x.id AS fid, x.action AS action, x.kind AS kind, x.title AS title",
    ]
