"""Rewrite the imports of one source file after modules move: stdlib `ast` + position edits.

Only import statements, `a.b.c` attribute chains of a plain `import a.b.c`, and `__all__`
entries of a package's `__init__.py` change; every other byte (comments, formatting, line
endings) stays as it was. The names a file binds never change: `from pkg import user_model`
becomes `from pkg.user import model as user_model`.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from layout_guard.refactor.modules import package_of, relative_text, resolve

_MODULE_TOKEN = re.compile(r"from(\s+)([.\w]+)")
_WRAP = 99


@dataclass
class Result:
    text: str
    edits: int = 0
    notes: list[str] = field(default_factory=list)   # things to fix by hand


class _Positions:
    """(lineno, utf-8 byte col) from ast -> index into the str."""

    def __init__(self, source: str):
        self.source = source
        self.starts = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", source)]

    def __call__(self, lineno: int, col: int) -> int:
        start = self.starts[lineno - 1]
        end = self.starts[lineno] if lineno < len(self.starts) else len(self.source)
        return start + len(self.source[start:end].encode("utf-8")[:col].decode("utf-8", "ignore"))

    def span(self, node: ast.AST) -> tuple[int, int]:
        return self(node.lineno, node.col_offset), self(node.end_lineno, node.end_col_offset)

    def line_prefix(self, node: ast.AST) -> str:
        start = self.starts[node.lineno - 1]
        return self.source[start:self(node.lineno, node.col_offset)]

    def rest_of_end_line(self, node: ast.AST) -> str:
        end = self(node.end_lineno, node.end_col_offset)
        nxt = self.starts[node.end_lineno] if node.end_lineno < len(self.starts) else len(self.source)
        return self.source[end:nxt]


def _alias(a: ast.alias) -> str:
    return f"{a.name} as {a.asname}" if a.asname else a.name


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _from_stmt(module: str, names: list[str], indent: str) -> str:
    if not module:  # moved to a top-level module: plain imports
        return "; ".join(f"import {n}" for n in names)
    one = f"from {module} import {', '.join(names)}"
    if len(indent) + len(one) <= _WRAP:
        return one
    body = "".join(f"{indent}    {n},\n" for n in names)
    return f"from {module} import (\n{body}{indent})"


def rewrite_source(source: str, old_mod: str, new_mod: str, is_init: bool,
                   moves: dict[str, str], modules: set[str]) -> Result:
    """`source` of module `old_mod` (now `new_mod`) with its imports pointing at the new places.

    `moves` maps old -> new dotted module names; `modules` is every module before the move.
    """
    tree = ast.parse(source)
    pos = _Positions(source)
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    chains: dict[str, str] = {}
    bound: set[str] = set()
    new_pkg = package_of(new_mod, is_init)

    def import_from(node: ast.ImportFrom) -> None:
        base = resolve(old_mod, is_init, node.level, node.module)
        if base is None:
            return
        groups: dict[str, list[str]] = {}
        renamed = False
        for a in node.names:
            full = f"{base}.{a.name}"
            if a.name != "*" and full in modules:          # `from pkg import submodule`
                key, _, last = moves.get(full, full).rpartition(".")
                name = a.asname or a.name
                text = last if last == name else f"{last} as {name}"
                renamed |= text != _alias(a)
            else:                                           # a name from module `base`
                key, text = moves.get(base, base), _alias(a)
            groups.setdefault(key, []).append(text)
        original = "." * node.level + (node.module or "")

        def spelled(key: str) -> str:
            if key == base and new_mod == old_mod:
                return original
            if node.level and key:
                return relative_text(new_pkg, key) or key
            return key

        if len(groups) == 1 and not renamed:
            module = spelled(next(iter(groups)))
            if module == original:
                return
            start, end = pos.span(node)
            m = _MODULE_TOKEN.match(source, start, end)
            if m:
                edits.append((m.start(2), m.end(2), module))
                return
        prefix = pos.line_prefix(node)
        alone = not prefix.strip() and pos.rest_of_end_line(node).strip()[:1] in ("", "#")
        indent = prefix if alone else ""
        stmts = [_from_stmt(spelled(k), names, indent) for k, names in groups.items()]
        edits.append((*pos.span(node), ("\n" + indent).join(stmts) if alone else "; ".join(stmts)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            parts, changed = [], False
            for a in node.names:
                bound.add(a.asname or a.name.split(".")[0])
                new = moves.get(a.name)
                if new is None:
                    parts.append(_alias(a))
                    continue
                changed = True
                if a.asname:
                    parts.append(f"{new} as {a.asname}")
                elif "." not in a.name:
                    parts.append(f"{new} as {a.name}")      # keep the bound name
                else:
                    parts.append(new)                       # binds the same top name; fix chains
                    chains[a.name] = new
            if changed:
                edits.append((*pos.span(node), "import " + ", ".join(parts)))
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names)
            import_from(node)

    if chains:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (d := _dotted(node)) in chains:
                edits.append((*pos.span(node), chains[d]))

    if is_init:
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            if not (isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                    and any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets)
                    and isinstance(node.value, (ast.List, ast.Tuple))):
                continue
            for el in node.value.elts:
                if not (isinstance(el, ast.Constant) and isinstance(el.value, str)):
                    continue
                new = moves.get(f"{old_mod}.{el.value}")
                if new is None or el.value in bound:
                    continue
                if new.startswith(old_mod + "."):
                    start, end = pos.span(el)
                    quote = source[start] if source[start] in "'\"" else '"'
                    edits.append((start, end, f"{quote}{new[len(old_mod) + 1:].split('.')[0]}{quote}"))
                else:
                    notes.append(f"__all__ lists {el.value!r}, which moved out of {old_mod} to {new}")

    out, last = source, None
    for start, end, text in sorted(edits, reverse=True):
        if last is not None and end > last:
            notes.append(f"overlapping edit at offset {start} skipped")
            continue
        out = out[:start] + text + out[end:]
        last = start
    return Result(out, len(edits), notes)
