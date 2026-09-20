from __future__ import annotations

from datetime import datetime, timezone

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.capability.contracts.context import (
    CapabilityExecutionContext,
)


class _Clock:
    def __init__(self) -> None:
        self.mono = 100.0

    def monotonic(self) -> float:
        return self.mono

    def now_utc(self):
        return datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def test_r4_exit_gate_deadline_hierarchy_contract_is_live():
    clock = _Clock()
    context = AgentExecutionContext.create(
        execution_id="exec-r4-exit",
        agent_id="agent-r4",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            timeout_seconds=12.0,
            iteration_timeout_seconds=7.0,
            inference_timeout_seconds=5.0,
            tool_timeout_seconds=2.0,
        ),
        clock=clock,
    )

    assert context.begin_iteration_budget() == 7.0
    assert context.remaining_for_operation(5.0) == 5.0
    assert context.remaining_for_operation(2.0) == 2.0


def test_r4_exit_gate_monotonic_deadlines_are_not_durable_sql_columns():
    columns = set(AgentExecutionRecord.__table__.columns.keys())

    assert "remaining_active_budget_seconds" in columns
    assert "wait_expires_at" in columns

    assert "deadline" not in columns
    assert "active_deadline_monotonic" not in columns
    assert "iteration_deadline_monotonic" not in columns


def test_r4_exit_gate_capability_context_has_typed_parent_timing_provenance():
    context = CapabilityExecutionContext.create(
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        execution_id="exec-parent",
        invocation_id="inv-r4",
        caller_agent_execution_id="exec-parent",
        caller_execution_remaining_seconds=12.0,
        caller_iteration_remaining_seconds=7.0,
    )

    assert context.caller_execution_remaining_seconds == 12.0
    assert context.caller_iteration_remaining_seconds == 7.0
