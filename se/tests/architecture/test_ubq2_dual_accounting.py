from __future__ import annotations

import ast
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
    assert script.get_heads() == ["27a_cas_f7_t_tool_media_projection"]
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

    # Structural R8/R12 control-plane operations may be incarnation-fenced,
    # but they must never be routed through the UBQ resource mirror.
    structural_methods = (
        "start_root_task_scoped_execution",
        "resume_task_scoped_execution",
        "finish_task_scoped_execution",
        "recover_task_scoped_execution",
        "_transition_execution_with_budget",
    )
    for method in structural_methods:
        marker = f"async def {method}("
        start = source.index(marker)
        end = source.find("\n    async def ", start + len(marker))
        body = source[start : end if end != -1 else len(source)]
        assert "mirror_resource_in_uow" not in body
        assert "_mutate_enrolled_resource(" not in body


def test_ubq2_all_task_budget_replay_and_cas_calls_are_incarnation_bound() -> None:
    source = _text("se/src/runtimes/agent/task_budget.py")
    tree = ast.parse(source)

    guarded = {
        "get_task_budget_reservation",
        "compare_and_set_task_budget",
    }
    missing: list[tuple[str, int]] = []
    reservation_writes_missing_generation: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr in guarded:
            keywords = {
                item.arg for item in node.keywords if item.arg is not None
            }
            if "expected_incarnation_generation" not in keywords:
                missing.append((func.attr, node.lineno))
        elif func.attr == "save_task_budget_reservation":
            if not node.args or not isinstance(node.args[0], ast.Dict):
                reservation_writes_missing_generation.append(node.lineno)
                continue
            keys = {
                item.value
                for item in node.args[0].keys
                if isinstance(item, ast.Constant)
                and isinstance(item.value, str)
            }
            if "task_budget_incarnation_generation" not in keys:
                reservation_writes_missing_generation.append(node.lineno)

    assert missing == [], (
        "Every TaskBudget reservation replay/CAS path must carry the exact "
        f"incarnation generation; missing={missing}"
    )
    assert reservation_writes_missing_generation == [], (
        "Every TaskBudget reservation write must persist exact source "
        "incarnation generation; missing lines="
        f"{reservation_writes_missing_generation}"
    )

def test_ubq2_postgresql_v7_parity_has_an_explicit_deployment_gate() -> None:
    workflow = _text(".github/workflows/architecture-baseline.yml").lower()
    requirements = _text("requirements-test.txt").lower()
    gate = _text(
        "docs/user_budget_quota/UBQ_2_POSTGRESQL_V7_EVIDENCE_GATE.md"
    )

    # Current CI has no executable PostgreSQL fixture/service. If one is
    # introduced, this assertion forces the explicit gate to be revisited
    # rather than silently treating SQLite as cross-dialect proof.
    assert "postgres:" not in workflow
    assert "asyncpg" not in requirements

    required_evidence = (
        "OPEN / REQUIRED BEFORE ANY POSTGRESQL 26a / UBQ-2 RUNTIME DEPLOYMENT",
        "26a_ubq2_dual_accounting_bridge",
        "does not satisfy this gate",
        "MUST NOT apply 26a",
        "monotonic allocator serialization",
        "MAX_INT64-1",
        "MAX_INT64",
        "rollback of an uncommitted final allocation",
        "(task_id, incarnation_generation)",
        "parent TaskBudget delete is RESTRICTED",
        "anti-delete/anti-reset/monotonicity",
        "never reused after source GC/recreate",
        "user_budget.dual_accounting.enabled = false",
        "does not waive either",
        "TaskBudget schema/runtime semantics are unconditional",
    )
    for item in required_evidence:
        assert item in gate

