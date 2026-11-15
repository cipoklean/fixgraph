"""Seed the demo graph with a small, believable history — used for the
demo video and for `fixgraph lookup` to have something to say."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fixgraph.api import FixGraph
from fixgraph.bench.harness import reset_graph


def main():
    fg = FixGraph()
    reset_graph(fg.db)
    t0 = time.time() - 86400 * 3  # pretend history started 3 days ago

    cases = [
        # (error, env, [(fix, outcome), ...])
        ("error: externally-managed-environment\n× This environment is "
         "externally managed\nhint: See PEP 668",
         ["python@3.12", "pip@24", "os:ubuntu-24.04", "arch:x64"],
         [("python3 -m venv .venv && .venv/bin/pip install -r requirements.txt", "ok"),
          ("pip install --break-system-packages -r requirements.txt", "ok"),
          ("sudo pip install -r requirements.txt", "fail")]),
        ("ModuleNotFoundError: No module named 'distutils'",
         ["python@3.12", "pip@24", "os:debian-12", "arch:x64"],
         [("pip install 'setuptools<69'", "ok"),
          ("apt-get install -y python3-distutils", "fail"),
          ("pip install 'setuptools<69'", "ok")]),
        ("Error [ERR_REQUIRE_ESM]: require() of ES Module "
         "/app/node_modules/pkg/index.js not supported.",
         ["node@18", "npm@9", "os:ubuntu-22.04", "arch:x64"],
         [("pin pkg to last CJS release", "ok"),
          ("switch require() to dynamic import()", "fail")]),
        ("Error [ERR_REQUIRE_ESM]: require() of ES Module "
         "/app/node_modules/pkg/index.js not supported.",
         ["node@22", "npm@10", "os:debian-12", "arch:x64"],
         [("switch require() to dynamic import()", "ok"),
          ("pin pkg to last CJS release", "fail"),
          ("switch require() to dynamic import()", "ok")]),
        ("fatal: detected dubious ownership in repository at '/repo'",
         ["git@2.43", "os:alpine-3.19", "arch:x64"],
         [("git config --global --add safe.directory /repo", "ok"),
          ("git -c safe.directory='*' status", "fail")]),
        ("Error: error:0308010C:digital envelope routines::unsupported",
         ["node@18", "npm@9", "os:ubuntu-22.04", "arch:x64"],
         [("NODE_OPTIONS=--openssl-legacy-provider npm run build", "ok")]),
        ("Error: error:0308010C:digital envelope routines::unsupported",
         ["node@20", "npm@10", "os:ubuntu-24.04", "arch:x64"],
         [("NODE_OPTIONS=--openssl-legacy-provider npm run build", "fail"),
          ("upgrade webpack to >=5.61", "ok")]),
    ]
    for err, env, tries in cases:
        for i, (fix, outcome) in enumerate(tries):
            fg.report(err, env, fix, outcome, ts=t0 + i * 3600)
    print(f"seeded {sum(len(t) for _,_,t in cases)} episodes")
    fg.link_failure_chain(
        "error: ca-certificates: certificate verify failed",
        "pip download failed: SSLError(SSLCertVerificationError)")
    print(fg.consolidate())


if __name__ == "__main__":
    main()
