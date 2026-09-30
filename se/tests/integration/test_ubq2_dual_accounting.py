from __future__ import annotations

import asyncio
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.application.user_budget import (
    DualAccountingSettings,
    UserBudgetDualAccountingService,
    UserBudgetParentOwnerMismatchError,
    UserBudgetParentUnboundError,
)
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.user_budget import UserBudgetRepository
from se.src.infrastructure.storage.repositories.user_data.users import UserRepository
from se.src.runtimes.agent.task_budget import TaskBudgetService


ROOT = Path(__file__).resolve().parents[3]


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _seed_user(database: Path, user_id: str) -> None:
    raw = sqlite3.connect(database)
    try:
        raw.execute(
            "INSERT INTO users (id, email, password_hash, status) VALUES (?, ?, ?, 'active')",
            (user_id, f"{user_id}@example.test", "hash"),
        )
        raw.commit()
    finally:
        raw.close()


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.users = UserRepository(self.session)
        self.agents = AgentRepository(self.session)
        self.user_budgets = UserBudgetRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=16,
        max_active_executions=8,
        max_active_branches=8,
        max_parallel_agents=4,
        max_total_tool_calls=64,
        max_total_inference_calls=64,
        max_total_tokens=100_000,
        max_total_cost_usd=Decimal("100.00000000"),
        max_delegation_depth=8,
    )


def _task(task_id: str, owner: str, *, parent_task_id: str | None = None):
    values = {
        "id": task_id,
        "session_id": f"session-{task_id}",
        "created_by": owner,
        "assigned_agent_id": "agent-ubq2",
        "revision": 0,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"goal": "ubq2 integration"},
    }
    if parent_task_id is not None:
        values["parent_task_id"] = parent_task_id
    return values


async def _setup(tmp_path: Path):
    database = tmp_path / "ubq2-integration.sqlite"
    # Alembic env.py intentionally resolves ASSISTANT_ALEMBIC_DATABASE_URL
    # ahead of Config sqlalchemy.url. Bind the test database explicitly so
    # CI never falls back to the repository-relative data/ default.
    env_key = "ASSISTANT_ALEMBIC_DATABASE_URL"
    previous_url = os.environ.get(env_key)
    os.environ[env_key] = f"sqlite+aiosqlite:///{database.as_posix()}"
    try:
        # Alembic's env owns its own asyncio.run(), so invoke the synchronous
        # command outside pytest's already-running event loop.
        await asyncio.to_thread(command.upgrade, _config(database), "head")
    finally:
        if previous_url is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous_url
    await asyncio.to_thread(_seed_user, database, "user-a")
    await asyncio.to_thread(_seed_user, database, "user-b")

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)

    enabled = UserBudgetDualAccountingService(
        factory,
        DualAccountingSettings(enabled=True),
    )
    disabled = UserBudgetDualAccountingService(
        factory,
        DualAccountingSettings(enabled=False),
    )
    service = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="ubq2-test-v1"),
        user_budget_dual_accounting=enabled,
    )
    return database, engine, sessions, factory, service, enabled, disabled


def _identity(user_id: str) -> Identity:
    return Identity(
        user_id=user_id,
        auth_type="api_key",
        scopes={"*"},
    )


