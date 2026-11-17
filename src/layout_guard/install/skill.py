"""install-skill / uninstall-skill / doctor: copy the `python-layout-guard` skill folder only.

Nothing here reads or writes settings.json: layout-guard installs no hooks.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path

SKILL_NAME = "python-layout-guard"


def default_claude_dir() -> Path:
    """`$CLAUDE_CONFIG_DIR` if set, else `~/.claude`."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def skill_source() -> Path:
    """The skill folder: packaged (`layout_guard/_skill/...`) or in the source checkout."""
    here = Path(__file__).resolve()
    for cand in (here.parents[1] / "_skill" / SKILL_NAME, here.parents[3] / "skill" / SKILL_NAME):
        if (cand / "SKILL.md").is_file():
            return cand
    raise FileNotFoundError(f"cannot find the packaged skill ({SKILL_NAME}/SKILL.md)")


def _differs(src: Path, dest: Path) -> bool:
    if not dest.is_dir():
        return True
    cmp = filecmp.dircmp(src, dest)
    return bool(cmp.left_only or cmp.diff_files or cmp.funny_files)


def install(claude_dir: Path, dry_run: bool = False) -> str:
    src, dest = skill_source(), claude_dir / "skills" / SKILL_NAME
    if not _differs(src, dest):
        return f"{dest}: skill already up to date"
    if dry_run:
        return f"would copy {src} -> {dest} (dry run, nothing written)"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return f"skill copied to {dest}"


def uninstall(claude_dir: Path, dry_run: bool = False) -> str:
    dest = claude_dir / "skills" / SKILL_NAME
    try:
        ours = f"name: {SKILL_NAME}" in (dest / "SKILL.md").read_text(encoding="utf-8")[:500]
    except OSError:
        return f"{dest}: not installed"
    if not ours:
        return f"{dest}: not our skill (no `name: {SKILL_NAME}`); left alone"
    if dry_run:
        return f"would remove {dest} (dry run)"
    shutil.rmtree(dest)
    return f"removed {dest}"


def doctor(claude_dir: Path) -> list[tuple[bool, str]]:
    """(ok, message) checks: Python version, git, the CLI on PATH, the installed skill."""
    dest = claude_dir / "skills" / SKILL_NAME
    checks = [(sys.version_info >= (3, 12), f"python {sys.version.split()[0]} (needs 3.12+)"),
              (shutil.which("git") is not None, "git on PATH (apply-plan needs it)"),
              (shutil.which("layout-guard") is not None, "layout-guard on PATH")]
    try:
        checks.append((not _differs(skill_source(), dest), f"skill at {dest} matches this version"))
    except FileNotFoundError as exc:
        checks.append((False, str(exc)))
    return checks
