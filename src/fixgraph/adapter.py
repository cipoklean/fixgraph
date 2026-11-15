"""HydraDB access layer.

One small interface, two implementations:

- ``HydraSelfHosted``: the open-source engine over its HTTP query API
  (``POST /v1/graphs/{graph}/query`` with ``{"cell_id","query","parameters"}``).
  Implemented and tested — this is what the benchmark runs against.
- ``HydraCloud``: HydraDB Cloud "Bring Your Own Graph"
  (``POST https://api.hydradb.com/byog/query`` with
  ``{"database","collection","query","params"}``). Thin stub, **untested** —
  we had no API key. Queries are written inside the documented shared subset
  of the dialect, so the same query text is meant to run on both.

Both speak the same narrow Cypher subset (see USING-HYDRADB.md):
no ``RETURN *``, no arithmetic in RETURN, one-hop writes only, labels via the
``UNWIND … MERGE … SET n:Label`` batch shape, batches capped at 1024 items.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Iterable, Protocol

BATCH_LIMIT = 1000  # server cap is 1024; stay under it


class QueryError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def unwrap(cell: Any) -> Any:
    """Decode one typed response cell: {"type": ..., "value": ...}.

    A null cell has no ``value`` key at all.
    """
    if not isinstance(cell, dict) or "type" not in cell:
        return cell
    if cell["type"] == "list":
        return [unwrap(c) for c in cell.get("value", [])]
    return cell.get("value")


def unwrap_row(row: Iterable[Any]) -> list[Any]:
    return [unwrap(c) for c in row]


class HydraAdapter(Protocol):
    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one statement, return rows as list-of-dicts keyed by column."""
        ...


class HydraSelfHosted:
    def __init__(self, base_url: str, token: str, graph_id: str = "default",
                 cell_id: str = "cell-0", namespace: str | None = None,
                 timeout: float = 30.0):
        self.url = f"{base_url.rstrip('/')}/v1/graphs/{graph_id}/query"
        self.token = token
        self.cell_id = cell_id
        self.namespace = namespace
        self.timeout = timeout
        self.last_bookmark: str | None = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if self.namespace:
            headers["X-Graph-Namespace"] = self.namespace
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # 422 arrives as a bare string, neither rows nor error
                raise QueryError(f"HTTP {e.code}: {raw.strip()}", code=str(e.code))
            err = payload.get("error", {})
            raise QueryError(err.get("message", raw), code=err.get("code", str(e.code)))

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"cell_id": self.cell_id, "query": cypher}
        if parameters:
            body["parameters"] = parameters
        if self.last_bookmark:
            body["bookmark"] = self.last_bookmark  # read-your-writes
        rows: list[dict[str, Any]] = []
        page_size = 4096
        body["page_size"] = page_size
        cursor = None
        query_id = None
        while True:
            if cursor is not None:
                body["cursor"] = cursor
                body["query_id"] = query_id
            payload = self._post(body)
            if "error" in payload:
                err = payload["error"]
                raise QueryError(err.get("message", "query failed"), code=err.get("code"))
            if "rows" not in payload:
                raise QueryError(f"unexpected response: {payload!r}")
            cols = payload["columns"]
            rows.extend(dict(zip(cols, unwrap_row(r))) for r in payload["rows"])
            if payload.get("bookmark"):
                self.last_bookmark = payload["bookmark"]
            cursor = payload.get("next_cursor")
            query_id = payload.get("query_id", query_id)
            if cursor is None:
                return rows
            # next_cursor is a consuming iterator: each fetch takes the NEXT page.

    def mutate(self, cypher: str, parameters: dict[str, Any] | None = None) -> None:
        self.query(cypher, parameters)


class HydraCloud:
    """UNTESTED stub for HydraDB Cloud BYOG — no API key was available.

    Same query dialect per docs; differs in endpoint, body shape
    (``params`` not ``parameters``, ``database``+``collection`` not
    ``cell_id``) and auth. Wire in a key and smoke-test before relying on it.
    """

    def __init__(self, api_key: str, database: str, collection: str,
                 base_url: str = "https://api.hydradb.com", timeout: float = 30.0):
        self.url = f"{base_url.rstrip('/')}/byog/query"
        self.api_key = api_key
        self.database = database
        self.collection = collection
        self.timeout = timeout

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        body = {
            "database": self.database,
            "collection": self.collection,
            "query": cypher,
            "params": parameters or {},
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise QueryError(f"HTTP {e.code}: {e.read().decode(errors='replace')}")
        if "error" in payload:
            err = payload["error"]
            raise QueryError(err.get("message", "query failed"), code=err.get("code"))
        cols = payload.get("columns", [])
        return [dict(zip(cols, unwrap_row(r))) for r in payload.get("rows", [])]


def from_env(env=None) -> HydraAdapter:
    """Build an adapter from FIXGRAPH_* env vars (or the passed mapping)."""
    import os

    env = env or os.environ
    if env.get("FIXGRAPH_CLOUD_KEY"):
        return HydraCloud(
            api_key=env["FIXGRAPH_CLOUD_KEY"],
            database=env.get("FIXGRAPH_DATABASE", "fixgraph"),
            collection=env.get("FIXGRAPH_COLLECTION", "default"),
        )
    return HydraSelfHosted(
        base_url=env.get("FIXGRAPH_URL", "http://127.0.0.1:8443"),
        token=env.get("FIXGRAPH_TOKEN", "local-development-token-32-bytes"),
        graph_id=env.get("FIXGRAPH_GRAPH", "default"),
        cell_id=env.get("FIXGRAPH_CELL", "cell-0"),
        namespace=env.get("FIXGRAPH_NAMESPACE") or None,
    )
