"""The checker: layout.toml validation and every rule over a whole project."""
from __future__ import annotations

import pytest

from layout_guard.checker import ConfigError, check_project, load
from tests.conftest import BASIC_TOML, CLEAN


def rules_of(proj):
    return sorted((v.rule, v.path) for v in check_project(load(proj / "layout.toml")))


def write(tmp_path, text):
    p = tmp_path / "layout.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults(tmp_path):
    cfg = load(write(tmp_path, 'version = 1\n[[root]]\npath = "src/pkg"\n'))
    [r] = cfg.roots
    assert r.max_files == 10 and r.require_init and r.package == "pkg" and r.top_level == ()
    assert cfg.forbidden_names == ("utils", "helpers", "misc", "common", "stuff", "tmp")


def test_defaults_section_applies_to_roots(tmp_path):
    cfg = load(write(tmp_path, '[defaults]\nmax_files_per_dir = 7\nforbidden_names = ["Junk"]\n'
                               '[[root]]\npath = "src"\n'))
    assert cfg.roots[0].max_files == 7 and cfg.forbidden_names == ("junk",)


@pytest.mark.parametrize("text, needle", [
    ('[[root]]\npath = "src"\nlanguage = "python"\n', "unknown key"),   # Python only: no language
    ('version = 1\n', "at least one [[root]]"),
    ('[[root]]\npath = "src"\n[[allow]]\nrule = "layer"\nfrom = "a"\nto = "b"\n', "reason"),
    ('[[root]]\npath = "src"\n[[allow]]\nrule = "ignore-next"\npath = "x"\nreason = "r"\n', "rule"),
    ('[[root]]\npath = "src"\n[[allow]]\nrule = "top-level"\npath = "x"\nmax = 3\nreason = "r"\n', "max"),
    ('[[root\n', "not valid TOML"),
])
def test_invalid_configs_explain_themselves(tmp_path, text, needle):
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text))
    assert needle in str(exc.value)


def test_clean_project_passes(clean_project):
    assert rules_of(clean_project) == []


def test_top_level_file_not_in_allow_list(make_project):
    assert ("top-level", "src/app/extra.py") in rules_of(make_project({**CLEAN, "src/app/extra.py": ""}))


def test_top_level_dirs_only_by_pattern(make_project):
    toml = BASIC_TOML.replace('"*/"', '"core/", "domain/", "adapters/"')
    proj = make_project({**CLEAN, "src/app/extra/__init__.py": ""}, toml)
    assert rules_of(proj) == [("top-level", "src/app/extra")]


def test_max_files_excludes_init_and_non_python(make_project):
    files = {**CLEAN, "src/app/core/a.py": "", "src/app/core/b.py": "", "src/app/core/x.json": ""}
    assert rules_of(make_project(files)) == []  # 3 modules + __init__.py: at the limit
    files["src/app/core/c.py"] = ""
    assert rules_of(make_project(files)) == [("max-files", "src/app/core")]


def test_forbidden_names_file_and_folder(make_project):
    proj = make_project({**CLEAN, "src/app/core/utils.py": "", "src/app/domain/Helpers/__init__.py": ""})
    assert rules_of(proj) == [("forbidden-name", "src/app/core/utils.py"),
                              ("forbidden-name", "src/app/domain/Helpers")]


def test_missing_init_including_parent_chain(make_project):
    proj = make_project({**CLEAN, "src/app/core/sub/deeper/x.py": ""})
    assert rules_of(proj) == [("missing-init", "src/app/core/sub"), ("missing-init", "src/app/core/sub/deeper")]


def test_require_init_false_and_data_folders(make_project):
    toml = 'version = 1\n[[root]]\npath = "tests"\nrequire_init = false\n'
    assert rules_of(make_project({"tests/test_a.py": "", "tests/sub/test_b.py": ""}, toml)) == []
    assert rules_of(make_project({**CLEAN, "src/app/core/assets/a.md": ""})) == []


def test_layer_violation_module_level_lazy_and_type_checking(make_project):
    proj = make_project({**CLEAN, "src/app/core/models.py": """\
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from app.domain.rules import R
        def f():
            import app.adapters.cli
        """})
    vs = check_project(load(proj / "layout.toml"))
    assert sorted(v.edge for v in vs) == [("app.core.models", "app.adapters.cli"), ("app.core.models", "app.domain.rules")]


def test_relative_imports_resolve(make_project):
    proj = make_project({**CLEAN, "src/app/domain/rules.py": "from ..adapters import cli\n"})
    [v] = check_project(load(proj / "layout.toml"))
    assert v.edge == ("app.domain.rules", "app.adapters")


def test_unknown_layer(make_project):
    proj = make_project({**CLEAN, "src/app/newpkg/__init__.py": "", "src/app/newpkg/x.py": "import app.core\n"})
    [v] = check_project(load(proj / "layout.toml"))
    assert v.rule == "layer" and "not a layer" in v.message


def test_allow_entries_suppress_and_stale_ones_fail(make_project):
    toml = BASIC_TOML + """
[[allow]]
rule = "layer"
from = "app.core.models"
to = "app.domain.rules"
reason = "legacy, fix by moving R into core"

[[allow]]
rule = "forbidden-name"
path = "src/app/core/common.py"
reason = "vendored name"

[[allow]]
rule = "max-files"
path = "src/app/nothing"
reason = "stale"
"""
    proj = make_project({**CLEAN, "src/app/core/models.py": "from app.domain.rules import R\n",
                         "src/app/core/common.py": ""}, toml)
    assert rules_of(proj) == [("stale-allow", "layout.toml")]


def test_max_files_allow_is_a_ratchet(make_project):
    toml = BASIC_TOML + '\n[[allow]]\nrule = "max-files"\npath = "src/app/core"\nmax = 4\nreason = "legacy"\n'
    files = {**CLEAN, **{f"src/app/core/m{i}.py": "" for i in range(3)}}
    assert rules_of(make_project(files, toml)) == []
    files["src/app/core/m9.py"] = ""
    assert rules_of(make_project(files, toml)) == [("max-files", "src/app/core"), ("stale-allow", "layout.toml")]


def test_ignored_entries(make_project):
    files = {**CLEAN, "src/app/__pycache__/x.pyc": "", "src/app/.hidden/utils.py": ""}
    assert rules_of(make_project(files)) == []


def test_no_inline_escape_hatch_exists(make_project):
    proj = make_project({**CLEAN, "src/app/core/models.py": "# layout-guard: ignore-next\nimport app.adapters.cli\n"})
    assert [v.rule for v in check_project(load(proj / "layout.toml"))] == ["layer"]