@pytest.mark.asyncio
async def test_ubq2_enrollment_is_durable_and_flag_only_controls_new_tasks(
    tmp_path: Path,
) -> None:
    _, engine, _, factory, service, enabled, disabled = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            _task("task-bound", "user-a"),
            identity=_identity("user-a"),
        )
        async with factory() as uow:
            binding = await uow.user_budgets.get_task_binding("task-bound")
            account = await uow.user_budgets.get_account("user-a")
            active = await uow.user_budgets.get_active_window("user-a")
            assert binding is not None
            assert binding.owner_user_id == "user-a"
            assert account is not None
            # Enrollment provisions policy/account only. First resource use
            # lazily creates the active window.
            assert active is None
            await uow.commit()

        service._user_budget_dual_accounting = disabled
        await service.reserve_inference("task-bound", request_id="req-bound")

        async with factory() as uow:
            receipt = await uow.user_budgets.get_dual_accounting_receipt(
                "task-bound",
                "INFERENCE",
                "req-bound",
                "INFERENCE_CALL",
            )
            assert receipt is not None
            assert receipt.owner_user_id == "user-a"
            active = await uow.user_budgets.get_active_window("user-a")
            assert active is not None
            assert active.inference_used == 1
            await uow.commit()

        service._user_budget_dual_accounting = disabled
        await service.create_task_with_budget(
            _task("task-legacy", "user-a"),
            identity=_identity("user-a"),
        )
        service._user_budget_dual_accounting = enabled
        await service.reserve_inference("task-legacy", request_id="req-legacy")

        async with factory() as uow:
            assert await uow.user_budgets.get_task_binding("task-legacy") is None
            assert (
                await uow.user_budgets.get_dual_accounting_receipt(
                    "task-legacy",
                    "INFERENCE",
                    "req-legacy",
                    "INFERENCE_CALL",
                )
                is None
            )
            legacy_budget = await uow.agents.get_task_budget("task-legacy")
            assert legacy_budget.used_inference_calls == 1
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_parent_binding_rules_are_fail_closed(tmp_path: Path) -> None:
    _, engine, _, factory, service, enabled, disabled = await _setup(tmp_path)
    try:
        service._user_budget_dual_accounting = disabled
        await service.create_task_with_budget(
            _task("parent-legacy", "user-a"),
            identity=_identity("user-a"),
        )
        service._user_budget_dual_accounting = enabled
        with pytest.raises(UserBudgetParentUnboundError):
            await service.create_task_with_budget(
                _task(
                    "child-of-legacy",
                    "user-a",
                    parent_task_id="parent-legacy",
                ),
                identity=_identity("user-a"),
            )

        await service.create_task_with_budget(
            _task("parent-bound", "user-a"),
            identity=_identity("user-a"),
        )
        await service.create_task_with_budget(
            _task(
                "child-bound",
                "user-a",
                parent_task_id="parent-bound",
            ),
            identity=_identity("user-a"),
        )
        async with factory() as uow:
            parent = await uow.user_budgets.get_task_binding("parent-bound")
            child = await uow.user_budgets.get_task_binding("child-bound")
            assert parent is not None and child is not None
            assert parent.owner_user_id == child.owner_user_id == "user-a"
            await uow.commit()

        with pytest.raises(UserBudgetParentOwnerMismatchError):
            await service.create_task_with_budget(
                _task(
                    "child-wrong-owner",
                    "user-b",
                    parent_task_id="parent-bound",
                ),
                identity=_identity("user-b"),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_resource_mirror_replay_and_minimized_bridge(
    tmp_path: Path,
) -> None:
    _, engine, _, factory, service, _, _ = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            _task("task-mirror", "user-a"),
            identity=_identity("user-a"),
        )

        first = await service.reserve_inference(
            "task-mirror",
            request_id="inference-1",
        )
        replay = await service.reserve_inference(
            "task-mirror",
            request_id="inference-1",
        )
        assert first.used_inference_calls == replay.used_inference_calls == 1

        usage = await service.account_usage(
            "task-mirror",
            usage_key="usage-1",
            tokens=12,
            cost_usd=Decimal("0.12500000"),
        )
        assert usage.used_tokens == 12
        assert usage.used_cost_usd == Decimal("0.12500000")

        no_charge = await service.account_usage(
            "task-mirror",
            usage_key="usage-zero",
            tokens=0,
            cost_usd=Decimal("0"),
        )
        assert no_charge.used_tokens == 12

        await service.reserve_tool_call_batch(
            "task-mirror",
            [
                {
                    "tool_call_id": "tool-call-1",
                    "capability_id": "tool.example",
                    "arguments": {"secret": "must-not-be-retained"},
                }
            ],
        )

        async with factory() as uow:
            inference = await uow.user_budgets.get_dual_accounting_receipt(
                "task-mirror",
                "INFERENCE",
                "inference-1",
                "INFERENCE_CALL",
            )
            tokens = await uow.user_budgets.get_dual_accounting_receipt(
                "task-mirror",
                "USAGE",
                "usage-1",
                "TOTAL_TOKEN",
            )
            cost = await uow.user_budgets.get_dual_accounting_receipt(
                "task-mirror",
                "USAGE",
                "usage-1",
                "COST_USD",
            )
            zero = await uow.user_budgets.get_dual_accounting_receipt(
                "task-mirror",
                "USAGE",
                "usage-zero",
                "NO_CHARGE",
            )
            tool = await uow.user_budgets.get_dual_accounting_receipt(
                "task-mirror",
                "TOOL_CALL",
                "tool-call-1",
                "TOOL_CALL",
            )
            assert inference is not None and inference.amount_atomic == 1
            assert tokens is not None and tokens.amount_atomic == 12
            assert cost is not None and cost.amount_atomic == 12_500_000
            assert zero is not None and zero.amount_atomic == 0
            assert zero.ubq_reservation_id is None
            assert tool is not None
            assert tool.capability_id == "tool.example"
            assert not hasattr(tool, "source_payload_json")
            assert not hasattr(tool, "source_projection_json")
            assert "must-not-be-retained" not in repr(tool.__dict__)
            await uow.commit()
    finally:
        await engine.dispose()


