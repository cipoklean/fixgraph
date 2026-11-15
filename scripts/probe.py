"""HydraDB probe: round-trips every query shape fixgraph uses.

Run BEFORE trusting the adapter:

    python scripts/probe.py            # against FIXGRAPH_URL/FIXGRAPH_TOKEN

Exercises: node batch upsert (label+props), dedup edge batch, single MERGE
edge, edge MERGE + MATCH..SET property update, anchored multi-hop MATCH with
multi-column RETURN, label-anchored scan, bounded var-length REQUIRES chain,
grouped aggregation, batched DETACH DELETE cleanup. Uses a reserved id range
(ids derived from "probe:" keys) and cleans up after itself.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fixgraph.adapter import from_env, QueryError  # noqa: E402
from fixgraph.signatures import node_id  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        detail = fn() or ""
        results.append((name, True, detail))
        print(f"  {PASS}  {name}  {detail}")
    except Exception as e:  # noqa: BLE001
        results.append((name, False, str(e)[:200]))
        print(f"  {FAIL}  {name}: {e}")


def main() -> int:
    db = from_env()
    stamp = str(int(time.time()))
    ids = {k: node_id("probe", f"{k}:{stamp}") for k in
           ("sig", "facet1", "facet2", "fix1", "fix2", "ep1", "ep2", "sig2")}

    print(f"probing {db.url if hasattr(db, 'url') else 'adapter'} …")

    check("node batch upsert (label+props)", lambda: db.mutate(
        "UNWIND $rows AS row MERGE (n {id: row.id}) "
        "SET n:Signature, n.text = row.text",
        {"rows": [{"id": ids["sig"], "text": "probe sig"},
                  {"id": ids["sig2"], "text": "probe sig 2"}]}))

    check("facet + fix + episode node batches", lambda: (
        db.mutate("UNWIND $rows AS row MERGE (n {id: row.id}) "
                  "SET n:Facet, n.key = row.key",
                  {"rows": [{"id": ids["facet1"], "key": "python@3.12"},
                            {"id": ids["facet2"], "key": "os:ubuntu-24.04"}]}),
        db.mutate("UNWIND $rows AS row MERGE (n {id: row.id}) "
                  "SET n:Fix, n.action = row.action, n.kind = row.kind, n.title = row.title",
                  {"rows": [{"id": ids["fix1"], "action": "pip --break-system-packages",
                             "kind": "command", "title": "use venv"},
                            {"id": ids["fix2"], "action": "apt install python3-venv",
                             "kind": "command", "title": "venv pkg"}]}),
        db.mutate("UNWIND $rows AS row MERGE (n {id: row.id}) "
                  "SET n:Episode, n.ts = row.ts, n.agent = row.agent",
                  {"rows": [{"id": ids["ep1"], "ts": 1.0, "agent": "probe"},
                            {"id": ids["ep2"], "ts": 2.0, "agent": "probe"}]}),
        "ok")[-1] or "ok")

    check("edge batch dedup (run twice, expect 1 row)", lambda: (
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:HIT]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep1"], "dst": ids["sig"]},
                            {"src": ids["ep2"], "dst": ids["sig"]}]}),
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:HIT]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep1"], "dst": ids["sig"]},
                            {"src": ids["ep2"], "dst": ids["sig"]}]}),
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:IN]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep1"], "dst": ids["facet1"]},
                            {"src": ids["ep1"], "dst": ids["facet2"]},
                            {"src": ids["ep2"], "dst": ids["facet1"]}]}),
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:TRIED_OK]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep1"], "dst": ids["fix1"]}]}),
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:TRIED_FAIL]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep2"], "dst": ids["fix1"]}]}),
        (lambda rows: (_ for _ in ()).throw(AssertionError(f"HIT edges={len(rows)} expected 2"))
         if len(rows) != 2 else f"HIT edges={len(rows)}")(
            db.query("MATCH (e:Episode)-[:HIT]->(s:Signature {id: $id}) "
                     "RETURN e.id AS eid", {"id": ids["sig"]}))))

    check("single MERGE edge + edge property SET", lambda: (
        db.mutate("MERGE (a {id: $s})-[:REQUIRES]->(b {id: $d})",
                  {"s": ids["fix2"], "d": ids["fix1"]}),
        db.mutate("MERGE (a {id: $s})-[:CONFIRMED_FOR]->(b {id: $d})",
                  {"s": ids["sig"], "d": ids["fix1"]}),
        db.mutate("MATCH (a {id: $s})-[r:CONFIRMED_FOR]->(b {id: $d}) "
                  "SET r.facet = $f, r.ok = $o", 
                  {"s": ids["sig"], "d": ids["fix1"], "f": "python@3.12", "o": 3}),
        "ok")[-1])

    def read_shape():
        rows = db.query(
            "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:IN]->(f:Facet) "
            "RETURN e.id AS eid, e.ts AS ts, f.key AS fkey", {"sig": ids["sig"]})
        by_ep = {}
        for r in rows:
            by_ep.setdefault(r["eid"], set()).add(r["fkey"])
        assert by_ep[ids["ep1"]] == {"python@3.12", "os:ubuntu-24.04"}, by_ep
        assert by_ep[ids["ep2"]] == {"python@3.12"}, by_ep
        return f"{len(rows)} rows, 2 episodes"

    check("lookup read shape (episodes x facets)", read_shape)

    def outcome_shape():
        ok = db.query(
            "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:TRIED_OK]->(x:Fix) "
            "RETURN e.id AS eid, x.id AS fid, x.action AS action", {"sig": ids["sig"]})
        fail = db.query(
            "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:TRIED_FAIL]->(x:Fix) "
            "RETURN e.id AS eid, x.id AS fid", {"sig": ids["sig"]})
        assert ok and ok[0]["fid"] == ids["fix1"], ok
        assert fail and fail[0]["eid"] == ids["ep2"], fail
        return f"ok={len(ok)} fail={len(fail)}"

    check("outcome read shape (TRIED_OK / TRIED_FAIL)", outcome_shape)

    def varlen():
        rows = db.query(
            "MATCH (x:Fix {id: $id})-[:REQUIRES*1..4]->(r:Fix) RETURN r.id AS rid",
            {"id": ids["fix2"]})
        assert rows and rows[0]["rid"] == ids["fix1"], rows
        return f"chain={[r['rid'] == ids['fix1'] for r in rows]}"

    check("bounded var-length REQUIRES chain", varlen)

    check("label-anchored scan + grouped agg", lambda: (
        (lambda rows: f"signature count rows={len(rows)}")(
            db.query("MATCH (s:Signature)<-[:HIT]-(e:Episode) "
                     "RETURN s.id AS sid, count(*) AS n"))))

    def idempotent_episode():
        # re-running the same report must not duplicate edges
        db.mutate("UNWIND $rows AS row CREATE (a {id: row.src})-[:TRIED_OK]->(b {id: row.dst})",
                  {"rows": [{"src": ids["ep1"], "dst": ids["fix1"]}]})
        rows = db.query(
            "MATCH (s:Signature {id: $sig})<-[:HIT]-(e:Episode)-[:TRIED_OK]->(x:Fix) "
            "RETURN e.id AS eid", {"sig": ids["sig"]})
        assert len(rows) == 1, f"expected 1 ok edge, got {len(rows)}"
        return "re-write deduped"

    check("report idempotency (same edge re-written)", idempotent_episode)

    def cleanup():
        all_ids = list(ids.values())
        for i in range(0, len(all_ids), 1000):
            db.mutate("UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n",
                      {"rows": [{"id": x} for x in all_ids[i:i + 1000]]})
        left = db.query("MATCH (s:Signature {id: $id}) RETURN s.id AS id",
                        {"id": ids["sig"]})
        assert not left, left
        return "id range cleared"

    check("cleanup (batched DETACH DELETE)", cleanup)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
