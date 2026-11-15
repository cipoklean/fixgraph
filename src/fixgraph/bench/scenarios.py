"""Scenario model shared by both benchmark tiers.

A scenario is one reproducible failure: raw error text, the environment it
happened in (a set of facet keys), the plausible fixes an agent might try,
and a deterministic `check(fix_index) -> bool` oracle.

For the synthetic tier the oracle evaluates a hidden rule. For the real tier
it replays a cached container execution — applying a fix to a scenario is
deterministic, so each (scenario, fix) pair is executed in a clean container
exactly once and the verdict is cached (see bench/real/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Scenario:
    sid: str
    error_text: str
    env: list[str]                    # facet keys, e.g. ["python@3.12", "os:ubuntu-24.04"]
    env_label: str                    # env variant id, e.g. "ubuntu24-py312"
    fixes: list[str]                  # candidate fix actions, default order
    check: Callable[[int], bool]      # fix index -> did it work
    first_round: int = 0              # for held-out scenarios/envs
    tags: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)  # e.g. {"round": n} for drift oracles
