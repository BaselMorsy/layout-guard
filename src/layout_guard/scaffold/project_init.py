"""`init`: infer the structure, write layout.toml, the layout test and friends (never overwriting).

Profiles: `python-src` (packages in src/) and `python-flat-package` (packages at the project
root). Every package found becomes a [[root]], and so does `tests/`. Today's problems become
legacy [[allow]] entries, so the project starts green and only new violations fail.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import layout_guard
from layout_guard import checker
from layout_guard.scaffold import render, templates

PROFILES = ("python-src", "python-flat-package")
ENTRY_FILES = ["__init__.py", "__main__.py", "config.py", "py.typed"]
FLAT_THRESHOLD = 3  # more loose top-level modules than this = a flat package
_SKIP_DIRS = {"tests", "test", "docs", "examples", "scripts", "build", "dist", "src"}


@dataclass
class InitResult:
    profile: str
    roots: list[str]
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _ignored(name: str) -> bool:
    return any(Path(name).match(p) for p in checker.IGNORE)


def _packages_in(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
                  and p.name not in _SKIP_DIRS and not _ignored(p.name))


def detect(project: Path) -> str:
    return "python-flat-package" if _packages_in(project) and not _packages_in(project / "src") else "python-src"


def _fallback_name(project: Path) -> str:
    try:
        name = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8-sig"))["project"]["name"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        name = project.name
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name)).lower()


def _migration_dirs(project: Path, roots: list[str]) -> list[str]:
    """Names of alembic-style folders (env.py + script.py.mako): scripts, not packages."""
    found = set()
    for r in roots:
        for env in (project / r).rglob("env.py"):
            if (env.parent / "script.py.mako").is_file():
                found.add(env.parent.name)
    return sorted(found)


def _package_root(project: Path, rel: str, forbidden: set[str], skip: list[str]) -> tuple[render.RootPlan, list[str]]:
    d = project / rel
    dirs = sorted(p.name for p in d.iterdir()
                  if p.is_dir() and not _ignored(p.name) and p.name not in skip) if d.is_dir() else []
    loose = sorted(p.name for p in d.glob("*.py") if p.name not in ENTRY_FILES) if d.is_dir() else []
    flat = len(loose) > FLAT_THRESHOLD
    ok_dirs = [x for x in dirs if x.lower() not in forbidden]
    top = ENTRY_FILES + ([] if flat else loose) + [f"{x}/" for x in ok_dirs]
    return render.RootPlan(rel, list(dict.fromkeys(top)), layer_hint=ok_dirs), ok_dirs


def build(project: Path, profile: str | None = None) -> tuple[str, render.TomlPlan, str]:
    """The layout.toml text and its plan, for `project` (nothing is written)."""
    profile = profile or detect(project)
    forbidden = set(checker.FORBIDDEN_NAMES)
    if profile == "python-src":
        rels = [f"src/{p}" for p in _packages_in(project / "src")] or [f"src/{_fallback_name(project)}"]
    else:
        rels = _packages_in(project) or [_fallback_name(project)]
    migrations = _migration_dirs(project, rels)
    roots, where = [], []
    for rel in rels:
        rp, dirs = _package_root(project, rel, forbidden, migrations)
        roots.append(rp)
        where += [(f"Code about {d} in {rel}/ (edit: say what belongs here)", f"{rel}/{d}/") for d in dirs]
    roots.append(render.RootPlan("tests", [], require_init=(project / "tests" / "__init__.py").is_file()))
    where = where or [("A data model or type shared by several packages", f"{rels[0]}/core/"),
                      ("A pure business rule (validation, scoring, a policy)", f"{rels[0]}/domain/"),
                      ("Code that talks to the outside world (HTTP, DB, files)", f"{rels[0]}/adapters/"),
                      ("Wiring: build objects, route commands, start the app", f"{rels[0]}/__main__.py")]
    where.append(("Anything that fits none of these", "ask the user; do not add a top-level module"))
    tp = render.TomlPlan(roots, where, ignore=[*checker.IGNORE, *migrations] if migrations else None)
    # Whatever fails with the inferred rules is existing debt: reviewed legacy [[allow]] entries.
    cfg = checker.parse(tomllib.loads(render.layout_toml(tp)), project / checker.CONFIG_NAME)
    tp.allows = checker.findings(cfg)
    return render.layout_toml(tp), tp, profile


def _write_new(path: Path, text: str, result: InitResult) -> bool:
    if path.exists():
        result.skipped.append(path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:  # "x": never overwrite
        fh.write(text)
    result.written.append(path)
    return True


def init(project: Path, profile: str | None = None, *, vendor: bool = False,
         pre_commit: bool = False, github_action: bool = False) -> InitResult:
    project = project.resolve()
    result = InitResult(profile or detect(project), [])
    # The test files first, so the legacy entries computed below already count them.
    _write_new(project / "tests" / templates.TEST_NAME, templates.LAYOUT_TEST, result)
    if vendor:
        source = Path(checker.__file__).read_text(encoding="utf-8")
        _write_new(project / "tests" / templates.VENDOR_NAME,
                   templates.VENDOR_HEADER.format(version=layout_guard.__version__) + source, result)
    toml_text, tp, result.profile = build(project, profile)
    result.roots = [r.path for r in tp.roots]
    cfg_path = project / checker.CONFIG_NAME
    if not _write_new(cfg_path, toml_text, result):
        result.notes.append(f"{cfg_path} exists; left unchanged (AGENTS.md is rendered from it).")
    elif tp.allows:
        result.notes.append(f"{len(tp.allows)} existing problem(s) recorded as legacy [[allow]] entries; "
                            "`layout-guard plan` proposes the moves that remove them.")
    _write_new(project / "AGENTS.md", render.agents_md(checker.load(cfg_path)), result)
    if not _write_new(project / "CLAUDE.md", templates.CLAUDE_SNIPPET, result):
        if "@AGENTS.md" not in (project / "CLAUDE.md").read_text(encoding="utf-8-sig", errors="replace"):
            result.notes.append("CLAUDE.md exists and does not import AGENTS.md: add a line `@AGENTS.md` to it.")
    if pre_commit and not _write_new(project / ".pre-commit-config.yaml", templates.PRE_COMMIT, result):
        result.notes.append(".pre-commit-config.yaml exists: add this hook to it:\n" + templates.PRE_COMMIT)
    if github_action:
        _write_new(project / ".github" / "workflows" / "layout-guard.yml", templates.GITHUB_ACTION, result)
    return result
