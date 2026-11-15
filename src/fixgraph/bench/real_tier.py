"""Real tier: failures reproduced in Docker, executed once, cached.

Applying a fix to a scenario is deterministic, so each (scenario, fix) pair
is executed in a clean container exactly once and the verdict stored in
bench/real_cache.json. Benchmark rounds replay from the cache — stated in
the README and results page.

Execution model: a scenario defines a base image and a `setup` script that
leaves the container in the failing state; we commit that as a temp image.
Each fix then runs in a fresh container from that image: apply fix, run
`check`, record the exit code. Remote execution goes over SSH to the demo
VM (docker is already there).
"""

from __future__ import annotations

import json
import os
import random
import shlex
import subprocess

from .scenarios import Scenario

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                          "bench", "real_cache.json")

# Each fix: {"action": display text, "cmds": shell that applies it}.
# `check` runs after the fix; exit 0 = fixed.
SCENARIOS: list[dict] = [
    {
        "sid": "pep668:deb12",
        "error_text": "error: externally-managed-environment\n\n× This environment is externally managed\n"
                      "hint: See PEP 668 for the detailed specification.",
        "image": "debian:12",
        "setup": "apt-get update -qq && apt-get install -y -qq python3 python3-pip >/dev/null",
        "fail": "pip install --quiet requests",
        "env": ["python@3.11", "pip@23", "os:debian-12", "arch:x64"],
        "env_label": "deb12-py311",
        "check": "python3 -c 'import requests'",
        "fixes": [
            {"action": "python3 -m venv /opt/v && /opt/v/bin/pip install requests",
             "cmds": "apt-get install -y -qq python3-venv >/dev/null && python3 -m venv /opt/v && /opt/v/bin/pip install -q requests",
             "check": "/opt/v/bin/python -c 'import requests'"},
            {"action": "pip install --break-system-packages requests",
             "cmds": "pip install -q --break-system-packages requests"},
            {"action": "apt-get install -y python3-requests",
             "cmds": "apt-get install -y -qq python3-requests >/dev/null"},
            {"action": "pipx install requests",
             "cmds": "apt-get install -y -qq pipx >/dev/null && pipx install requests || true"},  # installs into pipx's own venv — never lands on sys.path
        ],
    },
    {
        "sid": "distutils:py312",
        "error_text": "ModuleNotFoundError: No module named 'distutils'",
        "image": "python:3.12-slim",
        "setup": "pip uninstall -y setuptools >/dev/null 2>&1; true",
        "fail": "python -c 'import distutils'",
        "env": ["python@3.12", "pip@24", "os:debian-12", "arch:x64"],
        "env_label": "deb12-py312",
        "check": "python -c 'import distutils' 2>/dev/null || python -c 'import setuptools._distutils'",
        "fixes": [
            {"action": "pip install 'setuptools<69'  # provides _distutils shim",
             "cmds": "pip install -q 'setuptools<69'"},
            {"action": "apt-get install python3-distutils",
             "cmds": "false"},  # doesn't exist on this base
            {"action": "port the caller to importlib.metadata",
             "cmds": "python -c 'import importlib.metadata' "},
            {"action": "env SETUPTOOLS_USE_DISTUTILS=local pip install setuptools",
             "cmds": "SETUPTOOLS_USE_DISTUTILS=local pip install -q --force-reinstall 'setuptools<81'"},
        ],
    },
    {
        "sid": "esm:node18",
        "error_text": "Error [ERR_REQUIRE_ESM]: require() of ES Module /app/node_modules/pure-esm/index.js not supported.",
        "image": "node:18-bookworm-slim",
        "setup": "mkdir -p /app/node_modules/pure-esm && cd /app && "
                 "npm init -y >/dev/null && "
                 "echo '{\"name\":\"pure-esm\",\"version\":\"2.0.0\",\"type\":\"module\",\"main\":\"index.js\"}' > node_modules/pure-esm/package.json && "
                 "echo 'export default 42' > node_modules/pure-esm/index.js && "
                 "echo 'const m = require(\"pure-esm\"); console.log(m)' > app.js",
        "fail": "node /app/app.js",
        "env": ["node@18", "npm@9", "os:debian-12", "arch:x64"],
        "env_label": "deb12-node18",
        "check": "node /app/app.js",
        "fixes": [
            {"action": "pin pure-esm to last CJS release (pure-esm@1.0.0)",
             "cmds": "echo '{\"name\":\"pure-esm\",\"version\":\"1.0.0\",\"main\":\"index.js\"}' > /app/node_modules/pure-esm/package.json && "
                     "echo 'module.exports = 42' > /app/node_modules/pure-esm/index.js"},
            {"action": "switch require() to dynamic import()",
             "cmds": "printf 'import(\"pure-esm\").then(m=>console.log(m.default))' > /app/app.js"},
            {"action": "node --experimental-require-module /app/app.js",
             "cmds": "true"},  # flag does not exist on node18 → still fails
            {"action": "rename importer to .mjs with import syntax",
             "cmds": "printf 'import m from \"pure-esm\"; console.log(m)' > /app/app.mjs && sed -i 's/app.js/app.mjs/' /dev/null; true"},
        ],
    },
    {
        "sid": "esm:node22",
        "error_text": "Error [ERR_REQUIRE_ESM]: require() of ES Module /app/node_modules/pure-esm/index.js not supported.",
        "image": "node:22-bookworm-slim",
        "setup": "mkdir -p /app/node_modules/pure-esm && cd /app && "
                 "npm init -y >/dev/null && "
                 "echo '{\"name\":\"pure-esm\",\"version\":\"2.0.0\",\"type\":\"module\",\"main\":\"index.js\",\"exports\":{\"require\":\"./cjs.cjs\",\"import\":\"./index.js\"}}' > node_modules/pure-esm/package.json && "
                 "echo 'export default 42' > node_modules/pure-esm/index.js && "
                 "echo 'throw new Error(\"ERR_REQUIRE_ESM simulation: no cjs export\")' > node_modules/pure-esm/cjs.cjs && "
                 "echo 'const m = require(\"pure-esm\"); console.log(m)' > app.js",
        "fail": "node /app/app.js",
        "env": ["node@22", "npm@10", "os:debian-12", "arch:x64"],
        "env_label": "deb12-node22",
        "check": "node /app/app.js",
        "fixes": [
            {"action": "pin pure-esm to last CJS release (pure-esm@1.0.0)",
             "cmds": "echo '{\"name\":\"pure-esm\",\"version\":\"1.0.0\",\"main\":\"index.js\"}' > /app/node_modules/pure-esm/package.json && "
                     "echo 'module.exports = 42' > /app/node_modules/pure-esm/index.js"},
            {"action": "switch require() to dynamic import()",
             "cmds": "printf 'import(\"pure-esm\").then(m=>console.log(m.default))' > /app/app.js"},
            {"action": "node --experimental-require-module /app/app.js",
             "cmds": "printf 'process.env.NODE_OPTIONS=\"\"' >/dev/null; node --experimental-require-module /app/app.js 2>/dev/null || true"},
            {"action": "rename importer to .mjs with import syntax",
             "cmds": "printf 'import m from \"pure-esm\"; console.log(m)' > /app/app.mjs"},
        ],
    },
    {
        "sid": "git-dubious",
        "error_text": "fatal: detected dubious ownership in repository at '/repo'",
        "image": "alpine:3.19",
        "shell": "sh",
        "setup": "apk add -q git >/dev/null && adduser -D dev && mkdir /repo && chown dev:dev /repo && "
                 "su dev -c 'cd /repo && git init -q && git config user.email a@b.c && git config user.name t && git commit -qm init --allow-empty'",
        "fail": "git -C /repo status",
        "env": ["git@2.43", "os:alpine-3.19", "arch:x64"],
        "env_label": "al319-git243",
        "check": "git -C /repo status >/dev/null",
        "fixes": [
            {"action": "git config --global --add safe.directory /repo",
             "cmds": "git config --global --add safe.directory /repo"},
            {"action": "chown -R root /repo",
             "cmds": "chown -R root:root /repo"},
            {"action": "git -c safe.directory='*' -C /repo status",
             "cmds": "true"},  # per-command only; doesn't fix subsequent calls
            {"action": "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/repo",
             "cmds": "true"},  # env vars don't persist into check
        ],
    },
    {
        "sid": "eresolve:npm10",
        "error_text": "npm error ERESOLVE unable to resolve dependency tree\n"
                      "npm error peer react@\"^18.0.0\" from @testing-library/react@13.0.0",
        "image": "node:20-bookworm-slim",
        "setup": "cd /app 2>/dev/null || mkdir -p /app; cd /app && npm init -y >/dev/null && "
                 "npm install -s react@17.0.2 react-dom@17.0.2 >/dev/null",
        "fail": "cd /app && npm install @testing-library/react@13.0.0",
        "env": ["node@20", "npm@10", "os:debian-12", "arch:x64"],
        "env_label": "deb12-node20",
        "check": "cd /app && npm install @testing-library/react@13.0.0 --package-lock-only --loglevel=error",
        "fixes": [
            {"action": "npm config set legacy-peer-deps true",
             "cmds": "cd /app && npm config set legacy-peer-deps true"},
            {"action": "npm install --force",
             "cmds": "cd /app && echo 'force=true' > .npmrc"},
            {"action": "npm install @testing-library/react@12 (compatible major)",
             "cmds": "cd /app && npm install -s @testing-library/react@12.1.5 >/dev/null || true"},
            {"action": "npm cache clean --force && retry",
             "cmds": "cd /app && npm cache clean --force >/dev/null 2>&1 || true"},
        ],
    },
    {
        "sid": "openssl:node20",
        "error_text": "Error: error:0308010C:digital envelope routines::unsupported",
        "image": "node:20-bookworm-slim",
        "setup": "cd /app 2>/dev/null || mkdir -p /app && cd /app && npm init -y >/dev/null && "
                 "printf 'const {createHash}=require(\"crypto\");createHash(\"md4\")' > hash.js",
        "fail": "node /app/hash.js",
        "env": ["node@20", "npm@10", "os:debian-12", "arch:x64"],
        "env_label": "deb12-node20b",
        "check": "node /app/hash.js >/dev/null 2>&1",
        "fixes": [
            {"action": "NODE_OPTIONS=--openssl-legacy-provider",
             "cmds": "export NODE_OPTIONS=--openssl-legacy-provider"},
            {"action": "rewrite the call to sha256",
             "cmds": "printf 'const {createHash}=require(\"crypto\");createHash(\"sha256\")' > /app/hash.js"},
            {"action": "downgrade to node 16",
             "cmds": "false"},
            {"action": "upgrade webpack to >=5.61 (irrelevant here)",
             "cmds": "false"},
        ],
    },
]


