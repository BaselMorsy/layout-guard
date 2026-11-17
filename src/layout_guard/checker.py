"""layout-guard's checker: read layout.toml and check a Python project against it.

Stdlib only and self-contained (it imports nothing else from layout_guard), so
`layout-guard init --vendor` can copy this one file into a project and the project's layout test
runs with no extra dependency. Pure pathlib + ast: the checked code is never imported.

Rules: top-level (allow-list for the top of a root), max-files (Python files per folder,
`__init__.py` not counted), forbidden-name (catch-all modules/folders), missing-init (package
folders), layer (import direction between a root's top-level packages; lazy and TYPE_CHECKING
imports count), stale-allow (an [[allow]] entry that no longer matches anything).
"""
from __future__ import annotations

import ast
import os
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

CONFIG_NAME = "layout.toml"
MAX_FILES_PER_DIR = 10
FORBIDDEN_NAMES = ("utils", "helpers", "misc", "common", "stuff", "tmp")
IGNORE = ("__pycache__", "*.pyc", ".*", "*.egg-info")
ALLOW_RULES = ("top-level", "max-files", "forbidden-name", "missing-init", "layer")
ASK = ("If nothing in the table fits, stop and ask the user. Exceptions exist only as [[allow]] "
       "entries in layout.toml that the user agrees to; do not edit layout.toml to make this pass.")


class ConfigError(ValueError):
    """layout.toml is unreadable or invalid; the message says which key and how to fix it."""


@dataclass(frozen=True)
class Root:
    path: str                       # project-relative, `/`-separated ("." = the project folder)
    top_level: tuple[str, ...]      # `name/` matches folders, `name` files, globs ok; empty: off
    max_files: int
    require_init: bool
    package: str                    # import name of the root folder
    layers: dict[str, tuple[str, ...]] = field(default_factory=dict)  # layer -> may import


@dataclass(frozen=True)
class Allow:
    rule: str
    reason: str
    path: str = ""                  # glob on the project-relative path (all rules but layer)
    importer: str = ""              # layer: `from` module glob
    imported: str = ""              # layer: `to` module glob
    max: int | None = None          # max-files only: a ceiling (a ratchet for legacy folders)

    def describe(self) -> str:
        if self.rule == "layer":
            return f"rule={self.rule} from={self.importer} to={self.imported}"
        return f"rule={self.rule} path={self.path}" + (f" max={self.max}" if self.max else "")


@dataclass(frozen=True)
class Where:
    adding: str
    put_in: str


@dataclass(frozen=True)
class Config:
    file: Path
    roots: tuple[Root, ...]
    forbidden_names: tuple[str, ...]
    ignore: tuple[str, ...]
    allow: tuple[Allow, ...]
    where: tuple[Where, ...]
    base: Path | None = None        # folder the root paths are relative to (default: the file's)

    @property
    def project_dir(self) -> Path:
        return self.base or self.file.parent


@dataclass(frozen=True)
class Violation:
    rule: str                       # an ALLOW_RULES entry or "stale-allow"
    path: str                       # project-relative, `/`-separated
    message: str
    fix: str
    edge: tuple[str, str] | None = None   # layer: (importer, imported)
    count: int | None = None              # max-files: how many Python files the folder has

    def render(self) -> str:
        return f"- [{self.rule}] {self.path}: {self.message}\n  Fix: {self.fix}"


# --- layout.toml ----------------------------------------------------------------------------

def _strs(value, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return tuple(value)


def _keys(table: dict, allowed: set[str], where: str) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"unknown key(s) in {where}: {extra}; allowed: {sorted(allowed)}")


def _root(raw: dict, dflt: dict, i: int) -> Root:
    where = f"[[root]] #{i + 1}"
    _keys(raw, {"path", "top_level", "max_files_per_dir", "require_init", "package", "layers"}, where)
    if not isinstance(raw.get("path"), str):
        raise ConfigError(f'{where} needs path = "<folder relative to layout.toml>"')
    path = raw["path"].replace("\\", "/").strip("/") or "."
    max_files = raw.get("max_files_per_dir", dflt.get("max_files_per_dir", MAX_FILES_PER_DIR))
    if not isinstance(max_files, int) or max_files < 1:
        raise ConfigError(f"{where} max_files_per_dir must be a positive integer")
    layers = raw.get("layers", {})
    if not isinstance(layers, dict):
        raise ConfigError(f"[root.layers] of {where} must be a table: layer = [layers it may import]")
    return Root(path=path, top_level=_strs(raw.get("top_level", []), f"{where} top_level"),
                max_files=max_files, require_init=bool(raw.get("require_init", True)),
                package=raw.get("package") or (Path(path).name if path != "." else ""),
                layers={k: _strs(v, f"[root.layers] {k}") for k, v in layers.items()})


