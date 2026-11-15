"""Benchmark harness. No LLM inside the loop — fully deterministic.

One round = one pass over all live scenarios in a shuffled order (seeded).
For each scenario the arm looks up once, then tries fixes in the returned
order until the oracle passes or MAX_ATTEMPTS are used. Every outcome is
reported. Arms may consolidate between rounds.

Scenario oracles hold per-instance state (chains, drift), so each arm gets a
fresh `generate(seed)` — deterministic, so identical instances, separate state.

Metrics per arm per round:
  first_try  — share of scenarios fixed on the first attempt
  attempts   — mean attempts used (unfixed scenarios count MAX_ATTEMPTS)
  fix_rate   — share of scenarios fixed at all
  tokens     — mean context size returned by lookup, counted with
               tiktoken cl100k_base (see results/README for the caveat)
"""

from __future__ import annotations

import json
import os
import random
import time

from .arms import Arm, NoMemoryArm, VectorArm, VectorFilteredArm, FixGraphArm
from .scenarios import Scenario
from . import synthetic

MAX_ATTEMPTS = 4
ROUNDS = 5


def count_tokens(text: str, enc=None) -> int:
    if not text:
        return 0
    if enc is None:
        try:
            import tiktoken
            enc = count_tokens._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return len(text.split())  # fallback: whitespace tokens, stated as such
    return len(enc.encode(text))


count_tokens._enc = None


def reset_graph(db) -> None:
    """Wipe every fixgraph node. Label-anchored DETACH DELETE; episodes
    first so dangling-edge bookkeeping stays simple."""
    for label in ("Episode", "Facet", "Fix", "Signature"):
        while True:
            rows = db.query(
                f"MATCH (n:{label}) RETURN n.id AS id LIMIT 1000")
            if not rows:
                break
            db.mutate(
                "UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n",
                {"rows": [{"id": r["id"]} for r in rows]},
            )


def run_arm(arm: Arm, scenarios: list[Scenario], rounds: int,
            seed: int, enc=None, log=print) -> list[dict]:
    metrics = []
    for rnd in range(rounds):
        live = [s for s in scenarios if s.first_round <= rnd]
        order = list(range(len(live)))
        random.Random(seed * 1000 + rnd).shuffle(order)

        n = first = fixed = 0
        attempts_sum = tok_sum = 0
        tag_stats: dict[str, dict[str, int]] = {}

        for oi in order:
            sc = live[oi]
            sc.state["round"] = rnd
            ranked, ctx = arm.lookup(sc)
            tok_sum += count_tokens(ctx, enc)
            session: list[int] = []
            solved = False
            tries = 0
            for fi in ranked:
                if tries >= MAX_ATTEMPTS:
                    break
                tries += 1
                session.append(fi)
                ok = bool(sc.check(fi))
                arm.report(sc, fi, "ok" if ok else "fail", session)
                if ok:
                    solved = True
                    break
            n += 1
            attempts_sum += tries
            if solved:
                fixed += 1
                if tries == 1:
                    first += 1
            for tag in sc.tags:
                st = tag_stats.setdefault(tag, dict(n=0, first=0, fixed=0, att=0))
                st["n"] += 1
                st["att"] += tries
                st["fixed"] += int(solved)
                st["first"] += int(solved and tries == 1)

        arm.consolidate()
        m = {
            "round": rnd,
            "scenarios": n,
            "first_try": first / n if n else 0.0,
            "avg_attempts": attempts_sum / n if n else 0.0,
            "fix_rate": fixed / n if n else 0.0,
            "ctx_tokens": tok_sum / n if n else 0.0,
            "tags": {t: {"n": s["n"],
                         "first_try": s["first"] / s["n"],
                         "fix_rate": s["fixed"] / s["n"],
                         "avg_attempts": s["att"] / s["n"]}
                     for t, s in tag_stats.items()},
        }
        metrics.append(m)
        log(f"    round {rnd}: first_try={m['first_try']:.2f} "
            f"attempts={m['avg_attempts']:.2f} tokens={m['ctx_tokens']:.0f}")
    return metrics


def run_benchmark(fg=None, seeds=(0, 1, 2), rounds: int = ROUNDS,
                  reps: int = 3, out_dir: str = "results",
                  log=print) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    enc = None
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        token_note = "tiktoken cl100k_base"
    except Exception:
        token_note = "whitespace-split fallback (tiktoken unavailable)"

    all_runs = []
    for seed in seeds:
        log(f"seed {seed}")
        arms: list[Arm] = [NoMemoryArm(), VectorArm(), VectorFilteredArm()]
        if fg is not None:
            # Fresh FixGraph per seed: reset_graph deletes every node, and a
            # reused instance would skip re-upserting them because its
            # _upserted set still holds the old ids — leaving nodes unlabelled
            # and invisible to the label-anchored lookups.
            from ..api import FixGraph
            seed_fg = FixGraph(db=fg.db)
            reset_graph(seed_fg.db)
            arms.append(FixGraphArm(seed_fg, run_ns=f"seed{seed}"))
        for arm in arms:
            log(f"  arm {arm.name}")
            t0 = time.time()
            scs = synthetic.generate(seed=seed, reps=reps)
            metrics = run_arm(arm, scs, rounds, seed, enc, log)
            all_runs.append({"seed": seed, "arm": arm.name,
                             "tier": "synthetic", "metrics": metrics,
                             "seconds": round(time.time() - t0, 1)})
    doc = {
        "tier": "synthetic",
        "rounds": rounds, "max_attempts": MAX_ATTEMPTS, "seeds": list(seeds),
        "scenario_count": len(synthetic.generate(seed=0, reps=reps)),
        "token_counting": token_note,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": all_runs,
    }
    path = os.path.join(out_dir, "results.json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
    log(f"wrote {path}")
    return doc


def chart(doc: dict, out_dir: str = "results") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_arm: dict[str, list[dict]] = {}
    for run in doc["runs"]:
        by_arm.setdefault(run["arm"], []).extend(run["metrics"])

    rounds = range(doc["rounds"])
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    fig.suptitle("Fixgraph vs baselines — synthetic tier "
                 f"({doc['scenario_count']} scenarios, seeds {doc['seeds']})")

    for arm, ms in sorted(by_arm.items()):
        # mean across seeds per round
        def series(key):
            return [sum(m[key] for m in ms if m["round"] == r) /
                    max(1, sum(1 for m in ms if m["round"] == r))
                    for r in rounds]
        axes[0].plot(rounds, series("first_try"), marker="o", label=arm)
        axes[1].plot(rounds, series("avg_attempts"), marker="o", label=arm)
        axes[2].plot(rounds, series("ctx_tokens"), marker="o", label=arm)

    axes[0].set_title("fixed on first try")
    axes[0].set_ylim(0, 1)
    axes[1].set_title("avg attempts to fix (lower better)")
    axes[2].set_title("context tokens returned (log)")
    axes[2].set_yscale("log")
    for ax in axes:
        ax.set_xlabel("round")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    path = os.path.join(out_dir, "chart.png")
    fig.savefig(path, dpi=140)
    return path
