"""In-memory stand-in for HydraAdapter that interprets the exact query
shapes fixgraph uses. Lets the whole report/lookup/consolidate loop run
offline — used by unit tests and as a development fallback. NOT a general
Cypher engine; it pattern-matches our statements only.
"""

from __future__ import annotations

import re
from collections import defaultdict

from fixgraph.adapter import HydraAdapter, QueryError


class FakeDb:
    """nodes: id -> {label: {prop: val}}; edges: set of (src, type, dst, props)."""

    def __init__(self):
        self.nodes: dict[int, dict] = {}
        self.edges: dict[tuple[int, str, int], dict] = {}

    # --- write shapes -----------------------------------------------------
    def _node_batch(self, label, props_spec, rows):
        for row in rows:
            n = self.nodes.setdefault(row["id"], {"labels": set(), "props": {}})
            n["labels"].add(label)
            for prop in props_spec:
                if prop in row:
                    n["props"][prop] = row[prop]

    def _edge_batch(self, etype, rows):
        for row in rows:
            self.nodes.setdefault(row["src"], {"labels": set(), "props": {}})
            self.nodes.setdefault(row["dst"], {"labels": set(), "props": {}})
            self.edges.setdefault((row["src"], etype, row["dst"]), {})

    def mutate(self, cypher, parameters=None):
        p = parameters or {}
        m = re.search(r"UNWIND \$rows AS row MERGE \(n \{id: row\.id\}\) "
                      r"SET n:(\w+)((?:, n\.\w+ = row\.\w+)*)", cypher)
        if m:
            props = re.findall(r"n\.(\w+) = row\.(\w+)", m.group(2))
            # map n.prop = row.prop
            for row in p["rows"]:
                n = self.nodes.setdefault(row["id"], {"labels": set(), "props": {}})
                n["labels"].add(m.group(1))
                for nprop, rprop in props:
                    if rprop in row:
                        n["props"][nprop] = row[rprop]
            return
        m = re.search(r"UNWIND \$rows AS row CREATE \(a \{id: row\.src\}\)"
                      r"-\[:(\w+)\]->\(b \{id: row\.dst\}\)", cypher)
        if m:
            self._edge_batch(m.group(1), p["rows"])
            return
        m = re.search(r"UNWIND \$rows AS row MATCH \(n \{id: row\.id\}\) "
                      r"DETACH DELETE n", cypher)
        if m:
            for row in p["rows"]:
                self.nodes.pop(row["id"], None)
                for k in [k for k in self.edges
                          if k[0] == row["id"] or k[2] == row["id"]]:
                    del self.edges[k]
            return
        m = re.search(r"MERGE \(a \{id: \$s\}\)-\[:(\w+)\]->\(b \{id: \$d\}\)",
                      cypher)
        if m:
            self._edge_batch(m.group(1), [{"src": p["s"], "dst": p["d"]}])
            return
        m = re.search(r"MATCH \(a \{id: \$s\}\)-\[r:(\w+)\]->\(b \{id: \$d\}\) "
                      r"SET (.*)", cypher)
        if m:
            key = (p["s"], m.group(1), p["d"])
            if key not in self.edges:
                raise QueryError(f"edge {key} not found")
            assigns = re.findall(r"r\.(\w+) = \$(\w+)", m.group(2))
            for rprop, pkey in assigns:
                self.edges[key][rprop] = p[pkey]
            return
        raise QueryError(f"FakeDb cannot interpret mutation: {cypher[:80]}")

    # --- read shapes ------------------------------------------------------
    def query(self, cypher, parameters=None):
        p = parameters or {}
        c = " ".join(cypher.split())

        if re.match(r"MATCH \(n:(\w+)\) RETURN n\.id AS id LIMIT", c):
            label = re.match(r"MATCH \(n:(\w+)\)", c).group(1)
            return [{"id": i} for i, n in self.nodes.items()
                    if label in n["labels"]][:1000]

        if c == "MATCH (s:Signature) RETURN s.id AS sid":
            return [{"sid": i} for i, n in self.nodes.items()
                    if "Signature" in n["labels"]]

        if re.match(r"MATCH \(s:Signature \{id: \$sig\}\)<-\[:HIT\]-"
                    r"\(e:Episode\)-\[:IN\]->\(f:Facet\)", c):
            out = []
            for (src, t, dst), _ in self.edges.items():
                if t != "HIT" or dst != p["sig"]:
                    continue
                e = self.nodes[src]
                for (s2, t2, d2), _ in self.edges.items():
                    if t2 == "IN" and s2 == src:
                        fk = self.nodes[d2]["props"].get("key")
                        out.append({"eid": src, "ts": e["props"].get("ts"),
                                    "seq": e["props"].get("seq"),
                                    "fkey": fk})
            return out

        m = re.match(r"MATCH \(s:Signature \{id: \$sig\}\)<-\[:HIT\]-"
                     r"\(e:Episode\)-\[:(TRIED_OK|TRIED_FAIL)\]->\(x:Fix\)", c)
        if m:
            et = m.group(1)
            out = []
            for (src, t, dst), _ in self.edges.items():
                if t != "HIT" or dst != p["sig"]:
                    continue
                for (s2, t2, d2), _ in self.edges.items():
                    if t2 == et and s2 == src:
                        x = self.nodes[d2]["props"]
                        out.append({"eid": src, "fid": d2,
                                    "action": x.get("action"),
                                    "kind": x.get("kind"),
                                    "title": x.get("title")})
            return out

        if c.startswith("MATCH (x:Fix)-[:SUPERSEDED_BY]->(n:Fix)"):
            return [{"src": s, "dst": d} for (s, t, d) in self.edges
                    if t == "SUPERSEDED_BY"]

        m = re.match(r"MATCH \(x:Fix \{id: \$id\}\)-\[:REQUIRES\*1\.\.(\d+)\]"
                     r"->\(r:Fix\) RETURN r\.id AS rid", c)
        if m:
            maxd = int(m.group(1))
            seen, frontier, out = set(), [p["id"]], []
            for _ in range(maxd):
                nxt = []
                for f in frontier:
                    for (s, t, d) in self.edges:
                        if t == "REQUIRES" and s == f and d not in seen:
                            seen.add(d)
                            out.append({"rid": d})
                            nxt.append(d)
                frontier = nxt
            return out

        m = re.match(r"MATCH \(s:Signature \{id: \$sig\}\)-\[:LEADS_TO\*1\.\.(\d+)\]"
                     r"->\(n:Signature\)", c)
        if m:
            maxd = int(m.group(1))
            seen, frontier, out = set(), [p["sig"]], []
            for _ in range(maxd):
                nxt = []
                for f in frontier:
                    for (s, t, d) in self.edges:
                        if t == "LEADS_TO" and s == f and d not in seen:
                            seen.add(d)
                            nxt.append(d)
                            out.append({"nid": d,
                                        "text": self.nodes[d]["props"].get("text")})
                frontier = nxt
            return out

        if c == "MATCH (s:Signature {id: $id}) RETURN s.id AS id":
            return [{"id": p["id"]}] if p["id"] in self.nodes else []

        m = re.match(r"MATCH \(s:Signature\)<-\[:HIT\]-\(e:Episode\) "
                     r"RETURN s\.id AS sid, count\(\*\) AS n", c)
        if m:
            counts = defaultdict(int)
            for (s, t, d) in self.edges:
                if t == "HIT":
                    counts[d] += 1
            return [{"sid": k, "n": v} for k, v in counts.items()]

        raise QueryError(f"FakeDb cannot interpret query: {c[:90]}")
