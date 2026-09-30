from __future__ import annotations

from pathlib import Path

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[3]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ubq2_scope_and_safe_default_are_frozen() -> None:
    config = yaml.safe_load(_text("se/config/default.yaml"))
    dual = config["user_budget"]["dual_accounting"]
    assert dual == {
        "enabled": False,
        "policy_version": "ubq2-shadow-v1",
        "window_duration_seconds": 86400,
    }

    application = _text("se/src/application/user_budget.py")
    assert "source_payload_json" not in application
    assert "source_projection_json" not in application
    assert "arguments" not in application
    assert "RELEASE_EXECUTION" not in application
    assert "RESUME_EXECUTION" not in application
    assert "NEW_EXECUTION" not in application
    assert "RELEASE_BRANCH" not in application

    gc_executor = _text("se/src/runtimes/agent/gc_executor.py")
    gc_dry_run = _text("se/src/runtimes/agent/gc_dry_run.py")
    sqlite_driver = _text("se/src/infrastructure/storage/drivers/sqlite/driver.py")
    assert "UserBudget" not in gc_executor
    assert "user_budget" not in gc_executor
    assert "UserBudget" not in gc_dry_run
    assert "user_budget" not in gc_dry_run
    assert "UBQ" not in sqlite_driver
    assert "user_budget" not in sqlite_driver


def test_ubq2_is_single_linear_alembic_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["26a_ubq2_dual_accounting_bridge"]
    assert (
        script.get_revision("26a_ubq2_dual_accounting_bridge").down_revision
        == "25a_ubq1_user_budget_foundation"
    )


def test_ubq2_runtime_mirror_is_resource_only() -> None:
    source = _text("se/src/runtimes/agent/task_budget.py")
    assert "async def _mutate_enrolled_resource(" in source
    assert "async def reserve_inference(" in source
    assert "async def account_usage(" in source
    assert "async def reserve_tool_call_batch(" in source
    assert "USER_BUDGET_UNSUPPORTED_LEGACY_MIRROR" in source

    # Structural R12/R8 reservations still call the generic TaskBudget
    # reservation core and never name the UBQ mirror primitive.
    structural = source[
        source.index("async def reserve_new_execution("):
        source.index("async def reserve_tool_calls(")
    ]
    assert "mirror_resource_in_uow" not in structural
