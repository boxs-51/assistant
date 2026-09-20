from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from se.src.domain.schemas.multi_agent import AgentTask
from se.src.domain.schemas.task_budget import (
    TaskBudget,
    TaskBudgetLimits,
    TaskBudgetPolicy,
    TaskBudgetState,
    normalize_task_budget_cost,
    task_budget_policy_fingerprint,
)


def _limits(**updates):
    values = {
        "max_total_executions": 10,
        "max_active_executions": 4,
        "max_active_branches": 3,
        "max_parallel_agents": 2,
        "max_total_tool_calls": 20,
        "max_total_inference_calls": 10,
        "max_delegation_depth": 3,
        "max_total_tokens": 1000,
        "max_total_cost_usd": "1.25",
    }
    values.update(updates)
    return TaskBudgetLimits(**values)


def _budget(**updates):
    limits = updates.pop("limits", _limits())
    policy = TaskBudgetPolicy()
    values = {
        "task_id": "task-r5-b",
        "limits": limits,
        "policy_version": policy.version,
        "policy_fingerprint": task_budget_policy_fingerprint(
            limits,
            policy,
        ),
        "deny_recursive_agent_cycle": True,
    }
    values.update(updates)
    return TaskBudget(**values)


def test_r5_b_required_limits_are_positive():
    with pytest.raises(ValidationError):
        _limits(max_total_executions=0)
    with pytest.raises(ValidationError):
        _limits(max_total_cost_usd="0")


def test_r5_b_cost_is_decimal_and_quantized_to_eight_places():
    assert normalize_task_budget_cost("0.123456789") == Decimal(
        "0.12345679"
    )
    assert _limits(max_total_cost_usd="1.234567891").max_total_cost_usd == (
        Decimal("1.23456789")
    )


def test_r5_b_domain_enforces_counter_and_closed_state_invariants():
    with pytest.raises(ValidationError, match="active_executions"):
        _budget(active_executions=1, used_executions=0)

    with pytest.raises(ValidationError, match="closed_at"):
        _budget(state=TaskBudgetState.CLOSED)

    closed = _budget(
        state=TaskBudgetState.CLOSED,
        closed_at=datetime.now(timezone.utc),
    )
    assert closed.state is TaskBudgetState.CLOSED


def test_r5_b_policy_fingerprint_is_stable_and_limit_sensitive():
    policy = TaskBudgetPolicy(version="r5-test")
    first = task_budget_policy_fingerprint(_limits(), policy)
    second = task_budget_policy_fingerprint(_limits(), policy)
    changed = task_budget_policy_fingerprint(
        _limits(max_total_executions=11),
        policy,
    )

    assert first == second
    assert len(first) == 64
    assert changed != first


def test_r5_b_agent_task_exposes_revision():
    task = AgentTask(
        task_id="task-1",
        session_id="session-1",
        created_by="user-1",
        assigned_agent_id="agent-1",
        created_at=1.0,
        updated_at=1.0,
    )
    assert task.revision == 0
    task.revision = 2
    assert task.revision == 2
