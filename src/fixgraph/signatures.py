"""Error-text normalisation -> signature string + deterministic node id.

The same failure must map to the same Signature node no matter which paths,
versions, hashes, line numbers or timestamps appear in the raw text. That is
what lets two different agents on two different machines share memory.
"""

from __future__ import annotations

import hashlib
import re

_ID_MASK = (1 << 63) - 1


def node_id(kind: str, key: str) -> int:
    """Deterministic 63-bit id namespaced by node kind.

    `id` is HydraDB's node identity / upsert key, so a stable hash gives us
    idempotent upserts for free: the same logical node always hashes here.
    """
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & _ID_MASK


# Order matters: strip the most specific patterns first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # timestamps / dates
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}(\.\d+)?(z|[+-]\d{2}:?\d{2})?\b"), " "),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(\.\d+)?\b"), " "),
    # windows + posix file paths (before bare numbers, since paths contain them)
    (re.compile(r"[a-zA-Z]:\\[^\s:'\"]+"), " "),
    (re.compile(r"(?<![\w.-])(/[^\s:'\"]+)+"), " "),
    # "file.py", line 42  /  at line 42  /  :42:13
    (re.compile(r"\bline\s+\d+", re.IGNORECASE), " line N "),
    (re.compile(r":\d+(:\d+)?\b"), " "),
    # hex hashes / addresses (8+ hex chars, or 0x...)
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), " "),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE), " "),
    # version numbers like 1.2.3, v22.04, 3.12.0a1
    (re.compile(r"\bv?\d+(\.\d+)+([-.][a-zA-Z0-9]+)*\b"), " V "),
    # quoted strings (usually contain host-specific paths/names)
    (re.compile(r"'[^'\n]*'"), " 'X' "),
    (re.compile(r'"[^"\n]*"'), ' "X" '),
    # bare numbers left over
    (re.compile(r"\b\d+\b"), " N "),
]

_WS = re.compile(r"\s+")


def normalize(error_text: str) -> str:
    """Strip volatile content from raw error text -> canonical signature."""
    text = error_text.strip().lower()
    # keep only the tail of a traceback: the last non-empty block is the error
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > 3:
        text = "\n".join(lines[-3:])
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return _WS.sub(" ", text).strip()


def signature_id(error_text: str) -> tuple[int, str]:
    """Return (node id, normalized text) for a raw error string."""
    sig = normalize(error_text)
    return node_id("sig", sig), sig
