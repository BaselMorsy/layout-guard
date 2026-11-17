# layout-guard

Keeps **Python** project layouts organised, for people and for coding agents. A project declares
its layout in a small `layout.toml`; a stdlib-only checker enforces it **as a test**
(`tests/test_layout.py`); and `plan` / `apply-plan` clean up an existing flat project by moving
modules into packages, rewriting the imports, and committing one tested group at a time on a
branch.

Scope: Python only (3.12+ to run it; the projects it checks can be any Python 3). No hooks:
layout-guard installs nothing into Claude Code's `settings.json` and never blocks a tool call.
The safeguard is the layout test plus, optionally, pre-commit / CI. Zero runtime dependencies
(tomllib, ast, pathlib, subprocess for git).

The rules are the ones a typical Python architecture test enforces, made portable:

- only allow-listed entries at the top of a source root (`top_level`);
- at most N Python files per folder (default 10, `__init__.py` not counted);
- every package folder has an `__init__.py`;
- no catch-all `utils` / `helpers` / `misc` / `common` / `stuff` / `tmp` modules or folders;
- import direction between a root's top-level packages (`[root.layers]`; lazy and
  `TYPE_CHECKING` imports count);
- a "where new code goes" table, printed with every violation.

Exceptions exist only as `[[allow]]` entries in `layout.toml`, each with a reason. An entry that
no longer matches anything fails the check (`stale-allow`). There is no inline `# ignore`.

## Install

```sh
uv tool install .                 # from this folder, or: uv tool install <git url>
layout-guard --help

# optional: the Claude Code skill (copies one folder; nothing else is touched)
layout-guard install-skill --dry-run        # default dir: $CLAUDE_CONFIG_DIR or ~/.claude
layout-guard install-skill                  # -> <claude-dir>/skills/python-layout-guard/
layout-guard doctor                         # python, git, CLI on PATH, skill up to date
layout-guard uninstall-skill
```

## New project

```sh
cd my-project
layout-guard init            # layout.toml, tests/test_layout.py, AGENTS.md, CLAUDE.md (never overwrites)
layout-guard explain         # the rules and the decision table
layout-guard check           # exit 1 with a fix hint per violation
```

`init` options: `--profile python-src|python-flat-package` (detected), `--vendor`,
`--pre-commit` (writes `.pre-commit-config.yaml`), `--github-action`
(`.github/workflows/layout-guard.yml`). Every package found becomes a `[[root]]` (`src/<pkg>`
for the src layout, every top-level package for a flat layout), plus `tests/`. Alembic-style
folders (`env.py` + `script.py.mako`) are added to `[defaults] ignore`, since they are scripts,
not packages.

### How the layout test gets the checker

`tests/test_layout.py` loads the checker from
`tests/layout_guard_checker.py` if that file exists, else imports the installed `layout_guard`.
If neither is available it **fails** with the instruction to add layout-guard as a dev
dependency or run `layout-guard init --vendor`; it never skips silently.

- **Dependency (default):** add layout-guard to the dev dependencies of the project.
- **Vendored (`init --vendor`):** copies `checker.py`, one stdlib-only file with no other
  layout_guard import, to `tests/layout_guard_checker.py`, so the test runs with no extra
  dependency. To update it, delete the file and run `init --vendor` again.

## Existing flat project: assess and clean up

```sh
git switch -c layout-cleanup
layout-guard init                           # today's problems -> legacy [[allow]] entries; check is green
git add -A && git commit -m "adopt layout-guard"
layout-guard plan                           # layout-plan.toml (editable) + layout-refactor-plan.md
#   ...edit layout-plan.toml: real responsibility groups, names, docstrings...
layout-guard plan                           # refresh the summary from the edited plan
git commit -am "layout plan"
layout-guard apply-plan --all --dry-run     # what each group would do, refusals, string references
layout-guard apply-plan --group model       # or --all
layout-guard check                          # "N legacy [[allow]] entries left"
```

With the skill installed, telling a Claude Code session *"use the python-layout-guard skill to
assess and clean up this repo"* runs this flow, including refining the groups by reading the
code and handling rollbacks; it never merges and never touches main.

### layout-plan.toml

```toml
version = 1
unassigned = ["pkg/odd.py"]          # no obvious group: decide by hand (informational)
split_by_hand = ["pkg/utils.py"]     # catch-all modules: split by responsibility by hand

[[group]]                            # optional: the commit message of a group
name = "user"
message = "layout: move the user modules into pkg/user"

[[new_package]]                      # optional: docstring of an __init__.py the group creates
path = "pkg/user"
docstring = "User accounts: the model, its service and its HTTP routes."

[[move]]                             # one per module; `group` ties moves into one commit
group = "user"
from = "pkg/user_model.py"
to = "pkg/user/model.py"             # renaming is allowed
```

`plan` proposes groups from file names only (shared first or last word, a `test_` prefix
ignored; test modules keep their names so basenames stay unique). It never plans `__init__.py`,
`__main__.py`, `conftest.py` or the files `init` writes. An existing `layout-plan.toml` is never
overwritten (`--overwrite` regenerates it).

