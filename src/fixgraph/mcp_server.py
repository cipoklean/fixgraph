"""MCP server (stdio) exposing fixgraph to coding agents.

Tools:
  fixgraph_lookup   — call on ANY command/toolchain failure BEFORE trying
                      fixes; returns ranked fixes with evidence.
  fixgraph_report   — ALWAYS call after an attempt, whether it worked or not;
                      the outcome is what the next lookup ranks on.
  fixgraph_add_fix  — register a new fix (optionally requires/supersedes).
  fixgraph_explain  — show the evidence path and Cypher behind the last
                      lookup for a signature.

Works with Claude Code, Cursor, Codex, Devin — any MCP client:

    "mcpServers": {"fixgraph": {
      "command": "python", "args": ["-m", "fixgraph.mcp_server"],
      "env": {"FIXGRAPH_URL": "...", "FIXGRAPH_TOKEN": "..."}}}
"""

from __future__ import annotations

import json

from .api import FixGraph
from .envdetect import fingerprint
from .lookup import explain_cypher


def _fg() -> FixGraph:
    if not hasattr(_fg, "_inst"):
        _fg._inst = FixGraph()
    return _fg._inst


def handle_lookup(error_text: str, env: list[str] | None = None) -> str:
    res = _fg().lookup(error_text, env or fingerprint())
    out = {
        "seen": res.seen,
        "episodes": res.episode_count,
        "signature": res.signature_text,
        "fixes": [
            {"action": c.action, "kind": c.kind, "score": round(c.score, 3),
             "ok_weight": round(c.ok_w, 2), "fail_weight": round(c.fail_w, 2),
             "n_episodes": c.n_episodes, "requires": c.requires,
             "shared_facets": c.shared_facets}
            for c in res.candidates[:5]
        ],
        "deciding_facets": res.deciding_facets,
        "next_signatures": [t for _, t in res.next_signatures],
    }
    if not res.seen:
        out["hint"] = ("No memory of this failure. Whatever you try, call "
                       "fixgraph_report with the outcome afterwards.")
    return json.dumps(out, indent=2)


def handle_report(error_text: str, fix_action: str, outcome: str,
                  env: list[str] | None = None) -> str:
    ep = _fg().report(error_text, env or fingerprint(), fix_action, outcome)
    return json.dumps({"recorded": ep, "outcome": outcome})


def handle_add_fix(action: str, kind: str = "command", title: str = "",
                   requires: list[str] | None = None,
                   supersedes: list[str] | None = None) -> str:
    fid = _fg().add_fix(action, kind=kind, title=title,
                        requires=requires, supersedes=supersedes)
    return json.dumps({"fix_id": fid})


def handle_explain(error_text: str) -> str:
    res = _fg().lookup(error_text, fingerprint())
    return json.dumps({
        "signature": res.signature_text,
        "signature_id": res.signature_id,
        "cypher": explain_cypher(),
        "episodes": res.episode_count,
        "top_evidence": res.context_text(),
    }, indent=2)


def main():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fixgraph")

    @mcp.tool(description=(
        "Look up fixes for a command/toolchain failure in the shared fix "
        "graph. Call this on ANY non-zero exit, traceback, or toolchain "
        "error BEFORE attempting your own fix — another agent may already "
        "have solved this exact failure in an environment like yours. "
        "Pass the raw error text (stderr tail) verbatim."))
    def fixgraph_lookup(error_text: str, env: list[str] | None = None) -> str:
        return handle_lookup(error_text, env)

    @mcp.tool(description=(
        "Record the outcome of a fix attempt. ALWAYS call this after trying "
        "a fix, whether it worked or not — the ranking the next agent gets "
        "depends on your report. outcome: 'ok' or 'fail'."))
    def fixgraph_report(error_text: str, fix_action: str, outcome: str,
                        env: list[str] | None = None) -> str:
        return handle_report(error_text, fix_action, outcome, env)

    @mcp.tool(description=(
        "Register a fix you discovered so other agents can find it. "
        "requires: fixes that must be applied first; supersedes: older "
        "fixes this one replaces."))
    def fixgraph_add_fix(action: str, kind: str = "command", title: str = "",
                         requires: list[str] | None = None,
                         supersedes: list[str] | None = None) -> str:
        return handle_add_fix(action, kind, title, requires, supersedes)

    @mcp.tool(description=(
        "Explain the evidence behind a lookup: episode counts, environments, "
        "last-confirmed times, and the exact Cypher the graph ran."))
    def fixgraph_explain(error_text: str) -> str:
        return handle_explain(error_text)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
