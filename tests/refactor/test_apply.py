"""`apply-plan` on real git repos: the whole flow, preconditions, refusals and rollback."""
from __future__ import annotations

import subprocess
import sys

import pytest

from layout_guard import checker
from layout_guard.cli import main
from layout_guard.refactor.apply import ApplyError, Options, apply_plan
from tests.conftest import git

PY = f'"{sys.executable}"'
QUICK = f"{PY} -c pass"

APP = {
    ".gitignore": "__pycache__/\n.pytest_cache/\n",
    "pyproject.toml": '[project]\nname = "app"\n\n[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    "src/app/__init__.py": 'from .user_model import User\n__all__ = ["User"]\n',
    "src/app/user_model.py": "from .db import connect\n\n\nclass User:\n    conn = connect()\n",
    "src/app/user_service.py": "from app.user_model import User\nfrom app import db\n\n\ndef make():\n    return User()\n",
    "src/app/user_routes.py": "from . import user_service\n\n\ndef route():\n    return user_service.make()\n",
    "src/app/db.py": "def connect():\n    return 1\n",
    "src/app/cache.py": "",
    "tests/test_app.py": ("from unittest import mock\n\nimport app.user_model\nfrom app.user_routes import route\n\n\n"
                          "def test_route():\n    assert isinstance(route(), app.user_model.User)\n\n\n"
                          "def test_patch_target_is_reported():\n    assert mock.patch('app.user_model.User')\n"),
}

TOML = """\
version = 1

[[root]]
path = "src/app"
top_level = ["__init__.py", "db.py", "cache.py", "user_model.py", "user_service.py", "user_routes.py"]
"""


def plan(*moves: tuple[str, str, str], packages: dict[str, str] | None = None) -> str:
    out = ["version = 1"]
    for path, doc in (packages or {}).items():
        out += ["[[new_package]]", f'path = "{path}"', f'docstring = "{doc}"']
    for group, src, dst in moves:
        out += ["[[move]]", f'group = "{group}"', f'from = "{src}"', f'to = "{dst}"']
    return "\n".join(out) + "\n"


USER = [("user", "src/app/user_model.py", "src/app/user/model.py"),
        ("user", "src/app/user_service.py", "src/app/user/service.py"),
        ("user", "src/app/user_routes.py", "src/app/user/routes.py")]


@pytest.fixture
def repo(git_repo):
    def make(extra: dict[str, str] | None = None, plan_text: str | None = None, toml: str = TOML, branch="work"):
        files = {**APP, "layout.toml": toml, "layout-plan.toml": plan_text or plan(*USER), **(extra or {})}
        return git_repo(files, branch)
    return make


def run(repo, **kw):
    log: list[str] = []
    return apply_plan(repo, Options(**{"test_cmd": QUICK, **kw}), log.append), log


def pytest_passes(repo) -> bool:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                          capture_output=True).returncode == 0


def test_full_flow_moves_rewrites_tests_and_commits(git_repo):
    """init -> plan -> commit -> apply-plan: legacy entries go away, tests still pass."""
    files = {k: v for k, v in APP.items()}
    repo = git_repo(files)
    assert main(["init", str(repo)]) == 0
    assert main(["plan", str(repo)]) == 0
    plan_text = (repo / "layout-plan.toml").read_text()
    assert 'to = "src/app/user/model.py"' in plan_text
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "adopt layout-guard")
    legacy_before = len(checker.load(repo / "layout.toml").allow)
    [res], _ = run(repo, group="user", test_cmd=f"{PY} -m pytest -q -p no:cacheprovider")
    assert res.status == "applied", res.reason
    assert (repo / "src/app/user/__init__.py").read_text().startswith('"""User:')
    assert (repo / "src/app/user/service.py").read_text().startswith("from app.user.model import User\n")
    assert "from . import service as user_service" in (repo / "src/app/user/routes.py").read_text()
    assert (repo / "src/app/user/model.py").read_text().startswith("from ..db import connect")
    assert (repo / "src/app/__init__.py").read_text().startswith("from .user.model import User")
    assert "import app.user.model\n" in (repo / "tests/test_app.py").read_text()
    assert any("app.user_model" in w and "tests/test_app.py" in w for w in res.warnings)  # mock.patch
    cfg = checker.load(repo / "layout.toml")
    assert checker.check_project(cfg) == [] and len(cfg.allow) < legacy_before and res.stale_removed
    assert "user/" in cfg.roots[0].top_level and res.top_level_added
    assert git(repo, "status", "--porcelain") == ""
    assert "layout: move 3 module(s) into src/app/user" in git(repo, "log", "-1", "--format=%s")
    assert pytest_passes(repo)


def test_refuses_dirty_tree_main_branch_and_non_git(repo, tmp_path):
    r = repo()
    (r / "src/app/db.py").write_text("x = 1\n")
    with pytest.raises(ApplyError, match="not clean"):
        run(r, group="user")
    git(r, "checkout", "--", ".")
    git(r, "switch", "-q", "main")
    with pytest.raises(ApplyError, match="refusing to work on main"):
        run(r, group="user")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "layout.toml").write_text(TOML)
    (plain / "layout-plan.toml").write_text(plan(*USER))
    with pytest.raises(ApplyError, match="not inside a git work tree"):
        run(plain, group="user")


def test_no_tests_needs_a_flag_and_failing_baseline_is_refused(repo):
    r = repo()
    git(r, "rm", "-q", "tests/test_app.py")
    git(r, "commit", "-qm", "no tests")
    with pytest.raises(ApplyError, match="no tests"):
        run(r, group="user", test_cmd=None)
    with pytest.raises(ApplyError, match="baseline tests fail"):
        run(r, group="user", test_cmd=f"{PY} -c \"raise SystemExit(1)\"")
    [res], _ = run(r, group="user", test_cmd=None, no_tests_ok=True)
    assert res.status == "applied"


