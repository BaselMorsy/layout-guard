"""The two edits apply-plan makes to layout.toml, as surgical text edits (comments survive):
delete [[allow]] entries that became stale, and allow a new top-level package the plan creates."""
from __future__ import annotations

import json
import re

_HEADER = re.compile(r"^\s*\[")
_ALLOW = re.compile(r"^\s*\[\[allow\]\]\s*(#.*)?$")
_ROOT = re.compile(r"^\s*\[\[root\]\]\s*(#.*)?$")
_PATH = re.compile(r'^\s*path\s*=\s*"([^"]*)"', re.M)


def _blocks(lines: list[str], header: re.Pattern) -> list[tuple[int, int]]:
    """(start, end) line ranges of every table whose header matches."""
    out = []
    for i, line in enumerate(lines):
        if header.match(line):
            j = i + 1
            while j < len(lines) and not _HEADER.match(lines[j]):
                j += 1
            out.append((i, j))
    return out


def remove_allows(text: str, indices: set[int]) -> str:
    """Delete the [[allow]] tables with these 0-based indices (in file order)."""
    lines = text.splitlines(keepends=True)
    drop = {k for n, (s, e) in enumerate(_blocks(lines, _ALLOW)) if n in indices for k in range(s, e)}
    return "".join(line for k, line in enumerate(lines) if k not in drop)


def add_top_level(text: str, root_path: str, entry: str) -> str | None:
    """`entry` appended to top_level of the [[root]] with this path; None if there is no such list."""
    lines = text.splitlines(keepends=True)
    for s, e in _blocks(lines, _ROOT):
        block = "".join(lines[s:e])
        m = _PATH.search(block)
        if not m or m.group(1).strip("/") != root_path:
            continue
        key = re.search(r"^\s*top_level\s*=\s*\[", block, re.M)
        if not key:
            return None
        close = block.index("]", key.end())
        before = block[:close].rstrip()
        sep = "" if before.endswith("[") else " " if before.endswith(",") else ", "
        block = before + sep + json.dumps(entry) + block[close:]
        return "".join(lines[:s]) + block + "".join(lines[e:])
    return None
