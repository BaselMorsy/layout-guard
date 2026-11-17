"""Import rewriting for moved modules (pure: source text in, source text out)."""
from __future__ import annotations

import textwrap

from layout_guard.refactor import modules
from layout_guard.refactor.rewrite import rewrite_source

MOVES = {"app.user_model": "app.user.model"}
MODULES = {"app", "app.user_model", "app.db", "app.service", "app.core", "app.core.x", "tests.test_user"}


def rw(src: str, mod: str = "app.service", new: str | None = None, is_init: bool = False, moves=MOVES):
    return rewrite_source(textwrap.dedent(src), mod, new or mod, is_init, moves, MODULES).text


def test_absolute_import_and_attribute_chains():
    out = rw("import app.user_model\n\nx = app.user_model.User(app.user_model.ID)\n")
    assert out == "import app.user.model\n\nx = app.user.model.User(app.user.model.ID)\n"


def test_import_as_alias():
    assert rw("import app.user_model as um\n") == "import app.user.model as um\n"


def test_from_module_import_name():
    assert rw("from app.user_model import User, ID  # keep me\n") == "from app.user.model import User, ID  # keep me\n"


def test_from_package_import_moved_module_keeps_the_bound_name():
    assert rw("from app import user_model\n") == "from app.user import model as user_model\n"
    assert rw("from app import user_model as m\n") == "from app.user import model as m\n"


def test_mixed_names_are_split_keeping_indentation():
    src = """\
    def f():
        from app import db, user_model
        return db, user_model
    """
    assert rw(src) == textwrap.dedent("""\
    def f():
        from app import db
        from app.user import model as user_model
        return db, user_model
    """)


def test_relative_import_from_an_importer_that_stays():
    assert rw("from .user_model import User\n") == "from .user.model import User\n"
    assert rw("from . import user_model\n") == "from .user import model as user_model\n"


def test_relative_imports_of_the_moved_module_are_recomputed():
    src = "from .db import Session\nfrom . import db\nfrom .core.x import y\nimport os\n"
    out = rw(src, mod="app.user_model", new="app.user.model")
    assert out == "from ..db import Session\nfrom .. import db\nfrom ..core.x import y\nimport os\n"


def test_init_reexport_and_dunder_all():
    src = 'from .user_model import User\n__all__ = ["User", "user_model"]\n'
    out = rw(src, mod="app", is_init=True)
    assert out == 'from .user.model import User\n__all__ = ["User", "user"]\n'
    # a bound name stays valid: the import keeps binding `user_model`
    src = 'from . import user_model\n__all__ = ["user_model"]\n'
    assert rw(src, mod="app", is_init=True) == 'from .user import model as user_model\n__all__ = ["user_model"]\n'


def test_tests_importing_moved_modules():
    src = "from app.user_model import User\nimport app.user_model\n"
    assert rw(src, mod="tests.test_user") == "from app.user.model import User\nimport app.user.model\n"


def test_top_level_module_moved_into_a_package():
    moves = {"server": "app.server"}
    assert rw("import server\nserver.run()\n", moves=moves) == "import app.server as server\nserver.run()\n"
    assert rw("from server import run\n", moves=moves) == "from app.server import run\n"


def test_parenthesized_multiline_keeps_formatting():
    src = "from app.user_model import (\n    User,  # the model\n    ID,\n)\n"
    assert rw(src) == "from app.user.model import (\n    User,  # the model\n    ID,\n)\n"


def test_type_checking_and_lazy_imports_are_rewritten():
    src = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.user_model import User\n"
    assert "from app.user.model import User" in rw(src)


def test_unrelated_files_are_untouched_byte_for_byte():
    src = "from .db import x   # odd spacing\nimport app.db\nfrom ..app import  y\n"
    r = rewrite_source(src, "app.service", "app.service", False, MOVES, MODULES)
    assert r.text == src and r.edits == 0


def test_crlf_and_unicode_are_preserved():
    src = 's = "é"; from app.user_model import User\r\nt = 1\r\n'
    assert rw(src) == 's = "é"; from app.user.model import User\r\nt = 1\r\n'


def test_module_names_and_relative_spelling():
    assert modules.module_of("src/app/core/x.py", ["src", ""]) == "app.core.x"
    assert modules.module_of("src/app/__init__.py", ["src", ""]) == "app"
    assert modules.module_of("tests/test_a.py", ["src", ""]) == "tests.test_a"
    assert modules.relative_text("app.user", "app.core.x") == "..core.x"
    assert modules.relative_text("app", "app") == "."
    assert modules.relative_text("app", "other.x") is None
    assert modules.resolve("app.a", False, 2, "x") is None  # climbs above the top package
