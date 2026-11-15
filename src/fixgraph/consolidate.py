"""Periodic consolidation pass.

Walks every signature, re-derives per-facet outcome counts from the
append-only episode log, and writes CONFIRMED_FOR summary edges for
(signature, fix) pairs with enough support. Lookups keep reading the episode
log — the summary edges are what a *cheap* lookup (and the results page)
serves first, and they shrink the context an agent needs.

Also refreshes nothing destructively: episodes stay the source of truth, so a
consolidation bug can never corrupt memory — the edges are derived data and
can be rebuilt from scratch.
"""

from __future__ import annotations

import math
import time

from .adapter import HydraAdapter
from .lookup import DECAY_BASE
from . import store


def _decayed_pair(ok_orders: list[float], fail_orders: list[float]) -> tuple[float, float]:
    """Merged-stream decay: k-th newest outcome weighs DECAY_BASE**k."""
    events = ([(o, True) for o in ok_orders] +
              [(o, False) for o in fail_orders])
    events.sort(key=lambda x: x[0], reverse=True)
    ok_w = fail_w = 0.0
    for k, (_, is_ok) in enumerate(events):
        if is_ok:
            ok_w += DECAY_BASE ** k
        else:
            fail_w += DECAY_BASE ** k
    return ok_w, fail_w


def consolidate(db: HydraAdapter, min_support: float = 1.5) -> dict[str, int]:
    now = time.time()

    # every signature (label-anchored scan — we labelled them at write time)
    sigs = db.query("MATCH (s:Signature) RETURN s.id AS sid")
    written = 0

    for row in sigs:
        sid = row["sid"]
        ep_facets: dict[int, set[str]] = {}
        ep_ord: dict[int, float] = {}
        ok_map: dict[int, dict[int, float]] = {}
        fail_map: dict[int, dict[int, float]] = {}

        for r in db.query(
            "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:IN]->(f:Facet) "
            "RETURN e.id AS eid, e.ts AS ts, e.seq AS seq, f.key AS fkey",
            {"sig": sid}
        ):
            ep_facets.setdefault(r["eid"], set()).add(r["fkey"])
            ep_ord[r["eid"]] = r.get("seq") or r.get("ts") or 0.0

        for etype, sink in (("TRIED_OK", ok_map), ("TRIED_FAIL", fail_map)):
            for r in db.query(
                f"MATCH (s:Signature {{id: $sig}})<-[:HIT]-(e:Episode)-[:{etype}]->(x:Fix) "
                "RETURN e.id AS eid, x.id AS fid", {"sig": sid}
            ):
                sink.setdefault(r["fid"], {})[r["eid"]] = ep_ord.get(r["eid"], 0.0)

        # which facet best separates ok from fail for this signature?
        facet_ok: dict[str, float] = {}
        facet_n: dict[str, float] = {}
        ok_eids = {e for f in ok_map.values() for e in f}
        for eid, facets in ep_facets.items():
            for fk in facets:
                facet_n[fk] = facet_n.get(fk, 0) + 1
                if eid in ok_eids:
                    facet_ok[fk] = facet_ok.get(fk, 0) + 1
        decisive = max(
            (fk for fk in facet_n if facet_n[fk] >= 2),
            key=lambda fk: abs(facet_ok.get(fk, 0) / facet_n[fk] - 0.5),
            default="",
        )

        # write one summary edge per fix with real support
        for fid in set(ok_map) | set(fail_map):
            ok_w, fail_w = _decayed_pair(
                list(ok_map.get(fid, {}).values()),
                list(fail_map.get(fid, {}).values()))
            if ok_w + fail_w >= min_support and ok_w > fail_w:
                store.write_confirmed_edge(db, sid, fid, decisive, ok_w, fail_w)
                written += 1

    return {"signatures": len(sigs), "confirmed_edges": written}
