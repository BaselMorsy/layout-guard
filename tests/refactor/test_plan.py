"""`plan`: the grouping heuristic, layout-plan.toml round trip, and the generated summary."""
from __future__ import annotations

import pytest

from layout_guard import checker
from layout_guard.refactor import planfile
from layout_guard.refactor.plan import group_files, make_plan, target_name, words
from layout_guard.scaffold.project_init import init
from tests.conftest import write_tree

FLAT = {
    "pyproject.toml": '[project]\nname = "flatapp"\n',
    "src/flatapp/__init__.py": "",
    "src/flatapp/__main__.py": "",
    "src/flatapp/user_model.py": "",
    "src/flatapp/user_service.py": "",
    "src/flatapp/user_routes.py": "",
    "src/flatapp/order_model.py": "",
    "src/flatapp/order_service.py": "",
    "src/flatapp/stripe_client.py": "",
    "src/flatapp/s3_client.py": "",
    "src/flatapp/utils.py": "",
    "src/flatapp/report.py": "",
    "src/flatapp/settings.py": "",
    "src/flatapp/cache.py": "",
}


def test_grouping_heuristic():
    moves, unassigned, catch_all = group_files(["a_b.py", "a_c.py", "z_c.py", "lonely.py", "helpers.py"], ("helpers",))
    assert moves == {"a_b.py": "a", "a_c.py": "a", "z_c.py": "c"}
    assert unassigned == ["lonely.py"] and catch_all == ["helpers.py"]
    assert words("userProfileCard.py") == ["user", "profile", "card"]
    assert words("test_extraction_cli.py") == ["extraction", "cli"]


def test_target_names():
    assert target_name("user_model.py", "user") == "model.py"
    assert target_name("stripe_client.py", "client") == "stripe.py"
    assert target_name("user.py", "user") == "user.py"
    assert target_name("test_user_model.py", "user") == "test_user_model.py"  # unique test basenames


def test_plan_from_a_flat_project(tmp_path):
    write_tree(tmp_path, FLAT)
    init(tmp_path)
    p = make_plan(checker.load(tmp_path / "layout.toml"))  # legacy entries are planned too
    moves = {m.src: m.dst for m in p.moves}
    assert moves["src/flatapp/user_model.py"] == "src/flatapp/user/model.py"
    assert moves["src/flatapp/stripe_client.py"] == "src/flatapp/client/stripe.py"
    assert p.groups() == ["client", "order", "user"]
    assert "src/flatapp/cache.py" in p.unassigned and p.split_by_hand == ["src/flatapp/utils.py"]
    assert p.packages["src/flatapp/user"].startswith("User:")


def test_plan_file_round_trip_and_summary():
    p = planfile.Plan(moves=[planfile.Move("user", "pkg/user_model.py", "pkg/user/model.py")],
                      packages={"pkg/user": "User accounts."}, messages={"user": "layout: users"},
                      unassigned=["pkg/odd.py"])
    text = planfile.render(p)
    import tomllib
    back = planfile.parse(tomllib.loads(text))
    assert back.moves == p.moves and back.packages == p.packages and back.message("user") == "layout: users"
    md = planfile.summary(back)
    assert "`pkg/user_model.py` | `pkg/user/model.py`" in md and "pkg/odd.py" in md


@pytest.mark.parametrize("data, needle", [
    ({"move": [{"group": "g", "from": "a.py"}]}, "to ="),
    ({"move": [{"group": "g", "from": "a.py", "to": "b/"}]}, ".py"),
    ({"move": [], "group": [{"name": "x"}]}, "without any [[move]]"),
    ({"moves": []}, "unknown key"),
])
def test_invalid_plans(data, needle):
    with pytest.raises(planfile.PlanError) as exc:
        planfile.parse(data)
    assert needle in str(exc.value)
