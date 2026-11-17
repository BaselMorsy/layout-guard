# Agent instructions

## Project layout (enforced by layout-guard)

The layout of this project is declared in `layout.toml`. The test
`tests/cli/test_cli_commands.py::test_layout_guard_obeys_its_own_layout` runs the checker on
this repo and fails with a fix hint per violation.
Run `layout-guard check` (or the tests) before you finish. `layout-guard explain` prints
the current rules (layout.toml wins over this file).

### Rules

- src/layout_guard/: top level only __init__.py, __main__.py, cli.py, checker.py, scaffold/, refactor/, install/; max 10 Python files per folder; every package folder has __init__.py
- src/layout_guard/ layers (may import): checker -> nothing; scaffold -> checker, __init__; refactor -> checker, scaffold; install -> nothing; cli -> *; __main__ -> *
- tests/: top level only __init__.py, conftest.py, checker/, scaffold/, refactor/, install/, cli/; max 10 Python files per folder; every package folder has __init__.py
- forbidden catch-all names: utils, helpers, misc, common, stuff, tmp
- Exceptions exist only as `[[allow]]` entries in `layout.toml`, each with a reason, agreed
  with the user. There is no inline ignore comment.

### Where new code goes

| You are adding | Put it in |
|---|---|
| A layout.toml key, a rule, or how rules are reported (stdlib only, no layout_guard imports) | `checker.py` |
| Something `init` generates (layout.toml, the layout test, AGENTS.md, CI snippets) | `scaffold/` |
| Planning or applying moves (plan file, grouping, import rewriting, git transaction) | `refactor/` |
| Installing the skill, doctor | `install/` |
| A new subcommand or flag | `cli.py` |
| A test | `tests/<package>/ mirroring src/layout_guard/` |

If nothing in the table fits, stop and ask the user. Exceptions exist only as [[allow]] entries in layout.toml that the user agrees to; do not edit layout.toml to make this pass.

### How to work

1. Read `layout.toml` and this file before adding source files.
2. Pick the package from the table above. Never add a top-level module or folder to make
   something fit, never raise a limit, never edit layout.toml to silence a violation; ask
   the user instead.
3. Split a folder by responsibility before it reaches the file limit: `layout-guard plan`
   proposes groups in layout-plan.toml, `layout-guard apply-plan` moves them on a branch
   and rewrites the imports, one tested commit per group.
4. Name modules and folders after their one responsibility (no utils, helpers, misc, common).
