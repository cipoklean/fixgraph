"""Public library interface: lookup / report / add_fix / consolidate."""

from __future__ import annotations

import time

from .adapter import HydraAdapter, from_env
from .lookup import LookupResult, lookup as _lookup
from .model import Episode, Fix
from .signatures import signature_id
from . import store


class FixGraph:
    def __init__(self, db: HydraAdapter | None = None, agent: str = "fixgraph"):
        self.db = db or from_env()
        self.agent = agent
        # ids are deterministic and node upserts are idempotent, so skipping
        # a repeat upsert this session is safe and saves an HTTP round-trip
        self._upserted: set[int] = set()

    def lookup(self, error_text: str, env: list[str]) -> LookupResult:
        return _lookup(self.db, error_text, env)

    def report(self, error_text: str, env: list[str], fix_action: str,
               outcome: str, episode_id: int | None = None,
               ts: float | None = None, seq: int = 0) -> int:
        """Record one attempt. `outcome` is 'ok' or 'fail'.

        Pass ``episode_id`` to make retries idempotent; omit it and a fresh
        id is generated (returned so the caller can keep it).
        """
        assert outcome in ("ok", "fail")
        sid, sig = signature_id(error_text)
        fix = Fix(fix_action)
        ep = Episode(
            signature_id=sid, fix_id=fix.id, outcome=outcome,
            facets=env, ts=ts or time.time(), seq=seq, agent=self.agent,
        )
        if episode_id is not None:
            ep.id = episode_id
        if fix.id not in self._upserted:
            store.upsert_fix(self.db, fix)
            self._upserted.add(fix.id)
        if sid not in self._upserted:
            store.upsert_signature(self.db, sid, sig)
            self._upserted.add(sid)
        new_facets = [k for k in env if store.facet_id(k) not in self._upserted]
        if new_facets:
            store.upsert_facets(self.db, new_facets)
            self._upserted.update(store.facet_id(k) for k in new_facets)
        store.record_episode_nodes(self.db, ep)
        store.record_episode_edges(self.db, ep)
        return ep.id

    def add_fix(self, action: str, kind: str = "command", title: str = "",
                requires: list[str] | None = None,
                supersedes: list[str] | None = None) -> int:
        fix = Fix(action=action, kind=kind, title=title)
        store.upsert_fix(self.db, fix)
        for req in requires or []:
            rfix = Fix(req)
            if rfix.id not in self._upserted:
                store.upsert_fix(self.db, rfix)
                self._upserted.add(rfix.id)
            store.link_fixes(self.db, fix.id, rfix.id, "REQUIRES")
        for old in supersedes or []:
            store.link_fixes(self.db, Fix(old).id, fix.id, "SUPERSEDED_BY")
        return fix.id

    def link_failure_chain(self, first_error: str, then_error: str) -> None:
        a, _ = signature_id(first_error)
        b, _ = signature_id(then_error)
        store.link_signatures(self.db, a, b)

    def consolidate(self) -> dict[str, int]:
        """Write CONFIRMED_FOR summary edges for well-supported
        (signature, deciding-facet, fix) triples. Returns counts."""
        from .consolidate import consolidate as _cons
        return _cons(self.db)
