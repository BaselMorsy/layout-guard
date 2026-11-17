"""String references to moved modules: reported with file:line, never edited."""
from __future__ import annotations

from layout_guard.refactor.refs import is_text, scan

OLDS = {"app.cli": "src/app/cli.py", "server": "server.py"}


def test_python_strings_and_imports_by_string():
    src = ('"""Docs mention app.cli."""\nimport importlib\n'
           "importlib.import_module('app.cli')\nmock.patch('app.cli.run')\nx = 'app.client'\n")
    refs = scan("t.py", src, OLDS)
    assert [(r.line, r.by_import) for r in refs] == [(1, False), (3, True), (4, False)]  # not app.client


def test_config_files_line_by_line():
    toml = '[project.scripts]\nbf = "app.cli:main"\nweb = "server:app"\nname = "servers"\n'
    assert [(r.line, r.old) for r in scan("pyproject.toml", toml, OLDS)] == [(2, "app.cli"), (3, "server")]
    assert is_text("alembic.ini") and is_text("Dockerfile") and not is_text("layout-plan.toml")


def test_one_word_modules_only_match_as_module_references():
    refs = scan("t.py", "a = 'the server is up'\nb = 'server'\nc = 'server.app'\n", OLDS)
    assert [r.line for r in refs] == [2, 3]
