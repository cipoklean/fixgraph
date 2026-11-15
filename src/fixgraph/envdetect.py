"""Environment fingerprint -> facet keys.

Probes the local toolchain once and caches the result. Facets are the
vocabulary environments are compared on — keep them coarse (major.minor),
because a facet that is too specific can never match across machines.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from functools import lru_cache

_PROBES = [
    ("python", ["python", "--version"], r"(\d+\.\d+)"),
    ("pip", ["pip", "--version"], r"pip (\d+)"),
    ("node", ["node", "--version"], r"v?(\d+)"),
    ("npm", ["npm", "--version"], r"(\d+)"),
    ("pnpm", ["pnpm", "--version"], r"(\d+)"),
    ("uv", ["uv", "--version"], r"uv (\d+\.\d+)"),
    ("cargo", ["cargo", "--version"], r"cargo (\d+\.\d+)"),
    ("git", ["git", "--version"], r"(\d+\.\d+)"),
]


def _run(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return (out.stdout or out.stderr).strip()
    except Exception:
        return None


@lru_cache(maxsize=1)
def fingerprint() -> list[str]:
    facets = []
    sys_name = platform.system().lower()
    rel = platform.release().lower()
    if sys_name == "linux":
        try:
            info = dict(
                ln.split("=", 1) for ln in
                open("/etc/os-release").read().splitlines() if "=" in ln)
            facets.append(f"os:{info.get('ID', 'linux')}-{info.get('VERSION_ID', '').strip(chr(34))}")
        except OSError:
            facets.append("os:linux")
    else:
        facets.append(f"os:{sys_name}-{rel}")
    facets.append(f"arch:{platform.machine().lower() or 'unknown'}")
    for name, cmd, pat in _PROBES:
        out = _run(cmd)
        if out:
            m = re.search(pat, out)
            if m:
                facets.append(f"{name}@{m.group(1)}")
    return sorted(set(facets))
