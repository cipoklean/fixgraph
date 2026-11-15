"""Synthetic tier (labelled synthetic everywhere it is reported).

A seeded generator of ~250 scenarios driven by *hidden rules* that map
(signature, environment) -> correct fix. The hard cases are deliberate:

- IDENTICAL error text needing DIFFERENT fixes by environment: the crux.
  Indistinguishable to text similarity; only traversable structure decides.
- DRIFT: one family's correct fix changes at DRIFT_ROUND — a fix that used
  to work stops working (e.g. a tool upgrade changed the default).
- CHAINS: two-step scenarios where the second fix only works after the
  first has been applied.
- HELD-OUT envs: two environment variants first appear at HELD_OUT_ROUND,
  testing generalisation across facets none of the arms has seen together.
"""

from __future__ import annotations

import random

from .scenarios import Scenario

DRIFT_ROUND = 3
HELD_OUT_ROUND = 3

# environment variants: label -> facet set
ENVS: dict[str, list[str]] = {
    "ubu22-node18": ["node@18", "npm@9", "os:ubuntu-22.04", "arch:x64"],
    "ubu24-node20": ["node@20", "npm@10", "os:ubuntu-24.04", "arch:x64"],
    "deb12-node22": ["node@22", "pnpm@10", "os:debian-12", "arch:x64"],
    "ubu24-py311": ["python@3.11", "pip@23", "os:ubuntu-24.04", "arch:x64"],
    "deb12-py312": ["python@3.12", "pip@24", "os:debian-12", "arch:arm64"],
    "ubu24-py313": ["python@3.13", "uv@0.5", "os:ubuntu-24.04", "arch:x64"],
    # held out until HELD_OUT_ROUND — unseen OS/toolchain combos
    "fed41-node20": ["node@20", "npm@10", "os:fedora-41", "arch:arm64"],
    "al318-py312": ["python@3.12", "pip@24", "os:alpine-3.18", "arch:x64"],
}
HELD_OUT_ENVS = {"fed41-node20", "al318-py312"}


def _facets(env_label: str) -> dict[str, str]:
    """facet key -> (namespace, value): 'node@20' -> ('node','20')"""
    out = {}
    for f in ENVS[env_label]:
        ns, _, v = f.partition("@") if "@" in f else f.partition(":")
        out[ns] = v
    return out


# Each family: identical error text across envs; `rule(env_facets, round)`
# returns the index into `fixes` that actually works.
FAMILIES: list[dict] = [
    {
        "name": "esm-require",
        "text": "Error [ERR_REQUIRE_ESM]: require() of ES Module /x/pkg/index.js "
                "from /x/app.js not supported.",
        "fixes": ["pin pkg to last CJS release (pkg@3.4.0)",
                  "switch require() to dynamic await import()",
                  "add --experimental-require-module flag",
                  "rename importer to .mjs"],
        # node>=20: dynamic import; node<20: pin the CJS build
        "rule": lambda f, r: 1 if int(f.get("node", "0")) >= 20 else 0,
        "node": True,
    },
    {
        "name": "ext-managed",
        "text": "error: externally-managed-environment - pip install refused. "
                "hint: See PEP 668 for the detailed specification.",
        "fixes": ["python3 -m venv .venv && .venv/bin/pip install pkg",
                  "pip install --break-system-packages pkg",
                  "apt-get install -y python3-pkg",
                  "uv pip install --system pkg"],
        # debian/ubuntu: venv route; alpine (apk): break-system-packages;
        # uv present: uv route
        "rule": lambda f, r: 3 if "uv" in f else
                (1 if f.get("os", "").startswith("alpine") else 0),
        "python": True,
    },
    {
        "name": "distutils-gone",
        "text": "ModuleNotFoundError: No module named 'distutils'",
        "fixes": ["pip install setuptools  # provides the _distutils shim",
                  "apt-get install -y python3-distutils",
                  "port caller to importlib.metadata",
                  "downgrade interpreter to python3.11"],
        # py3.12: setuptools shim; py3.13: shim gone, port required
        "rule": lambda f, r: 2 if f.get("python") == "3.13" else 0,
        "python": True,
    },
    {
        "name": "openssl-legacy",
        "text": "Error: error:0308010C:digital envelope routines::unsupported "
                "(ERR_OSSL_EVP_UNSUPPORTED)",
        "fixes": ["NODE_OPTIONS=--openssl-legacy-provider npm run build",
                  "upgrade webpack to >=5.61",
                  "downgrade node to v16",
                  "npm rebuild node-sass"],
        # node 17-19: legacy provider; node>=20: provider flag dropped, upgrade
        "rule": lambda f, r: 1 if int(f.get("node", "0")) >= 20 else 0,
        "node": True,
    },
    {
        "name": "eresolve",
        "text": "npm error ERESOLVE unable to resolve dependency tree: "
                "peer react@\"18\" from pkg@9.1.0",
        "fixes": ["npm install --legacy-peer-deps",
                  "pnpm install --auto-install-peers",
                  "npm install --force",
                  "pin react to the peer range"],
        # pnpm envs: the pnpm route; npm envs: legacy-peer-deps
        "rule": lambda f, r: 1 if "pnpm" in f else 0,
        "node": True,
    },
    {
        "name": "ignored-builds",
        "text": "ERR_PNPM_IGNORED_BUILDS: build scripts were ignored for "
                "3 dependencies (pnpm 10 default)",
        "fixes": ["add onlyBuiltDependencies to package.json",
                  "pnpm install --ignore-scripts=false",
                  "pnpm approve-builds",
                  "downgrade to pnpm@9"],
        # DRIFT: until DRIFT_ROUND the flag fixes it; after, the flag was
        # removed and onlyBuiltDependencies is the only route
        "rule": lambda f, r: 0 if r >= DRIFT_ROUND else 1,
        "node": True,
        "drift": True,
    },
    {
        "name": "numpy-abi",
        "text": "ValueError: numpy.dtype size changed, may indicate binary "
                "incompatibility. Expected 96 from C header, got 88",
        "fixes": ["rebuild pkg against installed numpy",
                  "pip install 'numpy<2'",
                  "pip install --no-binary :all: pkg",
                  "set NPY_DISABLE_CPU_FEATURES"],
        # numpy<2 envs (the py3.11 variant): pin; newer numpy: rebuild
        "rule": lambda f, r: 1 if f.get("python") == "3.11" else 0,
        "python": True,
    },
    {
        "name": "git-ownership",
        "text": "fatal: detected dubious ownership in repository at '/repo'",
        "fixes": ["git config --global --add safe.directory /repo",
                  "chown -R $USER /repo",
                  "git -c safe.directory='*' status",
                  "set GIT_DISCOVERY_ACROSS_FILESYSTEM=1"],
        # same fix everywhere — control case with no env discrimination
        "rule": lambda f, r: 0,
    },
]