def test_rollback_when_tests_fail_after_the_move(repo):
    r = repo({"fail_if_moved.py": "import pathlib, sys\nsys.exit(pathlib.Path('src/app/user').exists())\n"})
    head = git(r, "rev-parse", "HEAD")
    [res], _ = run(r, group="user", test_cmd=f"{PY} fail_if_moved.py")
    assert res.status == "rolled-back" and "tests fail" in res.reason
    assert git(r, "rev-parse", "HEAD") == head and git(r, "status", "--porcelain") == ""
    assert not (r / "src/app/user").exists() and (r / "src/app/user_model.py").is_file()


def test_rollback_when_check_fails_after_the_move(repo):
    toml = TOML + '\n[root.layers]\n"__init__" = ["*"]\ndb = []\nuser_model = ["*"]\nuser_service = ["*"]\nuser_routes = ["*"]\n'
    [res], _ = run(repo(toml=toml), group="user")
    assert res.status == "rolled-back" and "not a layer" in res.reason


@pytest.mark.parametrize("plan_text, needle", [
    (plan(("g", "src/app/cache.py", "src/app/db.py")), "already exists"),
    (plan(("g", "src/app/cache.py", "src/app/x/a.py"), ("g", "src/app/db.py", "src/app/x/a.py")), "two moves"),
    (plan(("g", "src/app/cache.py", "src/app/json/cache.py")), "standard-library"),
    (plan(("g", "src/app/cache.py", "src/app/store/json.py")), "standard-library"),
    (plan(("g", "src/app/cache.py", "src/app/db/cache.py")), "collide with the module"),
    (plan(("g", "src/app/__init__.py", "src/app/x/__init__.py")), "cannot be moved"),
    (plan(("g", "src/app/nope.py", "src/app/x/nope.py")), "not a tracked file"),
])
def test_name_collisions_are_refused(repo, plan_text, needle):
    r = repo(plan_text=plan_text)
    head = git(r, "rev-parse", "HEAD")
    [res], _ = run(r)
    assert res.status == "refused" and needle in res.reason, res.reason
    assert git(r, "rev-parse", "HEAD") == head and git(r, "status", "--porcelain") == ""


def test_module_imported_by_string_is_refused(repo):
    r = repo({"src/app/loader.py": "import importlib\nm = importlib.import_module('app.cache')\n"},
             plan(("g", "src/app/cache.py", "src/app/store/cache.py")))
    [res], _ = run(r)
    assert res.status == "refused" and "imported by string" in res.reason and "src/app/loader.py:2" in res.reason


def test_new_import_cycle_is_refused(repo):
    """core/__init__ imports app.cache; app.cache imports app.db. Moving db into core makes
    app.core -> app.cache -> app.core.db -> app.core a cycle."""
    extra = {"src/app/core/__init__.py": "from app.cache import C\n",
             "src/app/cache.py": "from app.db import connect\nC = connect\n"}
    [res], _ = run(repo(extra, plan(("g", "src/app/db.py", "src/app/core/db.py"))))
    assert res.status == "refused" and "import cycle" in res.reason


def test_dry_run_changes_nothing(repo):
    r = repo()
    [res], _ = run(r, group="user", dry_run=True)
    assert res.status == "dry-run" and "src/app/user/service.py" in res.rewritten
    assert git(r, "status", "--porcelain") == "" and not (r / "src/app/user").exists()


def test_no_commit_leaves_the_group_staged_and_stops(repo):
    r = repo(plan_text=plan(*USER, ("db", "src/app/db.py", "src/app/store/db.py")))
    results, _ = run(r, no_commit=True)
    assert [x.status for x in results] == ["uncommitted"]
    assert git(r, "log", "--format=%s") == "baseline\n"  # nothing committed
    assert "R  src/app/user_model.py -> src/app/user/model.py" in git(r, "status", "--porcelain")


def test_stop_on_failure_is_default_and_keep_going_continues(repo):
    text = plan(("bad", "src/app/cache.py", "src/app/db.py"), *USER)
    r = repo(plan_text=text)
    results, _ = run(r)
    assert [x.status for x in results] == ["refused"]
    results, _ = run(r, keep_going=True)
    assert [x.status for x in results] == ["refused", "applied"]
    results, _ = run(r, keep_going=True)
    assert [x.status for x in results] == ["refused", "skipped"]  # already applied


def test_only_allows_that_became_stale_are_removed(repo):
    toml = TOML.replace('"user_model.py", "user_service.py", "user_routes.py"', '"user_model.py"') + """
[[allow]]
rule = "top-level"
path = "src/app/user_service.py"
reason = "legacy"

[[allow]]
rule = "top-level"
path = "src/app/user_routes.py"
reason = "legacy"

[[allow]]
rule = "forbidden-name"
path = "src/app/gone.py"
reason = "already stale before the move: not ours to delete"
"""
    r = repo(toml=toml, plan_text=plan(*USER[1:2]))
    [res], _ = run(r)
    assert res.status == "applied" and res.stale_removed == 1
    text = (r / "layout.toml").read_text()
    assert "user_service.py" not in text and "user_routes.py" in text and "gone.py" in text


def test_cli_apply_plan(repo, capsys):
    r = repo()
    assert main(["apply-plan", str(r), "--all", "--dry-run", "--test-cmd", QUICK]) == 0
    assert "group user: dry-run" in capsys.readouterr().out
    assert main(["apply-plan", str(r), "--group", "nope", "--test-cmd", QUICK]) == 2