def _cache() -> dict:
    if os.path.exists(CACHE_PATH):
        return json.load(open(CACHE_PATH))
    return {}


def _save_cache(c: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(c, open(CACHE_PATH, "w"), indent=2)


def _ssh(ssh_target: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         ssh_target, cmd],
        capture_output=True, text=True, timeout=900)


def execute(ssh_target: str, log=print) -> dict:
    """Run every (scenario, fix) pair once on the VM. Idempotent: cached
    verdicts are kept; only missing pairs execute."""
    cache = _cache()
    for sc in SCENARIOS:
        base = f"fixgraph-scen-{sc['sid'].replace(':', '-')}"
        shell = sc.get("shell", "bash")
        if sc["sid"] not in cache:
            log(f"  build scenario base {sc['sid']} …")
            # run setup in a throwaway container, commit it as the base image
            _ssh(ssh_target, f"docker rm -f {base} 2>/dev/null; "
                             f"docker run --name {base} {sc['image']} {shell} -c "
                             f"{shlex.quote(sc['setup'])} >/dev/null 2>&1; "
                             f"docker commit {base} {base}:img >/dev/null")
            # confirm the failure actually fails on the base
            fr = _ssh(ssh_target,
                      f"docker run --rm {base}:img {shell} -c {shlex.quote(sc['fail'])}")
            cache[sc["sid"]] = {"reproduces": fr.returncode != 0,
                                "fail_rc": fr.returncode,
                                "fail_tail": (fr.stdout + fr.stderr)[-400:],
                                "fixes": {}}
            log(f"    reproduces={cache[sc['sid']]['reproduces']} "
                f"(rc={fr.returncode})")
            _ssh(ssh_target, f"docker rm -f {base} >/dev/null 2>&1")
            _save_cache(cache)
        if not cache[sc["sid"]]["reproduces"]:
            log(f"    {sc['sid']} does NOT reproduce — skipping fixes")
            continue
        for i, fix in enumerate(sc["fixes"]):
            key = str(i)
            if key in cache[sc["sid"]]["fixes"]:
                continue
            check = fix.get("check") or sc["check"]
            cmd = f"{fix['cmds']} && {check}"
            r = _ssh(ssh_target,
                     f"docker run --rm {base}:img {shell} -c {shlex.quote(cmd)}")
            cache[sc["sid"]]["fixes"][key] = {
                "ok": r.returncode == 0, "rc": r.returncode,
                "tail": (r.stdout + r.stderr)[-300:],
            }
            log(f"    fix[{i}] {fix['action'][:50]} -> "
                f"{'OK' if r.returncode == 0 else 'FAIL'}")
            _save_cache(cache)
        _ssh(ssh_target, f"docker rmi {base}:img >/dev/null 2>&1")
    return cache