CHAIN_A = {
    "name": "chain-step1",
    "text": "error: ca-certificates: certificate verify failed (corporate MITM)",
    "fixes": ["update-ca-certificates with corp root CA",
              "pip install --cert /etc/ssl/corp.pem requests",
              "export SSL_CERT_FILE=/etc/ssl/corp.pem",
              "pip install --trusted-host pypi.org pkg"],
    "rule": lambda f, r: 0,
}
CHAIN_B = {
    "name": "chain-step2",
    "text": "pip download failed: SSLError(SSLCertVerificationError)",
    "fixes": ["retry pip download  # works once the CA is trusted",
              "pip download --no-deps pkg",
              "curl -k -O wheel && pip install ./pkg.whl",
              "pip download --trusted-host files.pythonhosted.org pkg"],
    "rule": lambda f, r: 0,
}


def generate(seed: int = 0, reps: int = 3) -> list[Scenario]:
    rng = random.Random(seed)
    scenarios: list[Scenario] = []

    for fam in FAMILIES:
        pool = "node" if fam.get("node") else ("python" if fam.get("python") else "")
        env_labels = [e for e in ENVS
                      if not pool or any(f.startswith(pool + "@") for f in ENVS[e])]
        for env_label in env_labels:
            held = env_label in HELD_OUT_ENVS
            facets = ENVS[env_label]
            fdict = _facets(env_label)
            for rep in range(reps if not held else max(1, reps - 1)):
                st = {"round": 0}
                scenarios.append(Scenario(
                    sid=f"{fam['name']}:{env_label}:{rep}",
                    error_text=fam["text"],
                    env=list(facets), env_label=env_label,
                    fixes=list(fam["fixes"]),
                    check=lambda i, fam=fam, fd=fdict, st=st: (
                        i == fam["rule"](fd, st["round"])
                    ),
                    first_round=HELD_OUT_ROUND if held else 0,
                    tags=[fam["name"]] + (["drift"] if fam.get("drift") else [])
                        + (["heldout"] if held else []),
                    state=st,
                ))

    # chain scenarios: fixing A exposes B; both must be applied in order.
    # modelled as one scenario whose oracle requires step1's fix tried first.
    for env_label in ("ubu24-py312" if "ubu24-py312" in ENVS else "ubu24-py311",
                      "deb12-py312"):
        if env_label not in ENVS:
            continue
        for rep in range(reps):
            # B-fixes first, the CA prereq at index 2 — deep enough that a
            # default-order walk burns attempts on wrong fixes first
            fixes = [CHAIN_B["fixes"][0], CHAIN_B["fixes"][1],
                     CHAIN_A["fixes"][0], CHAIN_B["fixes"][2],
                     CHAIN_B["fixes"][3]]
            st = {"round": 0, "_seen": -1, "_trusted": False}

            def check(i: int, st=st) -> bool:
                # a fresh round = a fresh environment instance: the CA is
                # untrusted again until re-applied this round
                if st["round"] != st["_seen"]:
                    st["_seen"] = st["round"]
                    st["_trusted"] = False
                if i == 2:            # the CA fix: prereq, not itself a fix
                    st["_trusted"] = True
                    return False
                if i == 0:            # retry: works once the CA is trusted
                    return st["_trusted"]
                return False

            scenarios.append(Scenario(
                sid=f"chain:{env_label}:{rep}",
                error_text=CHAIN_B["text"],
                env=list(ENVS[env_label]), env_label=env_label,
                fixes=fixes, check=check,
                tags=["chain"], state=st,
            ))
    # shuffle each scenario's fix list so the correct fix isn't parked at
    # index 0 — otherwise the no-memory arm gets first-try wins for free
    for sc in scenarios:
        perm = list(range(len(sc.fixes)))
        rng.shuffle(perm)
        old_check = sc.check
        sc.fixes = [sc.fixes[p] for p in perm]
        sc.check = lambda i, oc=old_check, p=perm: oc(p[i])
    rng.shuffle(scenarios)
    return scenarios