### apply-plan

`layout-guard apply-plan [PATH] (--group NAME | --all) [--test-cmd CMD] [--no-tests-ok]
[--dry-run] [--no-commit] [--stop-on-failure | --keep-going]`

Preconditions (otherwise exit 2, nothing changed): a git work tree; a clean tree (untracked files
count; commit the plan first); a branch that is not `main`/`master` (create one); tests that
pass. The test command defaults to `uv run pytest -q` when there is a `uv.lock`, else
`<.venv python> -m pytest -q`, else `<this python> -m pytest -q`. A project without any
`test_*.py` needs `--test-cmd` or `--no-tests-ok`.

Per group, in plan order:

1. **Refuse** (nothing touched) when: a target exists; two moves target the same file; the target
   or a new package is named like a standard-library module; a new package would collide with a
   module of the same name (or the reverse); the source is `__init__.py`/`__main__.py` or not
   tracked; a moved module is imported by string (`importlib.import_module`, `__import__`, ...);
   the move creates a new import cycle (module-level imports, including the implicit import of
   each parent package's `__init__.py`).
2. Create the new packages (`__init__.py` with the plan's docstring), `git mv` each module, and
   rewrite the imports of every tracked Python file (sources, tests, scripts).
3. A new top-level package of a root that has a `top_level` list is added to it (the plan
   created it); nothing else in `layout.toml` changes except step 5.
4. Run the checker: any violation that was not there before fails the group.
5. Delete the `[[allow]]` entries that matched something before the group and nothing after
   (only those).
6. Run the tests. On success commit (`git commit`, the plan's message, hooks run); on any failure
   `git reset --hard` to the group's starting commit, remove the folders it created, and report
   why. Default: stop at the first failed group; `--keep-going` continues.

A group whose moves are all done is skipped, so re-running is safe. `--no-commit` applies one
group, leaves it staged and stops. `--dry-run` reports moves, rewritten files, refusals and
warnings without touching anything. apply-plan never pushes, never merges, never uses force.

**Import rewriting** (stdlib `ast` + position edits; comments, formatting and line endings
survive): `import a.b` (and the `a.b.x` attribute chains that use it), `import a.b as m`,
`from a.b import c`, `from a import b` where `b` is a moved module (becomes
`from a.new import b2 as b`, so the bound name never changes), mixed lists (split in two),
relative imports (recomputed for the moved file and for importers), `__init__.py` re-exports,
and `__all__` entries that name a moved submodule. **Reported, not edited** (file:line): string
references to the old module or file path: `mock.patch` / `monkeypatch.setattr` targets,
`python -m old.mod`, docstrings, entry points in `pyproject.toml`, `alembic.ini`, YAML/JSON/INI.
Not detected: paths computed from `__file__` (the tests catch those, and the group rolls back).

## layout.toml reference

```toml
version = 1

[defaults]                          # optional
max_files_per_dir = 10              # per folder; default 10
forbidden_names = ["utils", "helpers", "misc", "common", "stuff", "tmp"]   # case-insensitive
ignore = ["__pycache__", "*.pyc", ".*", "*.egg-info"]   # entry names never governed

[[root]]                            # one per package (and tests/); at least one
path = "src/app"                    # relative to layout.toml
top_level = ["__init__.py", "__main__.py", "config.py", "core/", "domain/"]
                                    # "name/" = folder, "name" = file, globs ok; omitted = off
# max_files_per_dir = 10            # per-root override
# require_init = true               # default true
# package = "app"                   # import name; default: the folder name

[root.layers]                       # belongs to the [[root]] above it
core = []                           # a layer may always import itself
domain = ["core"]
app = ["*"]                         # "*" = anything (the composition root)
"__main__" = ["*"]

[[where]]                           # printed with every violation and in AGENTS.md
adding = "A persisted data model"
put_in = "core/models.py"

[[allow]]                           # the only way to make an exception
rule = "layer"                      # top-level | max-files | forbidden-name | missing-init | layer
from = "app.core.state"             # layer: module globs
to = "app.domain.criteria"
reason = "why it is tolerated and how it will be fixed"   # required

[[allow]]
rule = "max-files"
path = "src/app/legacy"             # other rules: project-relative path glob
max = 14                            # max-files only: a ceiling (a ratchet)
reason = "legacy"
```

Unknown keys are errors (a typo must not silently disable a rule). Only `.py` files and folders
are governed. `check --json` prints machine-readable violations. `check PATH --config FILE`
applies a layout.toml that lives elsewhere, root paths relative to `PATH`.

## Other agents and CI

- `AGENTS.md` (written by `init`) gives any agent the rules and the workflow; `CLAUDE.md` imports
  it with `@AGENTS.md`.
- `layout-guard check` exit codes: 0 ok, 1 violations, 2 usage/config error.
- `init --pre-commit` / `init --github-action` write configs that run `layout-guard check`.
  Existing files are never overwritten; the snippet is printed instead.

## Development

```sh
uv sync
uv run pytest
```

The repo obeys its own `layout.toml` (a test runs `check` on it). Design notes:
[docs/design.md](docs/design.md).
