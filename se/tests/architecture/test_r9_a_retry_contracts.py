from __future__ import annotations

from dataclasses import fields, replace

from se.src.infrastructure.storage.models.sql.agent import (
    AgentTaskRetryAdmissionRecord,
)
from se.src.runtimes.agent.contracts.retry import (
    RetryAdmission,
    RetryPlan,
    retry_plan_fingerprint,
)


def _plan(**updates):
    values = {
        "retry_request_id": "retry-request-a",
        "task_id": "task-r9-a",
        "expected_task_revision": 4,
        "session_id": "session-r9-a",
        "branch_id": "branch-r9-a",
        "expected_branch_revision": 7,
        "source_execution_id": "exec-r9-source",
        "expected_execution_revision": 9,
        "source_execution_state": "FAILED",
        "source_agent_id": "agent-r9",
        "source_checkpoint_id": None,
        "source_checkpoint_transcript_fingerprint": None,
        "expected_task_budget_revision": 5,
        "budget_policy_fingerprint": "b" * 64,
        "parent_execution_id": None,
        "base_execution_id": None,
        "base_checkpoint_id": None,
        "correlation_id": "corr-r9",
        "request_fingerprint": "d" * 64,
        "source_context_fingerprint": "e" * 64,
        "fresh_active_budget_seconds": 60.0,
        "target_user_id": "user-r9",
    }
    values.update(updates)
    fingerprint = retry_plan_fingerprint(values)
    return RetryPlan(**values, plan_fingerprint=fingerprint)


def test_r9_a_retry_request_id_is_not_semantic_fingerprint_input():
    first = _plan(retry_request_id="retry-a")
    second = replace(first, retry_request_id="retry-b")
    assert retry_plan_fingerprint(first) == retry_plan_fingerprint(second)
    assert first.plan_fingerprint == retry_plan_fingerprint(second)


def test_r9_a_retry_fingerprint_is_key_order_stable_and_semantic():
    base = _plan()
    mapping = {
        item.name: getattr(base, item.name)
        for item in reversed(fields(RetryPlan))
        if item.name not in {"plan_fingerprint", "retry_request_id"}
    }
    assert retry_plan_fingerprint(base) == retry_plan_fingerprint(mapping)

    changed = (
        replace(base, expected_task_revision=5),
        replace(base, expected_branch_revision=8),
        replace(base, expected_execution_revision=10),
        replace(base, source_execution_state="TIMEOUT"),
        replace(base, source_checkpoint_id="cp-r9"),
        replace(
            base,
            source_checkpoint_transcript_fingerprint="2" * 64,
        ),
        replace(base, expected_task_budget_revision=6),
        replace(base, budget_policy_fingerprint="c" * 64),
        replace(base, source_execution_id="exec-r9-other"),
        replace(base, request_fingerprint="f" * 64),
        replace(base, source_context_fingerprint="1" * 64),
        replace(base, fresh_active_budget_seconds=30.0),
    )
    for item in changed:
        assert retry_plan_fingerprint(item) != base.plan_fingerprint


def test_r9_a_retry_admission_contract_is_immutable_result_shape():
    admission = RetryAdmission(
        task_id="task-r9-a",
        retry_request_id="retry-a",
        plan_fingerprint="a" * 64,
        branch_id="branch-r9-a",
        branch_revision=8,
        source_execution_id="exec-r9-source",
        source_checkpoint_id=None,
        execution_id="exec-r9-retry",
        execution_revision=1,
        task_revision=4,
        task_budget_revision=6,
    )
    assert admission.branch_id == "branch-r9-a"
    assert admission.source_execution_id != admission.execution_id
    assert admission.execution_revision == 1


def test_r9_a_sql_schema_freezes_retry_request_identity_and_execution_uniqueness():
    table = AgentTaskRetryAdmissionRecord.__table__

    assert [column.name for column in table.primary_key.columns] == [
        "task_id",
        "retry_request_id",
    ]
    assert not table.c.plan_fingerprint.nullable
    assert not table.c.branch_id.nullable
    assert not table.c.source_execution_id.nullable
    assert table.c.source_checkpoint_id.nullable
    assert not table.c.execution_id.nullable

    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }
    assert "uq_agent_task_retry_admissions_execution_id" in constraint_names

    index_names = {index.name for index in table.indexes}
    assert {
        "ix_agent_task_retry_admissions_branch_id",
        "ix_agent_task_retry_admissions_source_execution_id",
    } <= index_names