def test_ubq2_26a_preserves_25a_sqlite_trigger_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ubq2-trigger-preservation.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)

    command.upgrade(config, "25a_ubq1_user_budget_foundation")
    raw = sqlite3.connect(database)
    try:
        before = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'trg_ubq_%'"
            )
        }
        assert before
    finally:
        raw.close()

    command.upgrade(config, "26a_ubq2_dual_accounting_bridge")
    raw = sqlite3.connect(database)
    try:
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        after = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'trg_ubq_%'"
            )
        }
        assert before <= after

        # 25a parent-history protection still executes after 26a.
        raw.execute(
            "INSERT INTO users (id, email, password_hash, status) "
            "VALUES ('trigger-user', 'trigger-user@example.test', 'hash', 'active')"
        )
        raw.execute(
            "INSERT INTO user_budget_policies ("
            "policy_id, owner_user_id, policy_version, policy_fingerprint, "
            "window_duration_seconds, tool_limits_json"
            ") VALUES ("
            "'trigger-policy', 'trigger-user', 'v1', ?, 3600, '{}'"
            ")",
            ("a" * 64,),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="UBQ_INTEGRITY_USER_HISTORY_RESTRICTED",
        ):
            raw.execute("DELETE FROM users WHERE id='trigger-user'")
        raw.rollback()
    finally:
        raw.close()

    command.downgrade(config, "25a_ubq1_user_budget_foundation")
    raw = sqlite3.connect(database)
    try:
        downgraded = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'trg_ubq_%'"
            )
        }
        assert downgraded == before
    finally:
        raw.close()


def test_ubq2_bridge_schema_has_no_generic_sensitive_projection_column(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ubq2-schema-minimization.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    command.upgrade(_config(database), "head")

    raw = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in raw.execute(
                "PRAGMA table_info(user_budget_dual_accounting_receipts)"
            )
        }
        assert "source_payload_json" not in columns
        assert "source_projection_json" not in columns
        assert {
            "source_payload_fingerprint",
            "amount_atomic",
            "capability_id",
        } <= columns
    finally:
        raw.close()


