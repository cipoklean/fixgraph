"""Writes against HydraDB, all inside the verified dialect subset.

Batch shapes used (from USING-HYDRADB.md):

- nodes+label+props:  UNWIND $rows AS row MERGE (n {id: row.id})
                      SET n:Label, n.prop = row.prop      (exactly one label)
- edges (dedup):      UNWIND $rows AS row CREATE (a {id: row.src})-[:T]->(b {id: row.dst})
- single edge:        MERGE (a {id: $s})-[:T]->(b {id: $d})
- edge props:         MERGE (a {id: $s})-[:T {k: $v}]->(b {id: $d})

Labels always go in the batch *before* the edge batch, so no node ever
exists unlabelled-and-undetectable.
"""

from __future__ import annotations

from .adapter import BATCH_LIMIT, HydraAdapter
from .model import Episode, Fix, facet_id


def _chunks(rows: list[dict], n: int = BATCH_LIMIT):
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def upsert_signature(db: HydraAdapter, sid: int, text: str) -> None:
    db.mutate(
        "UNWIND $rows AS row MERGE (n {id: row.id}) "
        "SET n:Signature, n.text = row.text",
        {"rows": [{"id": sid, "text": text[:500]}]},
    )


def upsert_facets(db: HydraAdapter, keys: list[str]) -> None:
    rows = [{"id": facet_id(k), "key": k.strip().lower()} for k in sorted(set(keys))]
    for chunk in _chunks(rows):
        db.mutate(
            "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Facet, n.key = row.key",
            {"rows": chunk},
        )


def upsert_fix(db: HydraAdapter, fix: Fix) -> int:
    db.mutate(
        "UNWIND $rows AS row MERGE (n {id: row.id}) "
        "SET n:Fix, n.action = row.action, n.kind = row.kind, n.title = row.title",
        {"rows": [{"id": fix.id, "action": fix.action, "kind": fix.kind,
                   "title": fix.title or fix.action[:80]}]},
    )
    return fix.id


def record_episode_nodes(db: HydraAdapter, ep: Episode) -> None:
    db.mutate(
        "UNWIND $rows AS row MERGE (n {id: row.id}) "
        "SET n:Episode, n.ts = row.ts, n.seq = row.seq, n.agent = row.agent",
        {"rows": [{"id": ep.id, "ts": ep.ts, "seq": ep.seq, "agent": ep.agent}]},
    )


def record_episode_edges(db: HydraAdapter, ep: Episode) -> None:
    edges = [
        ("HIT", [{"src": ep.id, "dst": ep.signature_id}]),
        ("IN", [{"src": ep.id, "dst": facet_id(k)} for k in sorted(set(ep.facets))]),
        ("TRIED_OK" if ep.outcome == "ok" else "TRIED_FAIL",
         [{"src": ep.id, "dst": ep.fix_id}]),
    ]
    for etype, rows in edges:
        db.mutate(
            f"UNWIND $rows AS row CREATE (a {{id: row.src}})-[:{etype}]->(b {{id: row.dst}})",
            {"rows": rows},
        )


def record_episode(db: HydraAdapter, ep: Episode, sig_text: str) -> None:
    """Append one episode node + its edges. Idempotent on ep.id."""
    upsert_signature(db, ep.signature_id, sig_text)
    upsert_facets(db, ep.facets)
    record_episode_nodes(db, ep)
    record_episode_edges(db, ep)


def link_fixes(db: HydraAdapter, src_fix: int, dst_fix: int, etype: str) -> None:
    assert etype in ("REQUIRES", "SUPERSEDED_BY")
    db.mutate(f"MERGE (a {{id: $s}})-[:{etype}]->(b {{id: $d}})",
              {"s": src_fix, "d": dst_fix})


def link_signatures(db: HydraAdapter, src_sig: int, dst_sig: int) -> None:
    db.mutate("MERGE (a {id: $s})-[:LEADS_TO]->(b {id: $d})",
              {"s": src_sig, "d": dst_sig})


def write_confirmed_edge(db: HydraAdapter, sig: int, fix: int,
                         facet: str, ok: float, fail: float) -> None:
    """Consolidation summary edge. MERGE dedups (src,dst,type); the
    properties then get refreshed by MATCH…SET (MERGE can't take SET)."""
    db.mutate("MERGE (a {id: $s})-[:CONFIRMED_FOR]->(b {id: $d})",
              {"s": sig, "d": fix})
    db.mutate(
        "MATCH (a {id: $s})-[r:CONFIRMED_FOR]->(b {id: $d}) "
        "SET r.facet = $f, r.ok = $o, r.fail = $n",
        {"s": sig, "d": fix, "f": facet, "o": ok, "n": fail},
    )
