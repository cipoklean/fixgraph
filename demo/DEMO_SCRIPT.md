# Demo script (target < 3 minutes)

1. **The failure (20s).** Terminal: `pip install requests` on Ubuntu 24.04 →
   `externally-managed-environment`. Then `fixgraph run -- pip install requests`
   → prints the ranked fix with evidence ("3 episodes, ok×2 on python@3.12,
   last confirmed today").
2. **The graph working (40s).** `fixgraph lookup "Error [ERR_REQUIRE_ESM]..."`
   on node@18 → returns "pin to CJS release" first. Change the env line to
   node@22 → same error text, returns "dynamic import()" first. Say the line:
   *"identical text, opposite fix — a vector store cannot tell these apart."*
3. **The curve (40s).** results/index.html — chart of 4 arms. Point at:
   fixgraph's first-try climbing while vector+env-filter flatlines on the
   drift slice (stale fix keeps winning similarity votes), the chain slice
   only fixgraph solves, and the context-token gap (~120 vs ~600).
4. **The memory (30s).** `python scripts/seed_demo.py` fresh, then
   `fixgraph_explain` showing the traversal + Cypher.
5. **Close (10s).** GTM: per-team shared memory, outcome data compounds,
   can't be rebuilt from docs.
