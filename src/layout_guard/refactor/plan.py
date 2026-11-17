"""`layout-guard plan`: propose moves for the folders `check` flags (legacy entries included).

Grouping is a name heuristic: modules sharing a first word (`user_model.py`, `user_service.py`
-> `user/`), else a last word (`stripe_client.py`, `s3_client.py` -> `client/`); a `test_` prefix
is ignored. Modules that share no word with another are left `unassigned`, not guessed. The plan
is only a starting point: refine it to real responsibilities before applying it.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from layout_guard import checker
from layout_guard.refactor.planfile import Move, Plan
from layout_guard.scaffold import templates

# never planned: package markers, pytest's conftest, and the files `init` writes
KEEP = ("__init__.py", "__main__.py", "conftest.py", templates.TEST_NAME, templates.VENDOR_NAME)
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def words(filename: str) -> list[str]:
    w = [x.lower() for x in _WORD.findall(filename.split(".")[0].replace("-", "_"))]
    return w[1:] if w[:1] == ["test"] and len(w) > 1 else w


def group_files(files: list[str], forbidden: tuple[str, ...], existing_dirs: tuple[str, ...] = ()
                ) -> tuple[dict[str, str], list[str], list[str]]:
    """(file -> group word, unassigned files, catch-all files) for the modules of one folder."""
    moves, unassigned, catch_all, candidates = {}, [], [], []
    for f in sorted(files):
        (catch_all if f.split(".")[0].lower() in forbidden else candidates).append(f)
    firsts = Counter(w[0] for w in map(words, candidates) if w)
    lasts = Counter(w[-1] for w in map(words, candidates) if len(w) > 1)
    for f in candidates:
        w = words(f)
        key = None
        if w and (w[0] in existing_dirs or firsts[w[0]] >= 2):
            key = w[0]
        elif len(w) > 1 and lasts[w[-1]] >= 2:
            key = w[-1]
        if key and key not in forbidden:
            moves[f] = key
        else:
            unassigned.append(f)
    return moves, unassigned, catch_all


def target_name(filename: str, key: str) -> str:
    """`user_model.py` in `user/` -> `model.py`; test modules keep their name (unique basenames)."""
    if filename.startswith("test_"):
        return filename
    parts = filename[:-3].split("_")
    for i in (0, len(parts) - 1):
        if parts[i].lower() == key and len(parts) > 1:
            return "_".join(parts[:i] + parts[i + 1:]) + ".py"
    return filename


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder and folder != "." else name


def make_plan(cfg: checker.Config) -> Plan:
    project = cfg.project_dir
    roots = {r.path: r for r in cfg.roots}
    folders: dict[str, set[str]] = {}
    plan = Plan()
    for v in checker.findings(cfg):
        if v.rule == "max-files":
            # a root's own top folder: only the modules top_level does not allow may move
            root = roots.get(v.path)
            if root is None or not root.top_level:
                folders.setdefault(v.path, set()).update(
                    p.name for p in (project / v.path).glob("*.py"))
        elif v.rule == "top-level" and v.path.endswith(".py"):
            folder, _, name = v.path.rpartition("/")
            folders.setdefault(folder, set()).add(name)
        elif v.rule == "forbidden-name" and v.path.endswith(".py"):
            plan.split_by_hand.append(v.path)
    taken: set[str] = set()
    for folder in sorted(folders):
        d = project / folder
        dirs = tuple(p.name for p in d.iterdir() if p.is_dir() and (p / "__init__.py").is_file())
        files = sorted(f for f in folders[folder] if f not in KEEP)
        moves, unassigned, _ = group_files(files, cfg.forbidden_names, dirs)
        plan.unassigned += [_join(folder, f) for f in unassigned]
        for key in sorted(set(moves.values())):
            name = key if key not in taken else f"{Path(folder).name}-{key}"
            taken.add(name)
            members = sorted(f for f, k in moves.items() if k == key)
            pkg = _join(folder, key)
            if not (project / pkg / "__init__.py").is_file():
                stems = ", ".join(target_name(f, key)[:-3] for f in members)
                plan.packages[pkg] = f"{key.capitalize()}: {stems}."
            plan.moves += [Move(name, _join(folder, f), f"{pkg}/{target_name(f, key)}") for f in members]
    plan.split_by_hand.sort()
    return plan
