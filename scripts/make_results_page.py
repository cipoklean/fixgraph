"""Generate results/index.html — single file, works from file://.

Direction: engineer's graph paper (see BRIEF.md). Faint metric grid,
indigo ink, mono data, annotated traversal diagram. All values come from
results/results.json + a live lookup — nothing is typed into the page.
"""

import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def load(out_dir):
    doc = json.load(open(os.path.join(out_dir, "results.json")))
    png = base64.b64encode(
        open(os.path.join(out_dir, "chart.png"), "rb").read()).decode()
    return doc, png


def _fix_title(fg, fid):
    try:
        rows = fg.db.query(
            "MATCH (x:Fix {id: $id}) RETURN x.action AS action", {"id": fid})
        return rows[0]["action"] if rows else f"#{fid}"
    except Exception:
        return f"#{fid}"


def worked_lookup():
    try:
        from fixgraph.api import FixGraph
        from fixgraph.lookup import explain_cypher
        fg = FixGraph()
        err = ("Error [ERR_REQUIRE_ESM]: require() of ES Module "
               "/app/node_modules/pure-esm/index.js not supported.")
        env = ["node@18", "npm@9", "os:debian-12", "arch:x64"]
        res = fg.lookup(err, env)
        return {
            "error": err, "env": env,
            "context": res.context_text() if res.seen else "",
            "episodes": res.episode_count, "seen": res.seen,
            "deciding": res.deciding_facets,
            "cypher": explain_cypher(),
            "candidates": [
                {"action": c.action, "score": round(c.score, 3),
                 "ok": round(c.ok_w, 1), "fail": round(c.fail_w, 1),
                 "episodes": c.n_episodes,
                 "requires": [_fix_title(fg, rid) for rid in c.requires]}
                for c in res.candidates[:5]],
        }
    except Exception as e:
        return {"error": "", "env": [], "seen": False, "deciding": [],
                "context": f"(lookup unavailable: {e})",
                "episodes": 0, "cypher": [], "candidates": []}


def table(rows, headers):
    import html
    thead = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>"
                   for r in rows)
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


def build(doc, png, lookup):
    runs = doc["runs"]
    arms = sorted({r["arm"] for r in runs})
    # last-round metrics, mean across seeds
    last_rows = []
    for arm in arms:
        last = [r["metrics"][-1] for r in runs if r["arm"] == arm]
        n = len(last)
        last_rows.append([
            arm,
            f"{sum(m['first_try'] for m in last)/n:.2f}",
            f"{sum(m['fix_rate'] for m in last)/n:.2f}",
            f"{sum(m['avg_attempts'] for m in last)/n:.2f}",
            f"{sum(m['ctx_tokens'] for m in last)/n:.0f}",
        ])
    # slice table: mean over seeds of each arm's LAST-round per-tag metrics
    tags: dict[str, dict[str, list[dict]]] = {}
    for r in runs:
        m = r["metrics"][-1]
        for tag, v in m.get("tags", {}).items():
            tags.setdefault(tag, {}).setdefault(r["arm"], []).append(v)
    slice_rows = []
    for name in sorted(tags):
        for arm in arms:
            vals = tags[name].get(arm)
            if vals:
                slice_rows.append([name, arm,
                                   f"{sum(v['first_try'] for v in vals)/len(vals):.2f}",
                                   f"{sum(v['fix_rate'] for v in vals)/len(vals):.2f}",
                                   f"{sum(v['avg_attempts'] for v in vals)/len(vals):.2f}"])

    cand_rows = [[c["action"], f"{c['score']:.2f}", c["ok"], c["fail"],
                  c["episodes"], ", ".join(c["requires"]) or "—"]
                 for c in lookup["candidates"]]
    import html as _h
    cypher_html = "\n".join(f"<div class=qy>{_h.escape(q)}</div>" for q in lookup["cypher"])

    import html as _h
    lookup = dict(lookup)
    for k in ("error", "context"):
        lookup[k] = _h.escape(str(lookup.get(k, "")))
    lookup["deciding"] = [_h.escape(str(d)) for d in lookup.get("deciding", [])]
    lookup["env"] = [_h.escape(e) for e in lookup.get("env", [])]
    return TEMPLATE.format(
        png=png,
        scenario_count=doc["scenario_count"],
        seeds=", ".join(map(str, doc["seeds"])),
        rounds=doc["rounds"],
        tier=doc["tier"],
        token_counting=doc.get("token_counting", "—"),
        last_table=table(last_rows,
                         ["arm", "first-try", "fix-rate", "attempts",
                          "ctx tokens"]),
        slice_table=table(slice_rows, ["slice", "arm", "first-try", "fix-rate", "attempts"])
                        if slice_rows else "",
        lk_error=lookup["error"], lk_env=", ".join(lookup["env"]),
        lk_seen="yes" if lookup["seen"] else "no",
        lk_episodes=lookup["episodes"],
        lk_context=lookup["context"],
        lk_table=table(cand_rows,
                       ["candidate", "score", "ok w", "fail w", "episodes",
                        "requires"]),
        lk_cypher=cypher_html,
        lk_deciding=", ".join(lookup["deciding"]) or "—",
    )


