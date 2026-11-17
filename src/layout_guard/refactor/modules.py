"""The project's modules as Python sees them: dotted names from file paths, resolution of
relative imports, and the module-level import graph used to refuse moves that create a cycle."""
from __future__ import annotations

import ast
from pathlib import Path


def import_roots(project: Path) -> list[str]:
    """Project-relative folders on sys.path, most specific first ("" = the project folder)."""
    return ["src", ""] if (project / "src").is_dir() else [""]


def module_of(rel: str, roots: list[str]) -> str:
    """`src/app/core/x.py` -> `app.core.x`; a package's `__init__.py` -> the package."""
    for r in roots:
        if not r or rel.startswith(r + "/"):
            parts = (rel[len(r) + 1:] if r else rel)[:-3].split("/")
            if parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts)
    return ""


def package_of(module: str, is_init: bool) -> str:
    return module if is_init else module.rpartition(".")[0]


def resolve(importer: str, is_init: bool, level: int, module: str | None) -> str | None:
    """The absolute module an `ImportFrom` names (None if it climbs above the top package)."""
    if not level:
        return module or None
    parts = [p for p in package_of(importer, is_init).split(".") if p]
    if level - 1 >= len(parts):
        return None
    parts = parts[:len(parts) - (level - 1)]
    return ".".join(parts + ([module] if module else [])) or None


def relative_text(importer_pkg: str, target: str) -> str | None:
    """The relative spelling of `target` from package `importer_pkg`; None across top packages."""
    p, t = [x for x in importer_pkg.split(".") if x], target.split(".")
    c = 0
    while c < min(len(p), len(t)) and p[c] == t[c]:
        c += 1
    if c == 0:
        return None
    return "." * (len(p) - c + 1) + ".".join(t[c:])


def _known(name: str, modules: set[str]) -> str | None:
    while name and name not in modules:
        name = name.rpartition(".")[0]
    return name or None


def _is_type_checking(node: ast.If) -> bool:
    t = node.test
    return (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
        isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING")


def eager_imports(tree: ast.Module, importer: str, is_init: bool, modules: set[str]) -> set[str]:
    """Project modules imported when `importer` is imported (not in functions or TYPE_CHECKING)."""
    found: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and _is_type_checking(node):
            stack.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            found |= {k for a in node.names if (k := _known(a.name, modules))}
        elif isinstance(node, ast.ImportFrom):
            base = resolve(importer, is_init, node.level, node.module)
            for a in node.names if base else ():
                k = _known(f"{base}.{a.name}", modules) if a.name != "*" else _known(base, modules)
                if k:
                    found.add(k)
        stack.extend(ast.iter_child_nodes(node))
    return found


def graph(trees: dict[str, tuple[ast.Module, bool]], modules: set[str]) -> dict[str, set[str]]:
    """module -> modules it needs at import time, incl. its parent package (`__init__` runs
    first). A package importing its own submodules (the re-export pattern) is not an edge."""
    g: dict[str, set[str]] = {}
    for mod, (tree, is_init) in trees.items():
        edges = {t for t in eager_imports(tree, mod, is_init, modules)
                 if t != mod and not t.startswith(mod + ".")}
        parent = mod.rpartition(".")[0]
        if parent in modules:
            edges.add(parent)
        g[mod] = edges
    return g


def cycles(g: dict[str, set[str]]) -> list[frozenset[str]]:
    """Strongly connected components with more than one module (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on, stack, out = set(), [], []
    for start in g:
        if start in index:
            continue
        work = [(start, iter(sorted(g.get(start, ()))))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on.add(start)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = set()
                    while True:
                        x = stack.pop()
                        on.discard(x)
                        comp.add(x)
                        if x == v:
                            break
                    if len(comp) > 1:
                        out.append(frozenset(comp))
            elif w not in index:
                index[w] = low[w] = len(index)
                stack.append(w)
                on.add(w)
                work.append((w, iter(sorted(g.get(w, ())))))
            elif w in on:
                low[v] = min(low[v], index[w])
    return out


def new_cycles(old: dict[str, set[str]], new: dict[str, set[str]], renamed: dict[str, str]) -> list[list[str]]:
    """Cycles of `new` that are not (renamed) cycles of `old`."""
    before = [frozenset(renamed.get(m, m) for m in c) for c in cycles(old)]
    return [sorted(c) for c in cycles(new) if not any(c <= b for b in before)]
