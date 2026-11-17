"""install-skill / uninstall-skill / doctor against a temp claude dir (never the real one)."""
from __future__ import annotations

from layout_guard.cli import main
from layout_guard.install import skill


def test_install_dry_run_then_install_then_uninstall(claude_dir, capsys):
    dest = claude_dir / "skills" / "python-layout-guard"
    assert main(["install-skill", "--claude-dir", str(claude_dir), "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out and not dest.exists()
    assert main(["install-skill", "--claude-dir", str(claude_dir)]) == 0
    assert "name: python-layout-guard" in (dest / "SKILL.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in claude_dir.iterdir()) == ["skills"]  # no settings.json, ever
    assert "already up to date" in skill.install(claude_dir)
    assert main(["uninstall-skill", "--claude-dir", str(claude_dir)]) == 0 and not dest.exists()


def test_uninstall_leaves_a_foreign_skill_alone(claude_dir):
    dest = claude_dir / "skills" / "python-layout-guard"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("---\nname: something-else\n---\n")
    assert "not our skill" in skill.uninstall(claude_dir) and dest.exists()


def test_doctor_reports_the_skill(claude_dir, capsys):
    assert main(["doctor", "--claude-dir", str(claude_dir)]) == 1
    assert "[FAIL] skill at" in capsys.readouterr().out
    skill.install(claude_dir)
    checks = dict((msg.split(" ")[0], ok) for ok, msg in skill.doctor(claude_dir))
    assert checks["skill"] and checks["python"]


def test_default_claude_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert skill.default_claude_dir() == tmp_path
