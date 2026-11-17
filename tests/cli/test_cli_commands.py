"""The command line: exit codes, messages with fix hints, JSON output, plan, and dogfooding."""
from __future__ import annotations

import json
from pathlib import Path

from layout_guard.cli import main
from tests.conftest import write_tree
from tests.refactor.test_plan import FLAT

REPO = Path(__file__).resolve().parents[2]


def run(argv, capsys):
    code = main(argv)
    return code, capsys.readouterr().out


def test_check_ok_and_failing(clean_project, capsys):
    assert run(["check", str(clean_project)], capsys)[0] == 0
    (clean_project / "src/app/core/misc.py").write_text("")
    code, out = run(["check", str(clean_project)], capsys)
    assert code == 1 and "[forbidden-name]" in out and "Fix:" in out and "ask the user" in out


def test_check_json(clean_project, capsys):
    (clean_project / "src/app/top.py").write_text("")
    code, out = run(["check", str(clean_project), "--json"], capsys)
    assert code == 1 and json.loads(out)[0]["rule"] == "top-level"


def test_check_without_layout_toml(tmp_path, capsys):
    code, out = run(["check", str(tmp_path)], capsys)
    assert code == 2 and "layout-guard init" in out


def test_explain_prints_rules_and_table(clean_project, capsys):
    code, out = run(["explain", str(clean_project)], capsys)
    assert code == 0 and "max 3 Python files" in out and "A data model -> core/models.py" in out


def test_init_and_plan_cli(tmp_path, capsys):
    write_tree(tmp_path, FLAT)
    code, out = run(["init", str(tmp_path), "--profile", "python-src"], capsys)
    assert code == 0 and "wrote" in out and "legacy" in out
    assert run(["init", str(tmp_path), "--profile", "node-ts"], capsys)[0] == 2
    code, out = run(["check", str(tmp_path)], capsys)
    assert code == 0 and "legacy [[allow]] entries left" in out
    code, out = run(["plan", str(tmp_path)], capsys)
    assert code == 0 and "3 group(s)" in out
    assert (tmp_path / "layout-plan.toml").is_file() and (tmp_path / "layout-refactor-plan.md").is_file()
    plan = tmp_path / "layout-plan.toml"
    plan.write_text(plan.read_text().replace('name = "user"', 'name = "accounts"').replace('group = "user"', 'group = "accounts"'))
    code, out = run(["plan", str(tmp_path)], capsys)  # an edited plan is kept; the summary follows it
    assert code == 0 and "exists" in out and "Group `accounts`" in (tmp_path / "layout-refactor-plan.md").read_text()


def test_layout_guard_obeys_its_own_layout(capsys):
    code, out = run(["check", str(REPO)], capsys)
    assert code == 0, out