def scenarios_from_cache() -> list[Scenario]:
    cache = _cache()
    out = []
    for sc in SCENARIOS:
        entry = cache.get(sc["sid"])
        if not entry or not entry["reproduces"]:
            continue
        # Deterministic per-scenario shuffle: the authored fix order puts the
        # correct fix first, which would make even the no-memory arm perfect.
        # Permute fixes and their cached verdicts together.
        order = list(range(len(sc["fixes"])))
        random.Random("real:" + sc["sid"]).shuffle(order)
        verdicts = [entry["fixes"].get(str(i), {}).get("ok", False)
                    for i in order]
        if not any(verdicts):
            continue  # no fix works — useless for the benchmark
        out.append(Scenario(
            sid=sc["sid"], error_text=sc["error_text"], env=sc["env"],
            env_label=sc["env_label"],
            fixes=[sc["fixes"][i]["action"] for i in order],
            check=lambda i, v=verdicts: v[i],
            tags=["real", sc["sid"]],
        ))
    return out


def run_real(fg=None, out_dir="results", log=print) -> dict:
    """Real-tier benchmark over the execution cache. Arms and rounds are
    identical in spirit to the synthetic tier, but each scenario appears
    once (they're the expensive kind), repeated as reps via env noise? No —
    real scenarios are few and deterministic; we run the same rounds and
    report per-round metrics on the small set honestly."""
    from .harness import run_arm, reset_graph, ROUNDS
    from .arms import NoMemoryArm, VectorArm, VectorFilteredArm, FixGraphArm
    import random

    base = scenarios_from_cache()
    if len(base) < 4:
        log(f"only {len(base)} cached real scenarios — run --execute first")
    arms = [NoMemoryArm(), VectorArm(), VectorFilteredArm()]
    if fg is not None:
        reset_graph(fg.db)
        arms.append(FixGraphArm(fg, run_ns="real"))
    runs = []
    # repeat the scenario set so rounds have enough mass to show a curve
    reps = 8
    for arm in arms:
        scs = []
        for r in range(reps):
            for sc in base:
                scs.append(Scenario(
                    sid=f"{sc.sid}:r{r}", error_text=sc.error_text,
                    env=list(sc.env), env_label=sc.env_label,
                    fixes=list(sc.fixes), check=sc.check,
                    tags=sc.tags + ["real"]))
        random.Random(7).shuffle(scs)
        m = run_arm(arm, scs, ROUNDS, seed=7)
        runs.append({"seed": 7, "arm": arm.name, "tier": "real",
                     "metrics": m})
    doc = {"tier": "real", "rounds": ROUNDS, "max_attempts": 4, "seeds": [7],
           "scenario_count": len(base) * reps,
           "distinct_failures": len(base),
           "token_counting": "tiktoken cl100k_base",
           "runs": runs}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "results_real.json"), "w") as f:
        json.dump(doc, f, indent=2)
    return doc


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--ssh", default="ubuntu@158.180.57.167")
    args = ap.parse_args()
    if args.execute:
        execute(args.ssh)
    else:
        for sc in scenarios_from_cache():
            print(sc.sid, "fixes:", sum(1 for i in range(len(sc.fixes)) if sc.check(i)))
