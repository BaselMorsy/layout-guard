"""Find string references to moved modules: reported, never edited.

In Python files only string constants are searched (mock.patch targets, `python -m x`, docs);
`importlib.import_module("x")` / `__import__("x")` count as imports by string, which make
apply-plan refuse the group. Config files (pyproject entry points, alembic.ini, YAML, ...) are
searched line by line.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

TEXT_SUFFIXES = (".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".txt", ".env", ".mako", ".py")
TEXT_NAMES = ("Dockerfile", "Makefile", "Procfile")
SKIP = ("layout.toml", "layout-plan.toml")
_IMPORTERS = ("import_module", "__import__", "find_spec", "import_string")


@dataclass(frozen=True)
class Ref:
    path: str
    line: int
    old: str          # the old module name (or file path) referenced
    by_import: bool   # importlib.import_module / __import__ / ...

    def render(self) -> str:
        kind = "imports by string" if self.by_import else "string reference to"
        return f"{self.path}:{self.line}: {kind} {self.old} (fix by hand)"


def is_text(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name not in SKIP and (name.endswith(TEXT_SUFFIXES) or name in TEXT_NAMES)


def _patterns(olds: dict[str, str]) -> list[tuple[str, re.Pattern]]:
    """old module -> its old file path; both spellings are searched."""
    out = []
    for mod, path in olds.items():
        # a one-word module (`server`) is only a reference as `server.x` / `server:app` / "server"
        tail = r"(?![\w])" if "." in mod else r"(?=[.:]\w|$)"
        out.append((mod, re.compile(rf"(?<![\w./-]){re.escape(mod)}{tail}" if "." in mod
                                    else rf"(?:^|(?<=[\s\"'=]))(?<![\w./-]){re.escape(mod)}{tail}")))
        out.append((path, re.compile(rf"(?<![\w/.]){re.escape(path)}(?![\w])")))
    return out


def _callee(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def scan(path: str, text: str, olds: dict[str, str]) -> list[Ref]:
    pats = _patterns(olds)
    refs: list[Ref] = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return refs
        by_import = {id(n.args[0]) for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and n.args and _callee(n) in _IMPORTERS}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for old, pat in pats:
                    for m in pat.finditer(node.value):
                        line = node.lineno + node.value[:m.start()].count("\n")
                        refs.append(Ref(path, line, old, id(node) in by_import))
        return sorted(set(refs), key=lambda r: (r.path, r.line, r.old))
    for i, line in enumerate(text.splitlines(), 1):
        refs += [Ref(path, i, old, False) for old, pat in pats if pat.search(line)]
    return refs
