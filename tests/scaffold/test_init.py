"""`init`: profiles, multi-root inference, legacy entries, the layout test (installed or vendored)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from layout_guard.checker import check_project, load
from layout_guard.scaffold.project_init import detect, init
from tests.conftest import write_tree
from tests.refactor.test_plan import FLAT


@pytest.mark.parametrize("files, expected", [
    ({"pyproject.toml": "", "src/pkg/__init__.py": ""}, "python-src"),
    ({"pyproject.toml": "", "pkg/__init__.py": "", "tests/__init__.py": ""}, "python-flat-package"),
    ({"pyproject.toml": '[project]\nname = "new-thing"\n'}, "python-src"),
])
def test_detect_profile(tmp_path, files, expected):
    write_tree(tmp_path, files)
    assert detect(tmp_path) == expected


def test_new_project_gets_a_green_config_and_the_layout_files(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": '[project]\nname = "new-thing"\n'})
    r = init(tmp_path)
    cfg = load(tmp_path / "layout.toml")
    assert r.roots == ["src/new_thing", "tests"] and check_project(cfg) == []
    assert "@AGENTS.md" in (tmp_path / "CLAUDE.md").read_text()
    assert "Where new code goes" in (tmp_path / "AGENTS.md").read_text()
    assert "def test_layout_matches_layout_toml" in (tmp_path / "tests/test_layout.py").read_text()


def test_flat_package_with_several_packages_and_alembic(tmp_path):
    write_tree(tmp_path, {
        "pyproject.toml": "", "tests/__init__.py": "",
        "app/__init__.py": "", **{f"app/m{i}.py": "" for i in range(5)},
        "db/__init__.py": "", "db/models.py": "",
        "db/migrations/env.py": "", "db/migrations/script.py.mako": "", "db/migrations/versions/1_init.py": "",
        "services/__init__.py": "", "services/io/__init__.py": "", "services/io/routes.py": "",
    })
    r = init(tmp_path)
    cfg = load(tmp_path / "layout.toml")
    assert r.profile == "python-flat-package" and r.roots == ["app", "db", "services", "tests"]
    assert "migrations" in cfg.ignore and check_project(cfg) == []
    assert {a.path for a in cfg.allow} == {f"app/m{i}.py" for i in range(5)}  # flat app: legacy top-level
    assert "models.py" in cfg.roots[1].top_level


def test_flat_project_starts_green_with_ratchets(tmp_path):
    write_tree(tmp_path, FLAT)
    init(tmp_path)
    cfg = load(tmp_path / "layout.toml")
    assert check_project(cfg) == []
    assert {"top-level", "max-files", "forbidden-name"} <= {a.rule for a in cfg.allow}
    assert all(a.reason.startswith("legacy") for a in cfg.allow)
    [mx] = [a for a in cfg.allow if a.rule == "max-files"]
    assert mx.max == 12  # ratchet at today's count (__main__.py + 11 modules)
    write_tree(tmp_path, {"src/flatapp/another.py": ""})
    assert {"top-level", "max-files"} <= {v.rule for v in check_project(cfg)}  # new debt is caught


def test_init_never_overwrites(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": "", "src/p/__init__.py": ""})
    (tmp_path / "AGENTS.md").write_text("mine")
    (tmp_path / "CLAUDE.md").write_text("my rules")
    r = init(tmp_path)
    assert (tmp_path / "AGENTS.md").read_text() == "mine" and any("@AGENTS.md" in n for n in r.notes)
    first = (tmp_path / "layout.toml").read_text()
    r2 = init(tmp_path, "python-flat-package")
    assert (tmp_path / "layout.toml").read_text() == first and tmp_path / "layout.toml" in r2.skipped


def test_pre_commit_and_github_action(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": "", "src/p/__init__.py": ""})
    init(tmp_path, pre_commit=True, github_action=True)
    assert "layout-guard check" in (tmp_path / ".pre-commit-config.yaml").read_text()
    assert "layout-guard check" in (tmp_path / ".github/workflows/layout-guard.yml").read_text()


def _pytest(project):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_layout.py"],
                          cwd=project, capture_output=True, text=True)


def test_layout_test_passes_then_fails_with_the_fix_hint(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": "", "src/p/__init__.py": "", "src/p/core/__init__.py": ""})
    init(tmp_path)
    assert _pytest(tmp_path).returncode == 0
    write_tree(tmp_path, {"src/p/core/utils.py": ""})
    proc = _pytest(tmp_path)
    assert proc.returncode == 1 and "forbidden-name" in proc.stdout and "Fix:" in proc.stdout


BLOCKED = ("import sys; sys.modules['layout_guard'] = None; import pytest; "
           "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_layout.py']))")


def test_layout_test_fails_loudly_when_the_checker_is_missing(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": "", "src/p/__init__.py": ""})
    init(tmp_path)
    proc = subprocess.run([sys.executable, "-c", BLOCKED], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 1 and "layout-guard is not importable" in proc.stdout  # fails, never skips
    assert "init --vendor" in proc.stdout


def test_vendored_checker_needs_no_dependency(tmp_path):
    write_tree(tmp_path, {"pyproject.toml": "", "src/p/__init__.py": ""})
    r = init(tmp_path, vendor=True)
    vendored = tmp_path / "tests/layout_guard_checker.py"
    assert vendored in r.written and vendored.read_text().startswith("# Vendored by `layout-guard init --vendor`")
    assert "import layout_guard" not in vendored.read_text()
    assert check_project(load(tmp_path / "layout.toml")) == []  # the vendored file is counted already
    proc = subprocess.run([sys.executable, "-c", BLOCKED], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
