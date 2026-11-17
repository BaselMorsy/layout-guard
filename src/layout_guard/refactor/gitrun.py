"""The few git commands apply-plan needs. Never push, never force, never touch other branches."""
from __future__ import annotations

import subprocess
from pathlib import Path

PROTECTED = ("main", "master")


class GitError(RuntimeError):
    pass


def git(project: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode:
        raise GitError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def in_work_tree(project: Path) -> bool:
    try:
        return git(project, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (GitError, OSError):
        return False


def branch(project: Path) -> str | None:
    """The current branch, or None when HEAD is detached."""
    try:
        return git(project, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except GitError:
        return None


def head(project: Path) -> str:
    return git(project, "rev-parse", "HEAD").strip()


def dirty(project: Path) -> list[str]:
    """`git status --porcelain` lines (untracked files included; ignored files not)."""
    return [line for line in git(project, "status", "--porcelain").splitlines() if line.strip()]


def tracked(project: Path) -> list[str]:
    """Tracked files below `project`, relative to it, `/`-separated."""
    return [p for p in git(project, "ls-files", "-z").split("\0") if p]
