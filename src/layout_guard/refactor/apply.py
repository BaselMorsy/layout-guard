"""`layout-guard apply-plan`: apply layout-plan.toml one group at a time, transactionally.

Per group: refuse what is unsafe (static checks), create the target packages, `git mv` the
modules, rewrite the imports of every tracked Python file, run `check` and the tests, then
commit, or roll back to the commit the group started from. Preconditions: a git work tree, a
clean tree, a branch that is not main/master, and passing baseline tests.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable

from layout_guard import checker
from layout_guard.refactor import gitrun, layout_edit, modules, planfile, refs, rewrite

BOM = b"\xef\xbb\xbf"


class ApplyError(RuntimeError):
    """A precondition failed; nothing was changed."""


class Refused(Exception):
    """This group is unsafe to apply mechanically; nothing was changed."""


@dataclass
class Options:
    group: str | None = None            # None: every group, in plan order
    test_cmd: str | None = None         # None: detected
    no_tests_ok: bool = False
    dry_run: bool = False
    no_commit: bool = False
    keep_going: bool = False


@dataclass
class GroupResult:
    group: str
    status: str                          # applied | uncommitted | rolled-back | refused | skipped | dry-run
    reason: str = ""
    commit: str = ""
    moved: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale_removed: int = 0
    top_level_added: list[str] = field(default_factory=list)


@dataclass
class Change:
    """Everything a group will do, computed in memory before anything is touched."""
    moves: list[planfile.Move]
    contents: dict[str, bytes]           # new path -> new bytes (moved or rewritten files only)
    rewritten: list[str]
    new_dirs: list[str]                  # created packages, parents first
    warnings: list[str]


# --- test command ---------------------------------------------------------------------------

def detect_test_cmd(project: Path) -> str:
    if (project / "uv.lock").is_file() and shutil.which("uv"):
        return "uv run pytest -q"
    for venv in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (project / venv).is_file():
            return f'"{project / venv}" -m pytest -q'
    return f'"{sys.executable}" -m pytest -q'


def has_tests(tracked: list[str]) -> bool:
    names = [t.rsplit("/", 1)[-1] for t in tracked]
    return any(n.endswith(".py") and (n.startswith("test_") or n.endswith("_test.py")) for n in names)


def run_tests(project: Path, cmd: str) -> tuple[bool, str]:
    proc = subprocess.run(cmd, shell=True, cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600)
    lines = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
    errors = [line for line in lines if line.startswith("E ")][:8]
    summary = [i for i, line in enumerate(lines) if "short test summary info" in line]
    tail = errors + (lines[summary[-1]:summary[-1] + 20] if summary else lines[-25:])
    return proc.returncode == 0, "\n".join(tail)


# --- planning one group in memory -----------------------------------------------------------

def _decode(raw: bytes) -> tuple[str, bool]:
    bom = raw.startswith(BOM)
    return raw[len(BOM) if bom else 0:].decode("utf-8"), bom


def _new_dirs(project: Path, dsts: list[str], roots: list[str]) -> list[str]:
    out: set[str] = set()
    for dst in dsts:
        d = dst.rpartition("/")[0]
        while d and d not in roots and not (project / d).exists():
            out.add(d)
            d = d.rpartition("/")[0]
    return sorted(out, key=lambda p: (p.count("/"), p))


def _validate(project: Path, plan: planfile.Plan, moves: list[planfile.Move], tracked: set[str],
              roots: list[str]) -> list[str]:
    """Static refusals; returns the new package folders the group will create."""
    problems: list[str] = []
    every_dst = [m.dst for m in plan.moves]
    for m in moves:
        name = m.src.rsplit("/", 1)[-1]
        if m.src not in tracked:
            problems.append(f"{m.src} is not a tracked file")
        if name in ("__init__.py", "__main__.py"):
            problems.append(f"{m.src}: a package's {name} cannot be moved")
        if (project / m.dst).exists() or m.dst in tracked:
            problems.append(f"target {m.dst} already exists")
        if every_dst.count(m.dst) > 1:
            problems.append(f"two moves in the plan target {m.dst}")
        if (project / m.dst[:-3]).is_dir():
            problems.append(f"target {m.dst} would collide with the package folder {m.dst[:-3]}/")
        mod = modules.module_of(m.dst, roots)
        if not mod or not all(p.isidentifier() for p in mod.split(".")):
            problems.append(f"target {m.dst} is not an importable module path")
        stem = m.dst.rsplit("/", 1)[-1][:-3]
        if stem != name[:-3] and stem in sys.stdlib_module_names:
            problems.append(f"target module name {stem!r} is a standard-library module; pick another name")
    new_dirs = _new_dirs(project, [m.dst for m in moves], roots)
    for d in new_dirs:
        base = d.rsplit("/", 1)[-1]
        if base in sys.stdlib_module_names:
            problems.append(f"new package {d}/ is named like the standard-library module {base!r}; pick another name")
        if f"{d}.py" in tracked or f"{d}.py" in every_dst:
            problems.append(f"new package {d}/ would collide with the module {d}.py")
        if not base.isidentifier():
            problems.append(f"new package {d}/ is not a valid Python identifier")
    if problems:
        raise Refused("; ".join(dict.fromkeys(problems)))
    return new_dirs


def compute(project: Path, plan: planfile.Plan, group: str, tracked: list[str]) -> Change:
    roots = modules.import_roots(project)
    moves = plan.moves_of(group)
    new_dirs = _validate(project, plan, moves, set(tracked), roots)
    file_map = {m.src: m.dst for m in moves}
    py = [f for f in tracked if f.endswith(".py") and (project / f).is_file()]
    old_modules = {modules.module_of(f, roots) for f in py}
    mod_moves = {modules.module_of(m.src, roots): modules.module_of(m.dst, roots) for m in moves}
    contents: dict[str, bytes] = {}
    rewritten, warnings = [], []
    old_trees, new_trees = {}, {}
    new_texts: dict[str, str] = {}
    for f in py:
        raw = (project / f).read_bytes()
        try:
            text, bom = _decode(raw)
            old_mod, is_init = modules.module_of(f, roots), f.endswith("/__init__.py") or f == "__init__.py"
            dst = file_map.get(f, f)
            new_mod = modules.module_of(dst, roots)
            result = rewrite.rewrite_source(text, old_mod, new_mod, is_init, mod_moves, old_modules)
            old_trees[old_mod] = (ast.parse(text), is_init)
            new_trees[new_mod] = (ast.parse(result.text), is_init)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            if f in file_map:
                raise Refused(f"{f} cannot be parsed ({exc.__class__.__name__}); its imports cannot be rewritten")
            warnings.append(f"{f}: not parsed ({exc.__class__.__name__}); its imports were not checked")
            continue
        new_texts[dst] = result.text
        warnings += [f"{dst}: {n}" for n in result.notes]
        if result.text != text:
            rewritten.append(dst)
        if result.text != text or dst != f:
            contents[dst] = (BOM if bom else b"") + result.text.encode("utf-8")
    # string references (the new texts for Python, config files as they are)
    olds = {modules.module_of(m.src, roots): m.src for m in moves}
    found: list[refs.Ref] = []
    for f in tracked:
        if refs.is_text(f) and (project / f).is_file():
            path = file_map.get(f, f)
            try:
                text = new_texts.get(path) or (project / f).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            found += refs.scan(path, text, olds)
    by_import = [r.render() for r in found if r.by_import]
    if by_import:
        raise Refused("a moved module is imported by string: " + "; ".join(by_import))
    warnings += [r.render() for r in found]
    # import cycles the move would create (e.g. through an existing package's __init__.py)
    new_modules = {mod_moves.get(m, m) for m in old_modules} | {modules.module_of(d + "/__init__.py", roots)
                                                                for d in new_dirs}
    created = modules.new_cycles(modules.graph(old_trees, old_modules), modules.graph(new_trees, new_modules),
                                 mod_moves)
    if created:
        raise Refused("the move creates an import cycle: " + "; ".join(" <-> ".join(c) for c in created))
    return Change(moves, contents, rewritten, new_dirs, warnings)


# --- executing one group --------------------------------------------------------------------

def _rollback(project: Path, pre: str, created: list[str]) -> None:
    gitrun.git(project, "reset", "--quiet", "--hard", pre)
    for d in sorted(created, key=lambda p: -p.count("/")):
        path = project / d
        shutil.rmtree(path / "__pycache__", ignore_errors=True)
        try:
            path.rmdir()
        except OSError:
            pass


def _read(path: Path) -> str:
    return _decode(path.read_bytes())[0]


def _baseline(cfg: checker.Config) -> tuple[set, set[int]]:
    bad, used = checker.evaluate(cfg)
    return {(v.rule, v.path, v.edge) for v in bad}, used


def _apply_group(project: Path, plan: planfile.Plan, group: str, ch: Change, test_cmd: str | None,
                 commit: bool, log: Callable[[str], None]) -> GroupResult:
    res = GroupResult(group, "applied", moved=[f"{m.src} -> {m.dst}" for m in ch.moves],
                      rewritten=ch.rewritten, warnings=ch.warnings)
    cfg_path = project / checker.CONFIG_NAME
    before_bad, before_used = _baseline(checker.load(cfg_path))
    pre = gitrun.head(project)
    created: list[str] = []
    try:
        for d in ch.new_dirs:
            (project / d).mkdir()
            created.append(d)
            doc = plan.packages.get(d) or f"{d.rsplit('/', 1)[-1]}: modules grouped by layout-guard apply-plan."
            (project / d / "__init__.py").write_text(f'"""{doc.rstrip(".")}."""\n', encoding="utf-8", newline="\n")
            gitrun.git(project, "add", "--", f"{d}/__init__.py")
        for m in ch.moves:
            gitrun.git(project, "mv", "--", m.src, m.dst)
        for path, data in ch.contents.items():
            (project / path).write_bytes(data)
        if ch.contents:
            gitrun.git(project, "add", "--", *ch.contents)
        # a new top-level package of a root with a top_level list is part of the plan: allow it
        cfg = checker.load(cfg_path)
        text = original = _read(cfg_path)
        for d in ch.new_dirs:
            for r in cfg.roots:
                parent, _, name = d.rpartition("/")
                if r.top_level and parent == r.path and not any(
                        p.endswith("/") and fnmatchcase(name, p[:-1]) for p in r.top_level):
                    edited = layout_edit.add_top_level(text, r.path, f"{name}/")
                    if edited:
                        text = edited
                        res.top_level_added.append(f"{name}/ in {r.path}")
        if text != original:
            cfg_path.write_bytes(text.encode("utf-8"))
        bad, used = checker.evaluate(checker.load(cfg_path))
        new_bad = [v for v in bad if (v.rule, v.path, v.edge) not in before_bad]
        if new_bad:
            raise Refused("layout-guard check fails after the move:\n" + "\n".join(v.render() for v in new_bad))
        stale = before_used - used
        if stale:
            cfg_path.write_bytes(layout_edit.remove_allows(_read(cfg_path), stale).encode("utf-8"))
            res.stale_removed = len(stale)
        gitrun.git(project, "add", "--", checker.CONFIG_NAME)
        if test_cmd:
            log(f"  [{group}] running tests: {test_cmd}")
            ok, tail = run_tests(project, test_cmd)
            if not ok:
                raise Refused("tests fail after the move:\n" + tail)
        if commit:
            gitrun.git(project, "commit", "--quiet", "-m", plan.message(group))
            res.commit = gitrun.head(project)[:10]
        else:
            res.status = "uncommitted"
        return res
    except (Refused, gitrun.GitError, OSError, checker.ConfigError) as exc:
        _rollback(project, pre, created)
        res.status, res.reason, res.commit = "rolled-back", str(exc), ""
        res.stale_removed, res.top_level_added, res.rewritten = 0, [], []
        return res
    except BaseException:
        _rollback(project, pre, created)
        raise


# --- the command ----------------------------------------------------------------------------

def _state(project: Path, moves: list[planfile.Move]) -> str:
    done = [not (project / m.src).exists() and (project / m.dst).exists() for m in moves]
    return "done" if all(done) else "partial" if any(done) else "pending"


def apply_plan(project: Path, opts: Options, log: Callable[[str], None] = print) -> list[GroupResult]:
    project = project.resolve()
    if not (project / checker.CONFIG_NAME).is_file():
        raise ApplyError(f"no {checker.CONFIG_NAME} in {project}; run `layout-guard init` first")
    try:
        plan = planfile.load(project / planfile.PLAN_NAME)
    except planfile.PlanError as exc:
        raise ApplyError(f"{exc}; run `layout-guard plan` first") from exc
    groups = plan.groups()
    if opts.group is not None:
        if opts.group not in groups:
            raise ApplyError(f"no group {opts.group!r} in {planfile.PLAN_NAME}; groups: {', '.join(groups) or '(none)'}")
        groups = [opts.group]
    if not gitrun.in_work_tree(project):
        raise ApplyError(f"{project} is not inside a git work tree; apply-plan needs git to roll back")
    br = gitrun.branch(project)
    problems = []
    if br is None:
        problems.append("HEAD is detached; switch to a branch first (git switch -c layout-cleanup)")
    elif br in gitrun.PROTECTED:
        problems.append(f"refusing to work on {br}: create a branch first (git switch -c layout-cleanup)")
    dirty = gitrun.dirty(project)
    if dirty:
        problems.append("the work tree is not clean (commit or stash first, layout-plan.toml included): "
                        + ", ".join(line[3:] for line in dirty[:8]) + (" ..." if len(dirty) > 8 else ""))
    tracked = gitrun.tracked(project)
    test_cmd = opts.test_cmd
    if test_cmd is None:
        if has_tests(tracked):
            test_cmd = detect_test_cmd(project)
        elif not opts.no_tests_ok:
            problems.append("the project has no tests (no test_*.py); pass --test-cmd CMD, or --no-tests-ok "
                            "to rely on `layout-guard check` alone")
    if problems and not opts.dry_run:
        raise ApplyError("; ".join(problems))
    for p in problems:
        log(f"note (dry run): {p}")
    if test_cmd and not opts.dry_run:
        log(f"baseline: {test_cmd}")
        ok, tail = run_tests(project, test_cmd)
        if not ok:
            raise ApplyError(f"the baseline tests fail before any move; fix them or pass a --test-cmd "
                             f"that passes:\n{tail}")
    results: list[GroupResult] = []
    for g in groups:
        state = _state(project, plan.moves_of(g))
        if state == "done":
            results.append(GroupResult(g, "skipped", "already applied"))
            continue
        try:
            if state == "partial":
                raise Refused("some of its moves are already done and some are not; fix layout-plan.toml")
            ch = compute(project, plan, g, gitrun.tracked(project))
        except Refused as exc:
            results.append(GroupResult(g, "refused", str(exc)))
            log(f"  [{g}] refused: {exc}")
            if not opts.keep_going:
                break
            continue
        if opts.dry_run:
            results.append(GroupResult(g, "dry-run", moved=[f"{m.src} -> {m.dst}" for m in ch.moves],
                                       rewritten=ch.rewritten, warnings=ch.warnings))
            continue
        log(f"  [{g}] applying {len(ch.moves)} move(s)")
        res = _apply_group(project, plan, g, ch, test_cmd, not opts.no_commit, log)
        results.append(res)
        log(f"  [{g}] {res.status}" + (f" {res.commit}" if res.commit else ""))
        if res.status == "uncommitted" or (res.status == "rolled-back" and not opts.keep_going):
            break
    return results


def report(results: list[GroupResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"group {r.group}: {r.status}" + (f" ({r.commit})" if r.commit else "")
                     + (f": {r.reason}" if r.reason else ""))
        verb = "moved" if r.status in ("applied", "uncommitted") else "would move"
        lines += [f"  {verb} {m}" for m in r.moved]
        if r.rewritten:
            lines.append(f"  imports rewritten in {len(r.rewritten)} file(s): {', '.join(r.rewritten)}")
        if r.top_level_added:
            lines.append(f"  layout.toml top_level: added {', '.join(r.top_level_added)}")
        if r.stale_removed:
            lines.append(f"  layout.toml: removed {r.stale_removed} stale [[allow]] entr{'y' if r.stale_removed == 1 else 'ies'}")
        lines += [f"  warning: {w}" for w in r.warnings]
    return "\n".join(lines) or "nothing to do"
