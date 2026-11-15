# Fixgraph

Shared memory for coding agents, stored as a graph in HydraDB. HydraDB
Hackathon entry — track 1: *"a graph that improves itself, sharper every run."*

Coding agents hit the same environment and tooling failures again and again:
a package manager refusing to build, a module system mismatch, a removed
standard library module. Every session rediscovers the fix from scratch.
Fixgraph records every failure, the environment it happened in, each fix
that was tried, and whether it worked — so the next agent that hits the
failure gets the fix that has *actually worked in an environment like its
own*. Every lookup is followed by an outcome report, and the outcome
changes what the next lookup returns. Sharper every run.

## Why a graph and not a vector store

The same error text often needs a different fix depending on the toolchain
version. Two records with identical text and opposite outcomes are
indistinguishable to similarity search. Fixgraph answers by traversing:

```
(Signature)<-[:HIT]-(Episode)-[:IN]->(Facet)      which environments saw it
(Episode)-[:TRIED_OK]->(Fix)   /   -[:TRIED_FAIL]->(Fix)
(Fix)-[:REQUIRES]->(Fix)   (Fix)-[:SUPERSEDED_BY]->(Fix)
(Signature)-[:LEADS_TO]->(Signature)
(Signature)-[:CONFIRMED_FOR]->(Fix)                consolidation summary
```

Episodes are append-only and stay the source of truth — HydraDB has no
transactions, so there are no read-modify-write counters to corrupt.

Lookup normalizes the error text to a signature (paths, versions, hashes,
line numbers stripped), traverses to episodes and fixes, weighs each episode
by how much of its environment the current one shares, decays outcomes over
the event sequence so a fix that starts failing drops out within a round or
two, and ranks by the lower bound of a Wilson interval so one lucky success
doesn't outrank ten-of-twelve. It returns ranked fixes, any REQUIRES chain
in order, and the evidence: how many times, in which environments, when last
confirmed. The evidence is the part a vector store cannot give.

## Reproduce

```bash
# needs: python 3.10+, a reachable HydraDB (see "Running HydraDB")
pip install -e .[bench,mcp,dev]
export FIXGRAPH_URL=http://<host>:8443 FIXGRAPH_TOKEN=<token>
python scripts/probe.py        # verify the node round-trips every shape we use
python -m fixgraph.bench --tier synthetic --seeds 3 --out results
# or: make probe && make bench
```

`results/results.json` + `results/chart.png` — one line per arm per metric:
share of scenarios fixed on the first try, mean attempts, mean context tokens
(counted with tiktoken `cl100k_base`).

## Running HydraDB

Built from the upstream Dockerfile (`--target runtime`, Ubuntu 24.04, Rust
1.99) and run with `deploy/docker-compose.yml`. The demo instance lives on a
VM at `http://158.180.57.167:8443` (Bolt on 7687) so it stays up for judging;
plaintext is enabled because it is a throwaway demo node — the bearer token
is still required on every request.

The cloud adapter (`HydraCloud`, BYOG `api.hydradb.com`) is an **untested
stub** — no API key was available. All queries stay inside the documented
shared dialect so they should run unchanged; smoke-test before relying on it.

## The benchmark

Four arms, same episode history, same candidate fixes, each writing to its
own memory after every attempt:

| arm | memory |
|---|---|
| no-memory | tries fixes in a fixed default order |
| vector | episodes embedded as text (`all-MiniLM-L6-v2`), top-10 nearest records |
| vector+env-filter | same, filtered to matching environment — the strongest fair baseline |
| fixgraph | this library |

One round = one pass over all live scenarios, shuffled with a fixed seed;
try fixes in returned order until the check passes or 4 attempts are used;
report every outcome. Five rounds. No LLM anywhere in the loop.

**Synthetic tier** (~250 scenarios): seeded generator with hidden rules
mapping (signature, environment) to the correct fix, including the hard
cases on purpose — identical error text needing different fixes by
environment, a fix that stops working mid-run, two-step chains, and
environments held out until late rounds. Labelled synthetic everywhere.

**Real tier**: real failures reproduced in Docker, each (scenario, fix)
pair executed once in a clean container and cached — replayed across rounds
and arms. See `src/fixgraph/bench/real_tier.py`, `bench/real_cache.json` and
`results/results_real.json`.

## Results

See `results/` and `results/index.html` for the chart, the per-slice numbers,
and one worked lookup with its evidence path and Cypher. Numbers are from a
real run; where the graph loses a slice, the page says so.

Headline (synthetic tier, 3 seeds, final round): first-try 0.89 for the
graph vs 0.86 for the strongest vector baseline, mean attempts 1.19 vs 1.33,
context 158 vs 640 tokens. The graph's edge is concentrated where structure
matters: the drift slice (a fix that stops working mid-run) and the chain
slice (a fix that only works after a prerequisite).

## Interfaces

- **Python**: `FixGraph().lookup(error_text, env)`, `.report(...)`,
  `.add_fix(...)`, `.consolidate()`
- **MCP server** (stdio): `python -m fixgraph.mcp_server` exposing
  `fixgraph_lookup`, `fixgraph_report`, `fixgraph_add_fix`,
  `fixgraph_explain` — drop into any MCP client (Claude Code, Cursor,
  Codex, Devin)
- **CLI**: `fixgraph run -- <command>` — on non-zero exit it fingerprints
  the environment, looks up the error, prints the best known fix + evidence

## Limits and known weaknesses

- REQUIRES edges are learned from observed order ("fix A only ever worked
  right after fix B was applied") — a heuristic that can over-fire on
  unusual failure sequences; it only ever reorders candidates, never hides
  them.
- Facet vocabulary is coarse on purpose (major versions, os, arch);
  environments differing only in patch level are invisible to it.
- The consolidation pass writes derived CONFIRMED_FOR edges — lookups still
  read the episode log, so a consolidation bug can't corrupt memory.
- The synthetic tier is synthetic: the hidden rules are hand-designed to
  contain the hard cases, and results there bound, not prove, real-world
  performance. That's what the Docker tier is for.

## Go-to-market

- **Buyer:** engineering teams running many coding-agent sessions and CI
  jobs, where the same environment failure is rediscovered every session.
- **Wedge:** one-minute install as an MCP server + the CLI wrapper; a
  per-team graph that is useful from the second occurrence of any failure.
- **Why it compounds:** every session adds outcome data that can't be
  rebuilt from documentation — the graph is worth more the longer a team
  uses it.
- **Expansion:** opt-in shared graph for open-source toolchains.
- **Pricing (proposal):** per-seat SaaS, free under a session threshold;
  self-hosted graph node free. Marked as a proposal, not market research.
- **Why HydraDB:** the lookup is a multi-hop, environment-conditioned
  traversal, and the episode log only grows — object-storage-backed cheap
  durable history is exactly the fit.
- **Evidence:** measured attempts and tokens saved per failure — see
  `results/`.
