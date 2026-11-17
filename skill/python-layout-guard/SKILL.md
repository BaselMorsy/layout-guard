---
name: python-layout-guard
description: Use when asked to assess, organise or clean up the layout of a Python repo (flat folders, too many modules in one package, utils/helpers modules), when starting a new Python project, when adding modules or packages to a project that has a layout.toml, or when tests/test_layout.py fails. Drives layout-guard init / check / plan / apply-plan to move modules into packages by responsibility with rewritten imports, one tested commit per group, on a branch.
---

# python-layout-guard: Python code organised in packages by responsibility

`layout-guard` checks a Python project against its `layout.toml` (top-level allow-list, at most
N modules per folder, no `utils`/`helpers`/`misc`/`common` modules, `__init__.py` in every
package, import direction between top-level packages). The safeguard is a test:
`tests/test_layout.py` runs the checker with the test suite. Exceptions exist only as
`[[allow]]` entries with a reason.

If `layout-guard` is not on PATH, use `uvx --from <layout-guard path or git url> layout-guard`
or `python -m layout_guard` from an environment where it is installed.

## Hard rules (always)

- Never add a top-level module or folder to make something fit; never raise a limit; never edit
  `layout.toml` to silence a violation (no new `[[allow]]`, no longer `top_level`). The only
  automatic edits are apply-plan's: deleting `[[allow]]` entries that became stale, and adding a
  new top-level package the agreed plan creates.
- Never work on `main`/`master`, never merge, never push, never force anything.
- When a decision is not mechanical (what a responsibility is, whether a module is an entry
  point, a layer to add), ask the user.

## "Assess and clean up this repo"

1. **Adopt.** No `layout.toml`? Run `layout-guard init` (`--profile python-src|python-flat-package`
   if detection is wrong; `--vendor` if the project must not depend on layout-guard). Every
   package becomes a `[[root]]`, `tests/` too; existing problems become `legacy` `[[allow]]`
   entries so `check` starts green. Read the generated `layout.toml` and fix the inference
   (roots that are missing, `[[where]]` rows saying what belongs where). Commit it on a branch:
   `git switch -c layout-cleanup`, then `git add -A && git commit -m "adopt layout-guard"`.
2. **Assess.** `layout-guard check` (green, with N legacy entries left) and
   `layout-guard explain`. Run the project's tests once to know the baseline.
3. **Plan.** `layout-guard plan` writes `layout-plan.toml` (the moves) and
   `layout-refactor-plan.md` (a summary). The groups come from file names only. **Read the code**
   (module docstrings, what imports what) and rewrite the plan into real responsibilities:
   - one `[[move]]` per module (`group`, `from`, `to`); a group is one commit; renaming is fine
     (`pkg/user_model.py` -> `pkg/user/model.py`); keep test file names unique;
   - a `[[new_package]]` with a one-line `docstring` for each package it creates;
   - a `[[group]]` `message` per group (the commit message);
   - modules in `unassigned` need a decision; `split_by_hand` lists catch-all modules to split by
     responsibility yourself (a normal code change, before or after the moves);
   - avoid package or module names of the standard library (`io`, `json`, `types`, ...): they
     are refused.
   Run `layout-guard plan` again to refresh the summary, show the user the groups, and commit
   the plan (apply-plan needs a clean tree).
4. **Dry run.** `layout-guard apply-plan --all --dry-run`: every group's moves, the files whose
   imports change, refusals, and string references to fix by hand.
5. **Apply, group by group.** `layout-guard apply-plan --group NAME` (or `--all`). The tests
   run before anything moves and after each group; the default command is detected
   (`uv run pytest -q` or `python -m pytest -q`); pass `--test-cmd "..."` if it is wrong.
   A group that fails `check` or the tests is rolled back completely (`git reset --hard` to the
   commit it started from) and apply-plan stops (`--keep-going` continues with the next).
6. **Handle failures.** Read the message:
   - *refused* (target exists, stdlib name, two moves to one target, imported by string, import
     cycle): change the plan (another target or name, another group), commit, retry;
   - *tests fail after the move*: usually a path computed from `__file__`, a `mock.patch` /
     `monkeypatch.setattr` string, `python -m old.module`, or an entry point. Make that code
     location-independent in its own commit (it must still pass before the move), then retry;
   - *check fails*: e.g. a new package that `[root.layers]` does not list: ask the user.
7. **Repeat** until `layout-guard check` reports no legacy entries left, or nothing more can be
   done safely.
8. **Summarise** for the user: groups applied (commits), groups rolled back or refused and why,
   the string references to fix by hand (file:line from the report: docs, mock targets, entry
   points in pyproject, alembic/config files), the legacy entries that remain and what they
   need, and the test results before and after. Leave the branch for the user to review.

## New project

Run `layout-guard init` before writing modules. Agree the generated `layout.toml` with the user
(top_level, `[[where]]`, optionally `[root.layers]`). Then put every new module in the package
from the "Where new code goes" table; when a package approaches the limit, split it by
responsibility (plan + apply-plan, or by hand with `git mv`) before adding more.

## Rules you will meet

- **top-level**: only `top_level` entries may sit directly in a root.
- **max-files**: at most `max_files_per_dir` modules per folder (`__init__.py` not counted).
- **forbidden-name**: name modules after their one responsibility (`retry.py`, not `utils.py`).
- **missing-init**: every package folder has an `__init__.py` with a one-line docstring.
- **layer**: `[root.layers]` says which top-level packages each may import (lazy and
  `TYPE_CHECKING` imports count). Move the shared code down a layer, or pass it in.
- **stale-allow**: an `[[allow]]` entry that matches nothing; delete it.

## Before finishing

Run `layout-guard check` and the tests. If something cannot be fixed now, tell the user which
violation remains and why.