def _allow(raw: dict, i: int) -> Allow:
    where = f"[[allow]] #{i + 1}"
    _keys(raw, {"rule", "path", "from", "to", "max", "reason"}, where)
    rule, reason = raw.get("rule"), raw.get("reason", "")
    if rule not in ALLOW_RULES:
        raise ConfigError(f"{where} rule {rule!r}; use one of {list(ALLOW_RULES)}")
    if not isinstance(reason, str) or not reason.strip():
        raise ConfigError(f"{where} needs a non-empty reason: every exception says why it exists")
    if rule == "layer":
        if not (isinstance(raw.get("from"), str) and isinstance(raw.get("to"), str)):
            raise ConfigError(f'{where} (rule = "layer") needs from = "<module>" and to = "<module>"')
        return Allow(rule, reason, importer=raw["from"], imported=raw["to"])
    if not isinstance(raw.get("path"), str):
        raise ConfigError(f'{where} needs path = "<project-relative path or glob>"')
    mx = raw.get("max")
    if mx is not None and (rule != "max-files" or not isinstance(mx, int)):
        raise ConfigError(f'{where}: max = <int> is only valid with rule = "max-files"')
    return Allow(rule, reason, path=raw["path"].replace("\\", "/").strip("/"), max=mx)


def parse(data: dict, file: Path, base: Path | None = None) -> Config:
    _keys(data, {"version", "defaults", "root", "allow", "where"}, "layout.toml")
    if data.get("version", 1) != 1:
        raise ConfigError("version must be 1")
    dflt = data.get("defaults", {})
    _keys(dflt, {"max_files_per_dir", "forbidden_names", "ignore"}, "[defaults]")
    roots = data.get("root", [])
    if not isinstance(roots, list) or not roots:
        raise ConfigError('layout.toml needs at least one [[root]] with path = "..."')
    where = data.get("where", [])
    for i, w in enumerate(where):
        _keys(w, {"adding", "put_in"}, f"[[where]] #{i + 1}")
        if not (isinstance(w.get("adding"), str) and isinstance(w.get("put_in"), str)):
            raise ConfigError(f'[[where]] #{i + 1} needs adding = "..." and put_in = "..."')
    return Config(
        file=file, roots=tuple(_root(r, dflt, i) for i, r in enumerate(roots)),
        forbidden_names=tuple(n.lower() for n in _strs(dflt.get("forbidden_names", list(FORBIDDEN_NAMES)),
                                                       "[defaults] forbidden_names")),
        ignore=_strs(dflt.get("ignore", list(IGNORE)), "[defaults] ignore"),
        allow=tuple(_allow(a, i) for i, a in enumerate(data.get("allow", []))),
        where=tuple(Where(w["adding"], w["put_in"]) for w in where), base=base)


def load(file: Path, base: Path | None = None) -> Config:
    """Read and validate a layout.toml; `base` makes root paths relative to another folder."""
    try:
        data = tomllib.loads(Path(file).read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file}: not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{file}: cannot read: {exc}") from exc
    return parse(data, Path(file).resolve(), Path(base).resolve() if base else None)


def find_config(start: Path) -> Path | None:
    """The nearest layout.toml in `start` (a folder) or any folder above it."""
    start = Path(start).resolve()
    for d in [start, *start.parents]:
        if (d / CONFIG_NAME).is_file():
            return d / CONFIG_NAME
    return None


# --- imports (layer rule) -------------------------------------------------------------------

def module_name(package: str, parts: tuple[str, ...]) -> str:
    """`(core, state.py)` -> `pkg.core.state`; `(core, __init__.py)` -> `pkg.core`."""
    names = [*parts[:-1], parts[-1][:-3]]
    if names[-1] == "__init__":
        names.pop()
    return ".".join([package, *names])


