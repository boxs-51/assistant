from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from se.src.domain.schemas.user_budget import (
    USER_BUDGET_INT64_MAX,
    UserBudgetPolicy,
    UserBudgetReservationIntent,
    UserBudgetResourceKind,
    decimal_from_atomic,
    decimal_to_atomic,
)
from se.src.infrastructure.storage.models.sql.user_budget import (
    UserBudgetAccountRecord,
    UserBudgetReservationRecord,
    UserBudgetWindowRecord,
)
from se.src.infrastructure.storage.repositories import user_budget as repository_module


def _policy(*, tool_limits: dict[str, int] | None = None) -> UserBudgetPolicy:
    return UserBudgetPolicy(
        policy_id="policy-1",
        owner_user_id="user-1",
        policy_version="v1",
        window_duration_seconds=3600,
        max_compute_units=Decimal("12.50000000"),
        max_inference_calls=10,
        max_input_tokens=1000,
        max_output_tokens=1000,
        max_total_tokens=2000,
        max_tool_calls_total=20,
        default_per_tool_limit=5,
        tool_limits=tool_limits or {"tool.b": 3, "tool.a": 2},
        max_cost_usd=Decimal("1.25000000"),
    )


def test_ubq1_policy_fingerprint_is_order_independent_and_atomic_aligned() -> None:
    left = _policy(tool_limits={"tool.b": 3, "tool.a": 2})
    right = _policy(tool_limits={"tool.a": 2, "tool.b": 3})

    assert left.policy_fingerprint == right.policy_fingerprint
    durable = left.durable_values()
    assert durable["max_compute_atomic"] == 1_250_000_000
    assert durable["max_cost_usd_atomic"] == 125_000_000
    assert durable["tool_limits_json"] == {"tool.a": 2, "tool.b": 3}


def test_ubq1_decimal_quantum_uses_half_even_without_float() -> None:
    assert decimal_to_atomic("1.234567885") == 123_456_788
    assert decimal_to_atomic("1.234567895") == 123_456_790
    assert decimal_from_atomic(123_456_788) == Decimal("1.23456788")

    with pytest.raises(ValueError):
        decimal_to_atomic("NaN")
    with pytest.raises(ValueError):
        decimal_to_atomic("-0.00000001")
    with pytest.raises(ValueError):
        decimal_to_atomic(
            Decimal(USER_BUDGET_INT64_MAX + 1) / Decimal(100_000_000)
        )


def test_ubq1_reservation_fingerprint_binds_tool_and_attribution() -> None:
    first = UserBudgetReservationIntent(
        reservation_id="r1",
        owner_user_id="user-1",
        window_epoch=1,
        idempotency_key="logical-op-1",
        resource_kind=UserBudgetResourceKind.TOOL_CALL,
        capability_id="tool.a",
        reserved_amount_atomic=1,
        attribution={"execution_id": "e1", "task_id": "t1"},
    )
    same = UserBudgetReservationIntent(
        reservation_id="r2",
        owner_user_id="user-1",
        window_epoch=2,
        idempotency_key="logical-op-1",
        resource_kind=UserBudgetResourceKind.TOOL_CALL,
        capability_id="tool.a",
        reserved_amount_atomic=1,
        attribution={"task_id": "t1", "execution_id": "e1"},
    )

    assert first.payload_fingerprint == same.payload_fingerprint
    with pytest.raises(ValueError):
        UserBudgetReservationIntent(
            reservation_id="r3",
            owner_user_id="user-1",
            window_epoch=1,
            idempotency_key="logical-op-2",
            resource_kind=UserBudgetResourceKind.TOOL_CALL,
            capability_id=None,
            reserved_amount_atomic=1,
            attribution={},
        )


def test_ubq1_sql_model_freezes_single_active_and_owner_idempotency() -> None:
    indexes = {
        item.name: item
        for item in UserBudgetWindowRecord.__table__.indexes
    }
    active = indexes["uq_user_budget_windows_one_active_owner"]
    assert active.unique is True
    assert str(active.dialect_options["sqlite"]["where"]) == "state = 'ACTIVE'"
    assert str(active.dialect_options["postgresql"]["where"]) == "state = 'ACTIVE'"

    unique_names = {
        item.name
        for item in UserBudgetReservationRecord.__table__.constraints
        if item.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_user_budget_reservation_owner_idempotency" in unique_names


def test_ubq1_repository_has_named_mutations_and_write_intent_before_rollover_read() -> None:
    source = inspect.getsource(repository_module.UserBudgetRepository)
    rollover = inspect.getsource(repository_module.UserBudgetRepository.rollover_window)

    assert "compare_and_set_window" not in source
    assert "compare_and_set_reservation" not in source
    assert "mutate_window_usage" in source
    assert "mutate_tool_usage" in source
    assert "transition_reservation" in source

    assert rollover.index("_begin_sqlite_write_intent") < rollover.index("get_account")
    assert 'text("BEGIN IMMEDIATE")' in inspect.getsource(
        repository_module._begin_sqlite_write_intent
    )


def test_ubq1_postgresql_ddl_keeps_native_composite_fks_and_active_index() -> None:
    window_ddl = str(
        CreateTable(UserBudgetWindowRecord.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    account_ddl = str(
        CreateTable(UserBudgetAccountRecord.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    active_index = next(
        item
        for item in UserBudgetWindowRecord.__table__.indexes
        if item.name == "uq_user_budget_windows_one_active_owner"
    )
    index_ddl = str(
        CreateIndex(active_index).compile(dialect=postgresql.dialect())
    )

    assert "fk_user_budget_window_exact_policy" in window_ddl
    assert "FOREIGN KEY(owner_user_id, governing_policy_id, governing_policy_version, governing_policy_fingerprint)" in window_ddl
    assert "ON DELETE RESTRICT" in window_ddl

    assert "fk_user_budget_account_next_policy" in account_ddl
    assert "fk_user_budget_account_active_window" in account_ddl
    assert account_ddl.count("ON DELETE RESTRICT") >= 3

    assert "CREATE UNIQUE INDEX uq_user_budget_windows_one_active_owner" in index_ddl
    assert "WHERE state = 'ACTIVE'" in index_ddl
