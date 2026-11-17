"""Static text written by `init`: the layout test, the CLAUDE.md snippet, pre-commit and CI configs."""
from __future__ import annotations

TEST_NAME = "test_layout.py"
VENDOR_NAME = "layout_guard_checker.py"

LAYOUT_TEST = '''\
"""The project layout (layout.toml), enforced by layout-guard. Written by `layout-guard init`.

When this fails, its message says where the code goes instead. Do not loosen layout.toml (new
top-level entries, higher limits, new [[allow]] entries) to make it pass: move the code, or ask
the user. The checker comes from `layout_guard_checker.py` next to this file when it exists
(`layout-guard init --vendor`), else from the installed `layout_guard` package.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
VENDORED = Path(__file__).with_name("layout_guard_checker.py")


def _checker():
    if VENDORED.is_file():
        spec = importlib.util.spec_from_file_location("layout_guard_checker", VENDORED)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    try:
        from layout_guard import checker
    except ImportError:
        pytest.fail("layout-guard is not importable, so the layout cannot be checked. Add it as a "
                    "dev dependency (e.g. `uv add --dev layout-guard`, or pip install it into the "
                    "test environment), or run `layout-guard init --vendor` to copy the checker "
                    "into tests/ (no dependency). Do not delete this test.", pytrace=False)
    return checker


def test_layout_matches_layout_toml():
    checker = _checker()
    cfg = checker.load(PROJECT / "layout.toml")
    violations = checker.check_project(cfg)
    assert not violations, checker.violations_text(
        cfg, violations, f"{len(violations)} layout violation(s) of {cfg.file}:")
'''

VENDOR_HEADER = ("# Vendored by `layout-guard init --vendor` (layout-guard {version}); do not edit.\n"
                 "# To update: delete this file and run `layout-guard init --vendor` again.\n")

CLAUDE_SNIPPET = """\
# Claude Code instructions

Project rules for every agent live in AGENTS.md (the layout rules come from layout.toml):

@AGENTS.md
"""

PRE_COMMIT = """\
# layout-guard: fail the commit when the project layout breaks layout.toml.
repos:
  - repo: local
    hooks:
      - id: layout-guard
        name: layout-guard check
        entry: layout-guard check
        language: system
        pass_filenames: false
        always_run: true
"""

GITHUB_ACTION = """\
# layout-guard: fail the build when the project layout breaks layout.toml.
name: layout-guard
on: [push, pull_request]
jobs:
  layout:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      # Replace OWNER with where layout-guard is hosted (it has no runtime dependencies).
      - run: pip install "layout-guard @ git+https://github.com/OWNER/layout-guard"
      - run: layout-guard check
"""
