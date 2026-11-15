"""fixgraph run -- <command>

Runs the command. On non-zero exit it fingerprints the environment, looks
the error up, and prints the best known fix with its evidence — the fastest
thing to show in a demo. Exit code of the wrapped command is passed through.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from .api import FixGraph
from .envdetect import fingerprint


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fixgraph")
    ap.add_argument("--url", help="HydraDB HTTP base (default $FIXGRAPH_URL)")
    ap.add_argument("--token", help="bearer token (default $FIXGRAPH_TOKEN)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a command, look up fixes on failure")
    run.add_argument("command", nargs=argparse.REMAINDER)
    lk = sub.add_parser("lookup", help="look up fixes for an error string")
    lk.add_argument("error", nargs="+")
    rp = sub.add_parser("report", help="record an outcome")
    rp.add_argument("fix", nargs="+")
    rp.add_argument("--ok", action="store_true")
    rp.add_argument("--error", default="")
    args = ap.parse_args(argv)

    import os
    if args.url:
        os.environ["FIXGRAPH_URL"] = args.url
    if args.token:
        os.environ["FIXGRAPH_TOKEN"] = args.token
    fg = FixGraph()
    env = fingerprint()

    if args.cmd == "run":
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            ap.error("run needs a command: fixgraph run -- <cmd>")
        proc = subprocess.run(command)
        if proc.returncode == 0:
            return 0
        # can't capture stderr from an inherited-console run; the caller
        # pipes it: `fixgraph run -- cmd 2>err.txt`. Best effort: env lookup
        # on the generic failure is still useful.
        err_file = os.environ.get("FIXGRAPH_ERR_FILE")
        error_text = open(err_file).read() if err_file and os.path.exists(err_file) \
            else f"command exited {proc.returncode}: {' '.join(command)}"
        res = fg.lookup(error_text, env)
        _print_result(res, env)
        return proc.returncode

    if args.cmd == "lookup":
        res = fg.lookup(" ".join(args.error), env)
        _print_result(res, env)
        return 0

    if args.cmd == "report":
        ep = fg.report(args.error or "manual report", env,
                       " ".join(args.fix), "ok" if args.ok else "fail")
        print(f"recorded episode {ep}")
        return 0
    return 2


def _print_result(res, env):
    if not res.seen:
        print("fixgraph: no episodes recorded for this failure yet.")
        return
    print(f"fixgraph: {res.episode_count} episodes on this signature "
          f"(env: {', '.join(env[:4])}…)")
    for i, c in enumerate(res.candidates[:3], 1):
        print(f"\n#{i}  {c.title or c.action[:70]}")
        print(f"    {c.action}")
        print(f"    score={c.score:.2f}  ok≈{c.ok_w:.1f}  fail≈{c.fail_w:.1f}  "
              f"episodes={c.n_episodes}"
              + (f"  shared facets: {', '.join(c.shared_facets[:4])}"
                 if c.shared_facets else ""))
    if res.deciding_facets:
        print(f"\ndeciding facets: {', '.join(res.deciding_facets[:5])}")
    if res.next_signatures:
        print("known follow-on failures: "
              + ", ".join(t[:50] for _, t in res.next_signatures[:2]))


if __name__ == "__main__":
    raise SystemExit(main())
