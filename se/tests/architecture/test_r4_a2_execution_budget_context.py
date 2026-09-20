from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionContext,
    UnknownActiveBudgetError,
)


class _FakeClock:
    def __init__(self, *, monotonic: float = 100.0) -> None:
        self.monotonic_value = monotonic
        self.wall_value = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def now_utc(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


def _context(
    clock: _FakeClock,
    *,
    configured_timeout: float = 60.0,
    remaining_active_budget_seconds=...,
):
    kwargs = {}
    if remaining_active_budget_seconds is not ...:
        kwargs["remaining_active_budget_seconds"] = (
            remaining_active_budget_seconds
        )
    return AgentExecutionContext.create(
        execution_id="exec-r4-a2",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=configured_timeout),
        clock=clock,
        **kwargs,
    )


def test_r4_a2_fresh_context_uses_configured_budget_and_injected_clock():
    clock = _FakeClock(monotonic=100.0)
    context = _context(clock, configured_timeout=60.0)

    assert context.started_monotonic == 100.0
    assert context.remaining_active_budget_seconds == 60.0
    assert context.active_deadline_monotonic == 160.0
    assert context.deadline == 160.0
    assert context.remaining_seconds == 60.0

    clock.advance(20.0)

    assert context.remaining_active_seconds == 40.0
    assert context.remaining_seconds == 40.0
    assert context.remaining_for(15.0) == 15.0
    assert context.remaining_for(50.0) == 40.0


def test_r4_a2_freeze_stops_consumption_and_restore_uses_frozen_duration():
    clock = _FakeClock(monotonic=100.0)
    context = _context(clock, configured_timeout=60.0)

    clock.advance(20.0)
    frozen = context.freeze_active_budget()

    assert frozen == 40.0
    assert context.remaining_active_budget_seconds == 40.0
    assert context.active_deadline_monotonic is None
    assert context.active_budget_running is False

    # WAITING wall time does not consume active budget.
    clock.advance(600.0)
    assert context.remaining_active_seconds == 40.0
    assert context.remaining_seconds == 40.0

    restored = context.restore_active_budget()

    assert restored == 40.0
    assert context.active_budget_running is True
    assert context.active_deadline_monotonic == clock.monotonic() + 40.0
    assert context.remaining_seconds == 40.0

    clock.advance(5.0)
    assert context.remaining_seconds == 35.0


def test_r4_a2_explicit_persisted_budget_caps_context_instead_of_configured_max():
    clock = _FakeClock(monotonic=25.0)
    context = _context(
        clock,
        configured_timeout=60.0,
        remaining_active_budget_seconds=12.5,
    )

    assert context.remaining_active_budget_seconds == 12.5
    assert context.active_deadline_monotonic == 37.5
    assert context.remaining_seconds == 12.5


def test_r4_a2_explicit_unknown_budget_fails_closed_and_never_regenerates():
    clock = _FakeClock(monotonic=50.0)
    context = _context(
        clock,
        configured_timeout=60.0,
        remaining_active_budget_seconds=None,
    )

    assert context.remaining_active_budget_seconds is None
    assert context.active_deadline_monotonic is None
    assert context.remaining_active_seconds is None
    assert context.remaining_seconds == 0.0
    assert context.timed_out is True

    with pytest.raises(UnknownActiveBudgetError, match="unknown active budget"):
        context.restore_active_budget()

    assert context.active_deadline_monotonic is None
    assert context.remaining_active_budget_seconds is None


def test_r4_a2_restore_can_accept_new_trusted_remaining_duration():
    clock = _FakeClock(monotonic=200.0)
    context = _context(
        clock,
        remaining_active_budget_seconds=None,
    )

    restored = context.restore_active_budget(17.0)

    assert restored == 17.0
    assert context.remaining_active_budget_seconds == 17.0
    assert context.active_deadline_monotonic == 217.0
    assert context.remaining_seconds == 17.0


def test_r4_a2_existing_now_monotonic_factory_argument_remains_source_compatible():
    clock = _FakeClock(monotonic=100.0)
    context = AgentExecutionContext.create(
        execution_id="exec-compat",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(timeout_seconds=10.0),
        clock=clock,
        now_monotonic=95.0,
    )

    assert context.started_monotonic == 95.0
    assert context.active_deadline_monotonic == 105.0
    assert context.remaining_seconds == 5.0
