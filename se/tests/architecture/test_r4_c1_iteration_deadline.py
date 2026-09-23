from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent.execution import AgentExecutionRecord
from se.src.runtimes.agent.contracts import AgentExecutionContext


class _FakeClock:
    def __init__(self, monotonic: float = 100.0) -> None:
        self.mono = monotonic
        self.wall = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.mono

    def now_utc(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


def _context(
    clock: _FakeClock,
    *,
    execution_timeout: float = 60.0,
    iteration_timeout: float = 20.0,
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id="exec-r4-c1",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            timeout_seconds=execution_timeout,
            iteration_timeout_seconds=iteration_timeout,
        ),
        clock=clock,
    )


def test_r4_c1_iteration_budget_is_min_of_execution_and_config():
    clock = _FakeClock()
    context = _context(
        clock,
        execution_timeout=12.0,
        iteration_timeout=20.0,
    )

    budget = context.begin_iteration_budget()

    assert budget == 12.0
    assert context.remaining_iteration_seconds == 12.0
    assert context.remaining_for_operation(5.0) == 5.0

    clock.advance(4.0)

    assert context.remaining_seconds == 8.0
    assert context.remaining_iteration_seconds == 8.0


def test_r4_c1_iteration_timeout_caps_longer_execution():
    clock = _FakeClock()
    context = _context(
        clock,
        execution_timeout=60.0,
        iteration_timeout=7.0,
    )

    assert context.begin_iteration_budget() == 7.0
    assert context.remaining_iteration_seconds == 7.0

    clock.advance(3.0)

    assert context.remaining_seconds == 57.0
    assert context.remaining_iteration_seconds == 4.0
    assert context.remaining_for_operation(10.0) == 4.0


def test_r4_c1_new_iteration_gets_new_envelope_but_not_new_execution_budget():
    clock = _FakeClock()
    context = _context(
        clock,
        execution_timeout=12.0,
        iteration_timeout=5.0,
    )

    assert context.begin_iteration_budget() == 5.0
    clock.advance(4.0)
    context.clear_iteration_budget()

    assert context.remaining_seconds == 8.0
    assert context.begin_iteration_budget() == 5.0

    clock.advance(5.0)

    assert context.remaining_seconds == 3.0
    assert context.remaining_iteration_seconds == 0.0
    assert context.iteration_timed_out is True


def test_r4_c1_waiting_freeze_clears_process_local_iteration_deadline():
    clock = _FakeClock()
    context = _context(
        clock,
        execution_timeout=60.0,
        iteration_timeout=10.0,
    )
    context.begin_iteration_budget()
    clock.advance(20.0)

    frozen = context.freeze_active_budget()

    assert frozen == 40.0
    assert context.active_budget_running is False
    assert context.iteration_deadline_monotonic is None

    clock.advance(600.0)

    assert context.remaining_active_seconds == 40.0
    assert context.remaining_iteration_seconds == 40.0


def test_r4_c1_operation_timeout_is_three_level_minimum():
    clock = _FakeClock()
    context = _context(
        clock,
        execution_timeout=12.0,
        iteration_timeout=7.0,
    )
    context.begin_iteration_budget()

    assert context.remaining_for_operation(2.0) == 2.0

    clock.advance(6.0)

    assert context.remaining_seconds == 6.0
    assert context.remaining_iteration_seconds == 1.0
    assert context.remaining_for_operation(2.0) == 1.0


def test_r4_c1_monotonic_deadlines_are_not_durable_sql_columns():
    columns = set(AgentExecutionRecord.__table__.columns.keys())

    assert "remaining_active_budget_seconds" in columns
    assert "wait_expires_at" in columns
    assert "deadline" not in columns
    assert "active_deadline_monotonic" not in columns
    assert "iteration_deadline_monotonic" not in columns