def layer_of(package: str, module: str) -> str:
    """`pkg.workflows.hunt` -> `workflows`; `pkg` itself -> `__init__`."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == package else "__init__"


def imported_modules(source: str, module: str, is_init: bool, package: str) -> set[str] | None:
    """Every module of `package` that `source` imports, anywhere; None when it does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    me = module if is_init else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == package}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = me.split(".")
                base = base[:len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target.split(".")[0] != package:
                continue
            if target == package:  # `from pkg import x`: x may be a subpackage; it names the layer
                found |= {f"{package}.{a.name}" for a in node.names if a.name != "*"}
            else:
                found.add(target)
    return found


def _layer_violations(root: Root, rel: str, module: str, targets: set[str]) -> list[Violation]:
    me = layer_of(root.package, module)
    allowed = root.layers.get(me)
    if allowed is None:
        return [Violation(
            "layer", rel, f"'{me}' is not a layer in [root.layers] of {root.path} "
                          f"(layers: {', '.join(sorted(root.layers))})",
            "move the module into an existing layer, or ask the user to add the new package to "
            "[root.layers] in layout.toml with the layers it may import.", edge=(module, "*"))] if targets else []
    out = []
    for target in sorted(targets):
        theirs = layer_of(root.package, target)
        if theirs == me or theirs in allowed or "*" in allowed:
            continue
        out.append(Violation(
            "layer", rel, f"{module} imports {target}: layer '{me}' may import only "
                          f"{', '.join(allowed) or f'nothing else from {root.package}'} ([root.layers])",
            "move the shared type or function down to a layer both sides may import, or pass it in "
            "from the composition root (a layer allowed to import everything). Do not add an "
            "[[allow]] edge for new code.", edge=(module, target)))
    return out


# --- structure ------------------------------------------------------------------------------

def _top_level_ok(root: Root, name: str, is_dir: bool) -> bool:
    return not root.top_level or any(
        pat.endswith("/") == is_dir and fnmatchcase(name, pat.rstrip("/")) for pat in root.top_level)


def _walk(cfg: Config, top: Path):
    stack = [top]
    while stack:
        d = stack.pop()
        dirs, files = [], []
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not any(fnmatchcase(e.name, pat) for pat in cfg.ignore):
                        (dirs if e.is_dir() else files).append(e.name)
        except OSError:
            continue
        yield d, sorted(dirs), sorted(files)
        stack.extend(d / n for n in sorted(dirs, reverse=True))


def _check_root(cfg: Config, root: Root) -> list[Violation]:
    top = cfg.project_dir if root.path == "." else cfg.project_dir / root.path
    if not top.is_dir():
        return []
    base = () if root.path == "." else tuple(root.path.split("/"))

    def rel(parts: tuple[str, ...]) -> str:
        return "/".join(base + parts) or "."

    out: list[Violation] = []
    py_dirs: set[tuple[str, ...]] = set()
    init_dirs: set[tuple[str, ...]] = set()
    for d, dirs, files in _walk(cfg, top):
        here = d.relative_to(top).parts
        for name, is_dir in [*((n, True) for n in dirs), *((n, False) for n in files)]:
            if not is_dir and not name.endswith(".py"):
                continue
            if not here and not _top_level_ok(root, name, is_dir):
                out.append(Violation(
                    "top-level", rel(here + (name,)),
                    f"top-level {'folder' if is_dir else 'file'} '{name}' in {root.path}/ is not in "
                    f"the allow-list (top_level in layout.toml): {', '.join(root.top_level)}",
                    "put the code in the package whose responsibility it is (see 'Where new code "
                    "goes'); if none fits, ask the user before adding a top-level entry."))
            if name.split(".")[0].lower() in cfg.forbidden_names:
                out.append(Violation(
                    "forbidden-name", rel(here + (name,)),
                    f"'{name}' is a catch-all name (forbidden_names: {', '.join(cfg.forbidden_names)})",
                    "name the module or folder after the one responsibility it has (e.g. "
                    "`text_format.py`, `retry.py`), in the package that owns that responsibility."))
        py = [f for f in files if f.endswith(".py")]
        n = sum(f != "__init__.py" for f in py)
        if n > root.max_files:
            out.append(Violation(
                "max-files", rel(here),
                f"{n} Python files in this folder; the limit is {root.max_files} "
                "(max_files_per_dir, not counting __init__.py)",
                "split by responsibility: create a subpackage for the modules that belong together "
                "(`layout-guard plan` proposes groups, `layout-guard apply-plan` moves them and fixes "
                "imports), or put the new module in an existing subpackage. Do not raise the limit.",
                count=n))
        if "__init__.py" in files:
            init_dirs.add(here)
        if py:
            py_dirs.update(here[:k] for k in range(len(here) + 1))
        if root.layers:
            for f in py:
                try:
                    text = (d / f).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
                module = module_name(root.package, here + (f,))
                targets = imported_modules(text, module, f == "__init__.py", root.package)
                if targets is not None:
                    out += _layer_violations(root, rel(here + (f,)), module, targets)
    if root.require_init:
        out += [Violation("missing-init", rel(parts), "folder with Python modules but no __init__.py",
                          "add an __init__.py with a one-line docstring saying what the package is for.")
                for parts in sorted(py_dirs - init_dirs)]
    return out


def _matches(a: Allow, v: Violation) -> bool:
    if a.rule != v.rule:
        return False
    if v.rule == "layer":
        return v.edge is not None and fnmatchcase(v.edge[0], a.importer) and fnmatchcase(v.edge[1], a.imported)
    return fnmatchcase(v.path, a.path) and (a.max is None or v.count is None or v.count <= a.max)


def findings(cfg: Config) -> list[Violation]:
    """Every rule violation in the project, before [[allow]] entries are applied."""
    return [v for root in cfg.roots for v in _check_root(cfg, root)]


def evaluate(cfg: Config) -> tuple[list[Violation], set[int]]:
    """(violations no [[allow]] covers, indices of the [[allow]] entries that covered something)."""
    used: set[int] = set()
    out: list[Violation] = []
    for v in findings(cfg):
        hit = next((i for i, a in enumerate(cfg.allow) if _matches(a, v)), None)
        if hit is None:
            out.append(v)
        else:
            used.add(hit)
    return out, used


def check_project(cfg: Config) -> list[Violation]:
    """The violations [[allow]] does not cover, plus the [[allow]] entries that matched nothing."""
    out, used = evaluate(cfg)
    out += [Violation("stale-allow", CONFIG_NAME, f"[[allow]] #{i + 1} ({a.describe()}) no longer matches anything",
                      "delete the entry so the exception cannot come back unnoticed.")
            for i, a in enumerate(cfg.allow) if i not in used]
    return out


# --- text -----------------------------------------------------------------------------------

def where_lines(cfg: Config) -> list[str]:
    return [f"  - {w.adding} -> {w.put_in}" for w in cfg.where]


def violations_text(cfg: Config, violations: list[Violation], header: str) -> str:
    lines = [header, *(v.render() for v in violations)]
    if cfg.where:
        lines += ["Where new code goes (layout.toml [[where]]):", *where_lines(cfg)]
    return "\n".join([*lines, ASK])


def rules_summary(cfg: Config) -> list[str]:
    out = []
    for r in cfg.roots:
        bits = [f"max {r.max_files} Python files per folder"]
        if r.top_level:
            bits.insert(0, f"top level only {', '.join(r.top_level)}")
        if r.require_init:
            bits.append("every package folder has __init__.py")
        out.append(f"{r.path}/: " + "; ".join(bits))
        if r.layers:
            out.append(f"{r.path}/ layers (may import): " + "; ".join(
                f"{k} -> {', '.join(v) or 'nothing'}" for k, v in r.layers.items()))
    return out + [f"forbidden catch-all names: {', '.join(cfg.forbidden_names)}"]


def explain_text(cfg: Config) -> str:
    lines = [f"layout-guard rules from {cfg.file}", "", "Rules:", *(f"  - {x}" for x in rules_summary(cfg))]
    if cfg.allow:
        lines += ["", "Reviewed exceptions ([[allow]]):", *(f"  - {a.describe()}: {a.reason}" for a in cfg.allow)]
    lines += ["", "Where new code goes:",
              *(where_lines(cfg) or ["  (no [[where]] table yet: ask the user where new code goes)"]), "", ASK]
    return "\n".join(lines)
