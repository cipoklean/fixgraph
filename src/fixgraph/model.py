"""Graph model.

Nodes (each written via its own ``UNWIND … MERGE … SET n:Label`` batch):

    Signature {id, text}      normalised error text
    Facet     {id, key}       one environment fact, e.g. "python@3.12"
    Fix       {id, action, kind, title}
    Episode   {id, ts, agent} one recorded attempt (append-only, source of truth)

Edges:

    (Episode)-[:HIT]->(Signature)
    (Episode)-[:IN]->(Facet)
    (Episode)-[:TRIED_OK]->(Fix)
    (Episode)-[:TRIED_FAIL]->(Fix)
    (Fix)-[:REQUIRES]->(Fix)
    (Fix)-[:SUPERSEDED_BY]->(Fix)
    (Signature)-[:LEADS_TO]->(Signature)
    (Signature)-[:CONFIRMED_FOR]->(Fix)   consolidation summary edge

Everything is written idempotently: ids are deterministic hashes (see
signatures.node_id), node upserts use MERGE, edge writes use the deduplicating
UNWIND batch shape. Re-reporting the same episode id is a no-op — there are
no transactions, so every write must be safe to repeat.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .signatures import node_id


def facet_id(key: str) -> int:
    return node_id("facet", key.strip().lower())


def fix_id(action: str) -> int:
    return node_id("fix", action.strip())


def new_episode_id() -> int:
    return node_id("episode", uuid.uuid4().hex)


@dataclass
class Fix:
    action: str                      # the concrete thing to run/change
    kind: str = "command"            # command | config | pin | patch
    title: str = ""
    id: int = field(init=False)

    def __post_init__(self):
        self.id = fix_id(self.action)


@dataclass
class Episode:
    signature_id: int
    fix_id: int
    outcome: str                     # "ok" | "fail"
    facets: list[str]
    ts: float = field(default_factory=time.time)
    seq: int = 0                     # caller-supplied logical order (for decay)
    agent: str = "unknown"
    id: int = field(default_factory=new_episode_id)
