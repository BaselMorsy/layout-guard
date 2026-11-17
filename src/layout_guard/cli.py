"""The `layout-guard` command line (Python projects only)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_OK, EXIT_VIOLATIONS, EXIT_USAGE = 0, 1, 2


def _out(text: str) -> None:
    """Print without dying on a console that cannot encode a character (Windows cp1252)."""
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(text.encode(enc, errors="replace").decode(enc) + "\n")
    sys.stdout.flush()


def _load(path: str, config: str | None = None):
    from layout_guard import checker
    start = Path(path)
    file = Path(config) if config else checker.find_config(start)
    if file is None:
        _out(f"layout-guard: no layout.toml in {start.resolve()} or above. Run `layout-guard init` first.")
        return None
    try:
        return checker.load(file, base=start if config else None)
    except checker.ConfigError as exc:
        _out(f"layout-guard: invalid {file}: {exc}")
        return None


def cmd_check(a) -> int:
    from layout_guard import checker
    cfg = _load(a.path, a.config)
    if cfg is None:
        return EXIT_USAGE
    vs = checker.check_project(cfg)
    if a.json:
        _out(json.dumps([{"rule": v.rule, "path": v.path, "message": v.message, "fix": v.fix,
                          "edge": list(v.edge) if v.edge else None} for v in vs], indent=2))
    elif vs:
        _out(checker.violations_text(cfg, vs, f"layout-guard: {len(vs)} violation(s) of {cfg.file}:"))
    else:
        legacy = sum(a.reason.startswith("legacy") for a in cfg.allow)
        _out(f"layout-guard: OK ({cfg.file})" + (f"; {legacy} legacy [[allow]] entr{'y' if legacy == 1 else 'ies'} "
                                               "left (`layout-guard plan`)" if legacy else ""))
    return EXIT_VIOLATIONS if vs else EXIT_OK


def cmd_explain(a) -> int:
    from layout_guard import checker
    cfg = _load(a.path, a.config)
    if cfg is None:
        return EXIT_USAGE
    _out(checker.explain_text(cfg))
    return EXIT_OK


def cmd_init(a) -> int:
    from layout_guard.scaffold.project_init import PROFILES, init
    if a.profile and a.profile not in PROFILES:
        _out(f"unknown profile {a.profile!r}; use one of {list(PROFILES)}")
        return EXIT_USAGE
    r = init(Path(a.path), a.profile, vendor=a.vendor, pre_commit=a.pre_commit, github_action=a.github_action)
    _out(f"layout-guard init: profile {r.profile}, roots {', '.join(r.roots)}")
    for p in r.written:
        _out(f"  wrote   {p}")
    for p in r.skipped:
        _out(f"  kept    {p} (exists; never overwritten)")
    for n in r.notes:
        _out(f"  note: {n}")
    _out("Next: review layout.toml (roots, top_level, [[where]], [root.layers]), then `layout-guard check`.")
    return EXIT_OK


def cmd_plan(a) -> int:
    from layout_guard.refactor import plan, planfile
    cfg = _load(a.path)
    if cfg is None:
        return EXIT_USAGE
    project = cfg.project_dir
    toml_path, md_path = project / planfile.PLAN_NAME, project / planfile.SUMMARY_NAME
    if toml_path.exists() and not a.overwrite:
        try:
            p = planfile.load(toml_path)
        except planfile.PlanError as exc:
            _out(f"layout-guard: invalid {toml_path}: {exc}")
            return EXIT_USAGE
        _out(f"{toml_path} exists (edit it, or --overwrite to regenerate it); summary refreshed.")
    else:
        p = plan.make_plan(cfg)
        toml_path.write_text(planfile.render(p), encoding="utf-8", newline="\n")
        _out(f"wrote {toml_path}: {len(p.groups())} group(s), {len(p.moves)} move(s), "
             f"{len(p.unassigned)} unassigned, {len(p.split_by_hand)} to split by hand")
    md_path.write_text(planfile.summary(p), encoding="utf-8", newline="\n")
    _out(f"wrote {md_path}")
    _out("Next: refine the groups to real responsibilities, commit, then `layout-guard apply-plan --all --dry-run`.")
    return EXIT_OK


def cmd_apply(a) -> int:
    from layout_guard.refactor import apply
    opts = apply.Options(group=a.group, test_cmd=a.test_cmd, no_tests_ok=a.no_tests_ok,
                         dry_run=a.dry_run, no_commit=a.no_commit, keep_going=a.keep_going)
    try:
        results = apply.apply_plan(Path(a.path), opts, log=_out)
    except apply.ApplyError as exc:
        _out(f"layout-guard apply-plan: {exc}")
        return EXIT_USAGE
    _out(apply.report(results))
    ok = all(r.status in ("applied", "uncommitted", "skipped", "dry-run") for r in results)
    return EXIT_OK if ok else EXIT_VIOLATIONS


def _claude_dir(raw: str | None) -> Path:
    from layout_guard.install.skill import default_claude_dir
    return Path(raw).expanduser().resolve() if raw else default_claude_dir()


def cmd_install_skill(a) -> int:
    from layout_guard.install import skill
    try:
        _out((skill.uninstall if a.remove else skill.install)(_claude_dir(a.claude_dir), a.dry_run))
    except OSError as exc:
        _out(f"layout-guard: {exc}")
        return EXIT_USAGE
    return EXIT_OK


def cmd_doctor(a) -> int:
    from layout_guard.install.skill import doctor
    checks = doctor(_claude_dir(a.claude_dir))
    for ok, msg in checks:
        _out(f"[{'ok' if ok else 'FAIL'}] {msg}")
    return EXIT_OK if all(ok for ok, _ in checks) else EXIT_VIOLATIONS


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="layout-guard", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("check", help="check the whole project against its layout.toml")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--json", action="store_true")
    s.add_argument("--config", help="use this layout.toml; its root paths are relative to PATH")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("explain", help="print the rules and the decision table")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--config", help="use this layout.toml instead of the nearest one")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("init", help="write layout.toml, tests/test_layout.py, AGENTS.md, CLAUDE.md (never overwrites)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--profile", help="python-src | python-flat-package (detected when omitted)")
    s.add_argument("--vendor", action="store_true",
                   help="copy the checker into tests/ so the layout test needs no dependency")
    s.add_argument("--pre-commit", action="store_true")
    s.add_argument("--github-action", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("plan", help="write layout-plan.toml (editable moves) and layout-refactor-plan.md")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--overwrite", action="store_true", help="regenerate an existing layout-plan.toml")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("apply-plan", help="apply layout-plan.toml group by group: move, rewrite imports, test, commit")
    s.add_argument("path", nargs="?", default=".")
    which = s.add_mutually_exclusive_group(required=True)
    which.add_argument("--group", metavar="NAME", help="apply this group only")
    which.add_argument("--all", action="store_true", help="apply every group in plan order")
    s.add_argument("--test-cmd", metavar="CMD", help="default: uv run pytest -q / <python> -m pytest -q (detected)")
    s.add_argument("--no-tests-ok", action="store_true", help="allow a project without tests (check only)")
    s.add_argument("--dry-run", action="store_true", help="show what each group would do; change nothing")
    s.add_argument("--no-commit", action="store_true", help="apply one group, leave it staged, stop")
    stop = s.add_mutually_exclusive_group()
    stop.add_argument("--stop-on-failure", dest="keep_going", action="store_false", help="(default)")
    stop.add_argument("--keep-going", dest="keep_going", action="store_true",
                      help="after a refused or rolled-back group, continue with the next")
    s.set_defaults(fn=cmd_apply, keep_going=False)

    for name, remove, help_ in (("install-skill", False, "copy the python-layout-guard skill to <claude-dir>/skills/"),
                                ("uninstall-skill", True, "remove the python-layout-guard skill folder")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--claude-dir", help="default: $CLAUDE_CONFIG_DIR or ~/.claude")
        s.add_argument("--dry-run", action="store_true", help="say what would change; change nothing")
        s.set_defaults(fn=cmd_install_skill, remove=remove)

    s = sub.add_parser("doctor", help="check python, git, the CLI and the installed skill")
    s.add_argument("--claude-dir")
    s.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    return a.fn(a)
