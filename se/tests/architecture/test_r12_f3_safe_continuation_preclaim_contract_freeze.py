from __future__ import annotations

import inspect
from pathlib import Path

from se.src.runtimes.agent.contracts.recovery import (
    RecoveryActivationResult,
    RecoveryContinuationAuthority,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_F3_SAFE_CONTINUATION_PRECLAIM_CONTRACT_FREEZE_6ACF6C5F.md"
)


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_r12_f3_activation_result_carries_exact_lease_handoff_authority():
    fields = RecoveryActivationResult.__dataclass_fields__
    assert "activation_owner_instance_id" in fields
    assert "lease_generation" in fields
    assert "lease_expires_at" in fields
    assert "consumed_execution_revision" in fields


def test_r12_f3_existing_store_exposes_read_only_active_lease_fence():
    source = inspect.getsource(DurableAgentStore.has_active_execution_lease_fence)

    assert "owner_instance_id" in source
    assert "lease_generation" in source
    assert "now_utc" in source
    assert "has_active_execution_lease_fence" in source


def test_r12_f3_parallel_continuation_slot_is_the_dispatch_guard_insertion_seam():
    source = inspect.getsource(AgentToolExecutionCoordinator.continue_invocations)

    assert "async with semaphore" in source
    assert "self.continue_invocation(context, action)" in source


def test_r12_f3_generic_r7_postclaim_recovery_is_not_r12_lease_fenced():
    recover_source = inspect.getsource(AgentRuntime.recover_claimed_resume)
    fail_source = inspect.getsource(AgentRuntime.fail_claimed_resume)

    assert "lease_generation" not in recover_source
    assert "owner_instance_id" not in recover_source
    assert "lease_generation" not in fail_source
    assert "owner_instance_id" not in fail_source


def test_r12_f3_contract_freezes_exact_per_action_fence_and_fail_closed_option_a():
    text = _text()

    assert "F3-A bounded production slice" in text
    assert "per-action semaphore slot" in text
    assert "immediately before the adapter/CapabilityRuntime" in text
    assert "lease_expires_at == RecoveryActivationResult.lease_expires_at" in text
    assert "Option A / current-safe minimum" in text
    assert "DO NOT publish WAITING/RECOVERY" in text
    assert "DO NOT publish FAILED" in text


def test_r12_f3_contract_closes_provider_inference_from_f3_a():
    text = _text()

    assert "F3-A ends **before provider/model inference**" in text
    assert "ProviderInferenceAdapter.complete()" in text
    assert "F3-B production CLAIM" in text
    assert "fresh exact-main dependency audit" in text


def test_r12_f3_contract_forbids_fake_r7_claim_and_generic_parking():
    text = _text()

    assert "MUST NOT manufacture a fake ResumePlan or ResumeClaimConsumeResult" in text
    assert "MUST NOT call generic R7 recover_claimed_resume()" in text
    assert "fail_claimed_resume()" in text


def test_r12_f3_contract_requires_no_second_tool_admission_or_quota_lifecycle():
    text = _text()

    assert "MUST NOT create a second continuation engine" in text
    assert "F3 performs no second admission" in text
    assert "F3 performs no second logical TOOL charge" in text
    assert "REUSE_COMMITTED never mints quota authority" in text


def test_r12_f3_contract_freezes_exact_recovery_continuation_affinity_before_dispatch():
    fields = RecoveryContinuationAuthority.__dataclass_fields__
    assert "target_client_id" in fields
    assert "target_connection_id" in fields
    assert "implementation_id" in fields

    text = _text()
    assert "exact F1-frozen" in text
    assert "continuation_authority.target_client_id" in text
    assert "continuation_authority.target_connection_id" in text
    assert "continuation_authority.implementation_id" in text
    assert "implementation-id replacement/drift" in text
    assert "kind/execution_mode/origin" in text
    assert "zero external dispatch" in text


def test_r12_f3_contract_preserves_r6_ubq_truth_after_postdispatch_fence_loss():
    text = _text()

    assert "PRESERVE canonical R6/UBQ truth for any already-started external attempt" in text
    assert "terminal outcome or OUTCOME_UNKNOWN" in text
    assert "zero AgentToolResult projection" in text
    assert "zero checkpoint/transcript update" in text
    assert "zero execution progression" in text
    assert "zero model-visible recovery result" in text
    assert "already-dispatched slot MUST NOT be cancelled in a way that loses side-effect" in text