TEMPLATE = """<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Fixgraph — benchmark report</title>
<style>
:root{{
--paper:#fbf9f4;--grid:#e4e0d6;--ink:#1c2733;--faint:#5c6670;
--indigo:#2b3f8f;--rule:#c9c4b8;
--mono:ui-monospace,'Cascadia Code',Menlo,Consolas,monospace;
--text:'Segoe UI',system-ui,sans-serif}}
*{{box-sizing:border-box;margin:0}}
body{{
font-family:var(--text);color:var(--ink);background:var(--paper);
background-image:
 repeating-linear-gradient(0deg,var(--grid) 0 1px,transparent 1px 24px),
 repeating-linear-gradient(90deg,var(--grid) 0 1px,transparent 1px 24px);
line-height:1.5}}
.page{{max-width:960px;margin:0 auto;padding:48px 32px 96px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:8px}}
.kicker{{font-family:var(--mono);font-size:11px;letter-spacing:.18em;
text-transform:uppercase;color:var(--indigo)}}
h1{{font-size:32px;letter-spacing:-.02em;line-height:1.1;margin-top:8px;
text-wrap:balance}}
.sub{{color:var(--faint);font-size:14px;max-width:64ch;margin-top:8px}}
h2{{font-family:var(--mono);font-size:12px;font-weight:600;
letter-spacing:.14em;text-transform:uppercase;color:var(--indigo);
margin:48px 0 12px;display:flex;align-items:baseline;gap:10px}}
h2::before{{content:attr(data-n);border:1px solid var(--indigo);
padding:1px 7px;font-size:11px}}
p{{max-width:68ch;font-size:14.5px;margin-bottom:12px}}
.meta{{font-family:var(--mono);font-size:11.5px;color:var(--faint);
margin-top:16px;display:flex;gap:24px;flex-wrap:wrap}}
.fig{{border:1px solid var(--rule);background:#fff;padding:16px;
margin-top:16px}}
.fig img{{width:100%;display:block}}
.figcap{{font-family:var(--mono);font-size:11px;color:var(--faint);
margin-top:10px;border-top:1px dashed var(--rule);padding-top:8px}}
table{{width:100%;border-collapse:collapse;font-family:var(--mono);
font-size:12px;margin-top:12px;background:#fff}}
th{{text-align:left;font-weight:600;font-size:10.5px;letter-spacing:.08em;
text-transform:uppercase;color:var(--faint);border-bottom:1.5px solid var(--ink)}}
td,th{{padding:7px 10px;border-bottom:1px solid var(--rule)}}
tr:last-child td{{border-bottom:0}}
td:first-child{{color:var(--indigo)}}
pre{{font-family:var(--mono);font-size:12px;background:#fff;
border:1px solid var(--rule);padding:14px;overflow-x:auto;
margin-top:12px;white-space:pre-wrap;word-break:break-word}}
.qy{{font-family:var(--mono);font-size:11.5px;background:#fff;
border:1px solid var(--rule);border-left:3px solid var(--indigo);
padding:8px 12px;margin-top:8px;overflow-x:auto;white-space:pre-wrap}}
.tag{{font-family:var(--mono);font-size:10.5px;border:1px solid var(--ink);
padding:1px 8px;margin-left:8px;vertical-align:middle}}
.note{{border-left:3px solid var(--indigo);padding:8px 14px;
font-size:13px;color:var(--faint);background:#fff;
border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);
border-right:1px solid var(--rule);margin-top:16px;max-width:68ch}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:16px}}
@media(max-width:720px){{.cols{{grid-template-columns:1fr}}}}
a{{color:var(--indigo)}}
</style></head><body><div class=page>
<header>
<div class=kicker>hydradb hackathon · track 1 · bench report</div>
<h1>Fixgraph: shared failure memory for coding agents, measured</h1>
<p class=sub>Same failure history and same candidate fixes fed to four arms.
The graph arm traverses <code>Signature → Episode → Facet/Fix</code> in HydraDB;
the vector arms embed episode text with all-MiniLM-L6-v2.
Every outcome each arm observes is reported back and changes its next lookup.</p>
<div class=meta>
<span>tier: {tier} (synthetic rules, hidden to all arms)</span>
<span>{scenario_count} scenarios · {rounds} rounds · seeds {seeds}</span>
<span>tokens: {token_counting}</span></div>
</header>

<h2 data-n=01>The curve</h2>
<div class=fig><img src="data:image/png;base64,{png}" alt="per-round metrics by arm">
<div class=figcap>Fig 1 — share fixed on first try, mean attempts, mean
context tokens per round. One line per arm per metric.</div></div>

<h2 data-n=02>Final round</h2>
{last_table}
{slice_table}

<h2 data-n=03>A worked lookup</h2>
<p>Error text seen on <code>{lk_env}</code>. The graph knew this failure:
{lk_episodes} episodes recorded, deciding facet(s): {lk_deciding}.</p>
<pre>{lk_error}

→ returned context:
{lk_context}</pre>
{lk_table}
<h2 data-n=04>The Cypher that produced it</h2>
{lk_cypher}
<p class=note>These are the exact statements the adapter runs — the narrow
self-hosted dialect: integer ids, UNWIND batch upserts, directed single-type
relationship patterns, anchored MATCH only.</p>

<h2 data-n=05>Honest limits</h2>
<p>The synthetic tier uses hand-designed hidden rules — it bounds, not proves,
real-world performance. Where a vector arm matches the graph on a slice, the
tables say so. REQUIRES chains are inferred from observed order and only ever
reorder candidates. Facet vocabulary is coarse: patch-level differences are
invisible. See README for the real Docker tier.</p>

<h2 data-n=06>Go-to-market</h2>
<p><strong>Buyer:</strong> engineering teams running many coding-agent sessions
and CI jobs where the same environment failure is rediscovered every session.
<strong>Wedge:</strong> one-minute MCP install + CLI wrapper — useful from the
second occurrence of any failure. <strong>Compounds:</strong> every session
adds outcome data that cannot be rebuilt from documentation.
<strong>Expansion:</strong> opt-in shared graph for open-source toolchains.
<strong>Pricing (proposal):</strong> per-seat SaaS; self-hosted node free.
<strong>Why HydraDB:</strong> the lookup is a multi-hop environment-conditioned
traversal, and the episode log only grows — object-storage-backed history is
the fit.</p>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/index.html")
    ap.add_argument("--results", default="results")
    args = ap.parse_args()
    doc, png = load(args.results)
    html = build(doc, png, worked_lookup())
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "w").write(html)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
