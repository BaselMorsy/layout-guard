"""Render generated text: layout.toml (from the inferred structure) and AGENTS.md (from a Config)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from layout_guard.checker import ASK, FORBIDDEN_NAMES, MAX_FILES_PER_DIR, Config, Violation, rules_summary

LEGACY = "legacy: existed when layout-guard was set up; remove it (layout-guard plan / apply-plan)"


def q(s: str) -> str:
    """A TOML basic string (JSON string escapes are valid TOML)."""
    return json.dumps(s, ensure_ascii=False)


def qlist(items) -> str:
    return "[" + ", ".join(q(i) for i in items) + "]"


@dataclass
class RootPlan:
    path: str
    top_level: list[str]             # empty: the top-level rule is off for this root
    require_init: bool = True
    layer_hint: list[str] = field(default_factory=list)   # packages, for the commented [root.layers]


@dataclass
class TomlPlan:
    roots: list[RootPlan]
    where: list[tuple[str, str]]
    ignore: list[str] | None = None  # None: the built-in default
    allows: list[Violation] = field(default_factory=list)


def layout_toml(p: TomlPlan) -> str:
    lines = [
        "# layout.toml: this project's layout rules, enforced by layout-guard (tests/test_layout.py).",
        "# `layout-guard explain` prints them; `layout-guard check` checks the whole tree.",
        "# Exceptions only as [[allow]] entries (with a reason) at the end; there is no inline ignore.",
        "version = 1", "", "[defaults]",
        f"max_files_per_dir = {MAX_FILES_PER_DIR}  # per folder, not counting __init__.py",
        f"forbidden_names = {qlist(FORBIDDEN_NAMES)}",
    ]
    if p.ignore is not None:
        lines.append(f"ignore = {qlist(p.ignore)}  # entry names never governed")
    for r in p.roots:
        lines += ["", "[[root]]", f"path = {q(r.path)}"]
        if r.top_level:
            lines += [f'# What may sit directly in {r.path}/ ("name/" = a folder, globs allowed). '
                      "Ask the user before adding to it.", f"top_level = {qlist(r.top_level)}"]
        if not r.require_init:
            lines.append("require_init = false  # the folder has no __init__.py today")
        if r.layer_hint:
            lines += ["# Import direction: each top-level package lists what it may import (itself is",
                      '# implied; "*" = anything, for the composition root). Uncomment to enforce.',
                      "# [root.layers]", *(f"# {name} = []" for name in r.layer_hint), '# "__main__" = ["*"]']
    lines += ["", "# Where new code goes (shown with every violation). Edit to match the project."]
    for adding, put_in in p.where:
        lines += ["[[where]]", f"adding = {q(adding)}", f"put_in = {q(put_in)}", ""]
    if p.allows:
        lines.append("# Reviewed exceptions. `check` fails when an entry no longer matches anything.")
    for v in p.allows:
        lines += ["[[allow]]", f"rule = {q(v.rule)}"]
        if v.rule == "layer" and v.edge:
            lines += [f"from = {q(v.edge[0])}", f"to = {q(v.edge[1])}"]
        else:
            lines.append(f"path = {q(v.path)}")
        if v.rule == "max-files" and v.count:
            lines.append(f"max = {v.count}  # ratchet: may shrink, never grow")
        lines += [f"reason = {q(LEGACY)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def agents_md(cfg: Config) -> str:
    where = ["| You are adding | Put it in |", "|---|---|"]
    where += [f"| {w.adding} | `{w.put_in}` |" for w in cfg.where] or ["| (no table yet) | ask the user |"]
    return "\n".join([
        "# Agent instructions", "",
        "## Project layout (enforced by layout-guard)", "",
        "The layout of this project is declared in `layout.toml`. `tests/test_layout.py` runs the",
        "layout-guard checker with the test suite and fails with a fix hint per violation.",
        "Run `layout-guard check` (or the tests) before you finish. `layout-guard explain` prints",
        "the current rules (layout.toml wins over this file).", "",
        "### Rules", "",
        *[f"- {line}" for line in rules_summary(cfg)],
        "- Exceptions exist only as `[[allow]]` entries in `layout.toml`, each with a reason, agreed",
        "  with the user. There is no inline ignore comment.", "",
        "### Where new code goes", "", *where, "", ASK, "",
        "### How to work", "",
        "1. Read `layout.toml` and this file before adding source files.",
        "2. Pick the package from the table above. Never add a top-level module or folder to make",
        "   something fit, never raise a limit, never edit layout.toml to silence a violation; ask",
        "   the user instead.",
        "3. Split a folder by responsibility before it reaches the file limit: `layout-guard plan`",
        "   proposes groups in layout-plan.toml, `layout-guard apply-plan` moves them on a branch",
        "   and rewrites the imports, one tested commit per group.",
        "4. Name modules and folders after their one responsibility (no utils, helpers, misc, common).",
        "",
    ])