@pytest.mark.asyncio
async def test_ubq2_reconciliation_retains_bridge_but_does_not_invent_gc_proof(
    tmp_path: Path,
) -> None:
    database, engine, _, factory, service, enabled, _ = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            _task("task-source-gone", "user-a"),
            identity=_identity("user-a"),
        )
        await service.reserve_inference(
            "task-source-gone",
            request_id="source-gone-inference",
        )

        before = await enabled.reconcile_task("task-source-gone")
        assert {item.status for item in before} == {"OK"}

        # Model the canonical R11 reservation-first -> TaskBudget -> Task
        # deletion order. UBQ binding/bridge are independently retained and
        # no durable R11 GC receipt exists.
        raw = sqlite3.connect(database)
        try:
            raw.execute(
                "DELETE FROM agent_task_budget_reservations WHERE task_id=?",
                ("task-source-gone",),
            )
            raw.execute(
                "DELETE FROM agent_task_budgets WHERE task_id=?",
                ("task-source-gone",),
            )
            raw.execute(
                "DELETE FROM agent_tasks WHERE id=?",
                ("task-source-gone",),
            )
            raw.commit()
        finally:
            raw.close()

        after = await enabled.reconcile_task("task-source-gone")
        assert after
        assert {item.status for item in after} == {
            "SOURCE_ABSENT_UNPROVEN"
        }

        async with factory() as uow:
            assert await uow.agents.get_task("task-source-gone") is None
            assert (
                await uow.user_budgets.get_task_binding("task-source-gone")
                is not None
            )
            assert (
                await uow.user_budgets.get_dual_accounting_receipt(
                    "task-source-gone",
                    "INFERENCE",
                    "source-gone-inference",
                    "INFERENCE_CALL",
                )
                is not None
            )
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_mirror_failure_rolls_back_taskbudget_reservation_and_usage(
    tmp_path: Path,
) -> None:
    _, engine, _, factory, service, enabled, _ = await _setup(tmp_path)

    class _FailingDual(UserBudgetDualAccountingService):
        async def mirror_resource_in_uow(self, *args, **kwargs):
            raise RuntimeError("forced UBQ mirror failure")

    try:
        await service.create_task_with_budget(
            _task("task-rollback", "user-a"),
            identity=_identity("user-a"),
        )
        service._user_budget_dual_accounting = _FailingDual(
            factory,
            enabled.settings,
        )

        with pytest.raises(RuntimeError, match="forced UBQ mirror failure"):
            await service.reserve_inference(
                "task-rollback",
                request_id="req-rollback",
            )

        async with factory() as uow:
            budget = await uow.agents.get_task_budget("task-rollback")
            assert budget is not None
            assert budget.used_inference_calls == 0
            generation = int(budget.incarnation_generation)
            assert (
                await uow.agents.get_task_budget_reservation(
                    "task-rollback",
                    "INFERENCE",
                    "req-rollback",
                    expected_incarnation_generation=generation,
                )
                is None
            )
            assert (
                await uow.user_budgets.get_dual_accounting_receipt(
                    "task-rollback",
                    "INFERENCE",
                    "req-rollback",
                    "INFERENCE_CALL",
                )
                is None
            )
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_replay_precedes_expired_window_rollover(
    tmp_path: Path,
) -> None:
    database, engine, _, factory, service, _, _ = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            _task("task-replay-window", "user-a"),
            identity=_identity("user-a"),
        )
        await service.reserve_inference(
            "task-replay-window",
            request_id="req-original-window",
        )

        raw = sqlite3.connect(database)
        try:
            original_count = raw.execute(
                "SELECT COUNT(*) FROM user_budget_windows "
                "WHERE owner_user_id='user-a'"
            ).fetchone()[0]
            assert original_count == 1
            raw.execute(
                "UPDATE user_budget_windows "
                "SET expires_at='2000-01-01 00:00:00' "
                "WHERE owner_user_id='user-a'"
            )
            raw.commit()
        finally:
            raw.close()

        replay = await service.reserve_inference(
            "task-replay-window",
            request_id="req-original-window",
        )
        assert replay.used_inference_calls == 1

        raw = sqlite3.connect(database)
        try:
            after_count = raw.execute(
                "SELECT COUNT(*) FROM user_budget_windows "
                "WHERE owner_user_id='user-a'"
            ).fetchone()[0]
            assert after_count == original_count
        finally:
            raw.close()

        async with factory() as uow:
            bridge = await uow.user_budgets.get_dual_accounting_receipt(
                "task-replay-window",
                "INFERENCE",
                "req-original-window",
                "INFERENCE_CALL",
            )
            assert bridge is not None
            assert bridge.window_epoch == 1
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_taskbudget_allocator_concurrent_legacy_creators_are_unique(
    tmp_path: Path,
) -> None:
    _, engine, _, factory, service, _, disabled = await _setup(tmp_path)
    try:
        service._user_budget_dual_accounting = disabled

        async def _create(task_id: str) -> None:
            await service.create_task_with_budget(
                _task(task_id, "user-a"),
                identity=_identity("user-a"),
            )

        await asyncio.gather(
            _create("task-generation-a"),
            _create("task-generation-b"),
        )

        async with factory() as uow:
            first = await uow.agents.get_task_budget("task-generation-a")
            second = await uow.agents.get_task_budget("task-generation-b")
            assert first is not None and second is not None
            assert first.incarnation_generation != second.incarnation_generation
            assert {
                int(first.incarnation_generation),
                int(second.incarnation_generation),
            } == {1, 2}
            allocator = await uow.agents.get_task_budget_incarnation_allocator()
            assert allocator is not None
            assert int(allocator.next_generation) == 3
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_sqlite_bridge_and_incarnation_triggers_hold_with_fk_off(
    tmp_path: Path,
) -> None:
    database, engine, _, _, service, _, _ = await _setup(tmp_path)
    try:
        await service.create_task_with_budget(
            _task("task-trigger-parity", "user-a"),
            identity=_identity("user-a"),
        )
        await service.reserve_inference(
            "task-trigger-parity",
            request_id="trigger-inference",
        )

        raw = sqlite3.connect(database)
        try:
            assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
            bridge = raw.execute(
                "SELECT bridge_receipt_id, owner_user_id, window_epoch, "
                "ubq_reservation_id, ubq_idempotency_key, "
                "ubq_payload_fingerprint, amount_atomic "
                "FROM user_budget_dual_accounting_receipts "
                "WHERE task_id='task-trigger-parity' "
                "AND task_budget_kind='INFERENCE' "
                "AND task_budget_reservation_key='trigger-inference' "
                "AND mirror_dimension='INFERENCE_CALL'"
            ).fetchone()
            assert bridge is not None

            with pytest.raises(
                sqlite3.IntegrityError,
                match="UBQ2_BRIDGE_REFERENCE_INVALID",
            ):
                raw.execute(
                    "INSERT INTO user_budget_dual_accounting_receipts ("
                    "bridge_receipt_id, owner_user_id, task_id, "
                    "task_budget_kind, task_budget_reservation_key, "
                    "mirror_dimension, source_payload_fingerprint, "
                    "window_epoch, ubq_reservation_id, ubq_idempotency_key, "
                    "ubq_payload_fingerprint, amount_atomic, capability_id"
                    ") VALUES (?, ?, 'task-trigger-parity', 'INFERENCE', "
                    "'trigger-inference', 'INFERENCE_CALL', ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        "bridge-wrong-source-fingerprint",
                        bridge[1],
                        "0" * 64,
                        bridge[2],
                        bridge[3],
                        bridge[4],
                        bridge[5],
                        bridge[6],
                    ),
                )

            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_BUDGET_INCARNATION_STILL_REFERENCED",
            ):
                raw.execute(
                    "DELETE FROM agent_task_budgets "
                    "WHERE task_id='task-trigger-parity'"
                )
            raw.rollback()
        finally:
            raw.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ubq2_committed_taskbudget_generation_is_not_reused_after_source_gc(
    tmp_path: Path,
) -> None:
    database, engine, _, factory, service, _, disabled = await _setup(tmp_path)
    try:
        service._user_budget_dual_accounting = disabled
        await service.create_task_with_budget(
            _task("task-old-generation", "user-a"),
            identity=_identity("user-a"),
        )
        async with factory() as uow:
            old_budget = await uow.agents.get_task_budget(
                "task-old-generation"
            )
            assert old_budget is not None
            old_generation = int(old_budget.incarnation_generation)
            await uow.commit()

        raw = sqlite3.connect(database)
        try:
            raw.execute(
                "DELETE FROM agent_task_budget_reservations WHERE task_id=?",
                ("task-old-generation",),
            )
            raw.execute(
                "DELETE FROM agent_task_budgets WHERE task_id=?",
                ("task-old-generation",),
            )
            raw.execute(
                "DELETE FROM agent_tasks WHERE id=?",
                ("task-old-generation",),
            )
            raw.commit()
        finally:
            raw.close()

        await service.create_task_with_budget(
            _task("task-new-generation", "user-a"),
            identity=_identity("user-a"),
        )
        async with factory() as uow:
            new_budget = await uow.agents.get_task_budget(
                "task-new-generation"
            )
            assert new_budget is not None
            assert int(new_budget.incarnation_generation) > old_generation
            assert int(new_budget.incarnation_generation) != old_generation
            await uow.commit()
    finally:
        await engine.dispose()
