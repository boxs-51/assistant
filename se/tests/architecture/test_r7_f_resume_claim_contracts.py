from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.contracts.resume import (
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
    ResumeTriggerType,
    normalize_resume_trigger_type,
    resume_plan_fingerprint,
)
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)


def _plan() -> ResumePlan:
    action = ResumeInvocationAction(
        invocation_id="inv-r7f-contract",
        tool_call_id="call-r7f-contract",
        ordinal=0,
        capability_id="tool.r7f",
        capability_version="1.0",
        request_fingerprint="f" * 64,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        expected_invocation_revision=5,
        expected_invocation_state=CapabilityInvocationState.WAITING,
        expected_remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
        action=ResumeInvocationActionKind.REPLAY_SAFE,
    )
    plan = ResumePlan(
        execution_id="exec-r7f-contract",
        checkpoint_id="cp-r7f-contract",
        expected_execution_revision=2,
        plan_fingerprint="",
        agent_id="agent-r7f",
        session_id="session-r7f",
        task_id=None,
        branch_id=None,
        parent_execution_id=None,
        retry_of_execution_id=None,
        base_execution_id=None,
        base_checkpoint_id=None,
        correlation_id="corr-r7f",
        trace_id="trace-r7f",
        request_id="request-r7f",
        iteration=1,
        ordered_tool_call_ids=("call-r7f-contract",),
        transcript_snapshot=(
            InferenceMessage(role="assistant", content=""),
        ),
        remaining_active_budget_seconds=20.0,
        wait_expires_at=None,
        target_user_id="user-r7f",
        target_client_id="client-r7f",
        target_connection_id="conn-k2",
        invocation_actions=(action,),
    )
    return replace(plan, plan_fingerprint=resume_plan_fingerprint(plan))


def test_r7_f_trigger_vocabulary_normalizes_legacy_connection_spelling():
    assert normalize_resume_trigger_type(
        "CONNECTION_RECONNECT"
    ) is ResumeTriggerType.CLIENT_RECONNECT
    assert normalize_resume_trigger_type(
        ResumeTriggerType.CLIENT_RECONNECT
    ) is ResumeTriggerType.CLIENT_RECONNECT


def test_r7_f_plan_fingerprint_is_recomputable_and_semantic():
    plan = _plan()
    assert resume_plan_fingerprint(plan) == plan.plan_fingerprint

    changed = replace(plan, target_connection_id="conn-k3")
    assert resume_plan_fingerprint(changed) != plan.plan_fingerprint
