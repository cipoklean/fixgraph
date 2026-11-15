"""Integration tests — need a live HydraDB (FIXGRAPH_URL/FIXGRAPH_TOKEN).

Run:  python -m pytest tests/test_integration.py -m integration
"""

import os
import time

import pytest

pytestmark = pytest.mark.integration

from fixgraph.adapter import from_env, QueryError          # noqa: E402
from fixgraph.api import FixGraph                          # noqa: E402
from fixgraph.bench.harness import reset_graph             # noqa: E402


@pytest.fixture(scope="module")
def fg():
    if not os.environ.get("FIXGRAPH_URL"):
        pytest.skip("FIXGRAPH_URL not set")
    fg = FixGraph()
    reset_graph(fg.db)
    yield fg
    reset_graph(fg.db)


def test_report_and_lookup_roundtrip(fg):
    err = "ModuleNotFoundError: No module named 'distutils' at /x/y.py"
    env = ["python@3.12", "os:debian-12", "arch:x64"]
    fg.report(err, env, "pip install setuptools<69", "fail")
    fg.report(err, env, "pip install setuptools<69", "ok")
    fg.report(err, env, "apt install python3-distutils", "fail")

    res = fg.lookup(err, env)
    assert res.seen
    assert res.episode_count == 3
    assert res.candidates[0].action.startswith("pip install setuptools")
    assert res.candidates[0].ok_w > res.candidates[0].fail_w


def test_env_discrimination(fg):
    """Same error text, different correct fix by environment."""
    err = "Error [ERR_REQUIRE_ESM]: require() of ES Module /x/m.js"
    env18 = ["node@18", "os:debian-12"]
    env22 = ["node@22", "os:debian-12"]
    fg.report(err, env18, "pin to last CJS release", "ok")
    fg.report(err, env18, "switch to dynamic import()", "fail")
    fg.report(err, env22, "pin to last CJS release", "fail")
    fg.report(err, env22, "switch to dynamic import()", "ok")

    r18 = fg.lookup(err, env18)
    r22 = fg.lookup(err, env22)
    assert r18.candidates[0].action == "pin to last CJS release"
    assert r22.candidates[0].action == "switch to dynamic import()"


def test_negative_memory(fg):
    err = "fatal: detected dubious ownership in repository at '/r'"
    env = ["git@2.43", "os:alpine-3.19"]
    fg.report(err, env, "git config --global --add safe.directory /r", "ok")
    fg.report(err, env, "git -c safe.directory=* status", "fail")
    fg.report(err, env, "git -c safe.directory=* status", "fail")
    res = fg.lookup(err, env)
    assert res.candidates[0].action.startswith("git config")
    assert res.candidates[-1].fail_w == pytest.approx(1.6)  # 1.0 + 0.6 decayed


def test_report_idempotent_on_episode_id(fg):
    err = "Error: error:0308010C:digital envelope routines::unsupported"
    env = ["node@20"]
    ep = fg.report(err, env, "NODE_OPTIONS=--openssl-legacy-provider", "ok",
                   episode_id=424242)
    fg.report(err, env, "NODE_OPTIONS=--openssl-legacy-provider", "ok",
              episode_id=424242)
    res = fg.lookup(err, env)
    assert ep == 424242
    assert res.episode_count == 1


def test_unseen_signature(fg):
    res = fg.lookup("a failure nobody has ever reported xyz123",
                    ["python@3.13"])
    assert not res.seen
    assert res.candidates == []


def test_consolidate_writes_summary_edges(fg):
    err = "npm error ERESOLVE unable to resolve dependency tree"
    env = ["node@20", "npm@10"]
    for _ in range(3):
        fg.report(err, env, "npm install --legacy-peer-deps", "ok")
    out = fg.consolidate()
    assert out["confirmed_edges"] >= 1
