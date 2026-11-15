import argparse

from .harness import run_benchmark, chart


def main():
    ap = argparse.ArgumentParser(prog="python -m fixgraph.bench")
    ap.add_argument("--tier", default="synthetic", choices=["synthetic", "real"])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="results")
    ap.add_argument("--skip-graph", action="store_true",
                    help="run only the non-graph arms (offline sanity check)")
    args = ap.parse_args()

    fg = None
    if not args.skip_graph:
        from ..api import FixGraph
        fg = FixGraph()

    if args.tier == "synthetic":
        doc = run_benchmark(fg=fg, seeds=tuple(range(args.seeds)),
                            rounds=args.rounds, reps=args.reps,
                            out_dir=args.out)
        print("chart:", chart(doc, args.out))
    else:
        from .real_tier import run_real
        doc = run_real(fg=fg, out_dir=args.out)
        print("chart:", chart(doc, args.out))


if __name__ == "__main__":
    main()
