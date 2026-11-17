"""Shared fixtures: tiny projects in temp dirs, git repos for apply-plan, a fake claude dir.

No test touches the real ~/.claude, and none uses the network.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

BASIC_TOML = """\
version = 1

[[root]]
path = "src/app"
top_level = ["__init__.py", "__main__.py", "config.py", "*/"]
max_files_per_dir = 3

[root.layers]
core = []
domain = ["core"]
adapters = ["core", "domain"]
config = []
"__main__" = ["*"]

[[where]]
adding = "A data model"
put_in = "core/models.py"

[[where]]
adding = "A business rule"
put_in = "domain/"
"""


def write_tree(base: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
    return base


@pytest.fixture
def make_project(tmp_path):
    def make(files: dict[str, str], toml: str | None = BASIC_TOML) -> Path:
        proj = tmp_path / "proj"
        proj.mkdir(exist_ok=True)
        if toml is not None:
            (proj / "layout.toml").write_text(toml, encoding="utf-8")
        return write_tree(proj, files)
    return make


CLEAN = {
    "src/app/__init__.py": '"""app."""\n',
    "src/app/__main__.py": "from app.core import models\nfrom app.adapters import cli\n",
    "src/app/config.py": "",
    "src/app/core/__init__.py": "",
    "src/app/core/models.py": "",
    "src/app/domain/__init__.py": "",
    "src/app/domain/rules.py": "from app.core.models import X\n",
    "src/app/adapters/__init__.py": "",
    "src/app/adapters/cli.py": "from ..domain import rules\n",
}


@pytest.fixture
def clean_project(make_project):
    return make_project(dict(CLEAN))


@pytest.fixture
def claude_dir(tmp_path):
    d = tmp_path / "fake-claude"
    d.mkdir()
    return d


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(tmp_path):
    """make(files) -> a committed git repo on branch `work` (main exists too)."""
    def make(files: dict[str, str], branch: str = "work") -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        write_tree(repo, files)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "test")
        git(repo, "config", "core.autocrlf", "false")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "baseline")
        if branch != "main":
            git(repo, "switch", "-qc", branch)
        return repo
    return make
