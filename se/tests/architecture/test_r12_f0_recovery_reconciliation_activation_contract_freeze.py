from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from se.src.infrastructure.storage.models.sql.agent.session import AgentSessionRecord
from se.src.infrastructure.storage.models.sql.agent.task import AgentTaskRecord
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.resume import (
    ResumeClaim,
    ResumeClaimIntent,
    ResumeTriggerType,
)
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.agent.task_budget import (
    TaskBudgetService,
    prepare_resume_capacity_in_uow,
)
from se.src.runtimes.capability.reconciliation import (
    RemoteInvocationReconciliationService,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_F0_RECOVERY_RECONCILIATION_ACTIVATION_CONTRACT_FREEZE_AC1707EF.md"
)
R12_E_DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_E_ATOMIC_RECOVERY_OWNERSHIP.md"
)
R12_A_DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_A_HEAD_AUDIT_CRASH_RECOVERY_LEASE_CONTRACT_FREEZE_6228734A.md"
)
R12_C_DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_C_ATOMIC_EXECUTION_LEASE_AUTHORITY.md"
)
R12_D1_DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_D1_BOUNDED_EXPIRED_OWNED_OBSERVATION.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_r12_f0_server_recovery_trigger_already_exists():
    assert ResumeTriggerType.SERVER_RECOVERY.value == "SERVER_RECOVERY"


def test_r12_f0_existing_r7_planner_remains_connection_only():
    source = inspect.getsource(AgentResumePlanningService.build_resume_plan)

    assert "target_connection_id: str" in source
    assert 'execution_wait_reason != "CONNECTION"' in source
    assert '"UNSUPPORTED_RESUME_TRIGGER"' in source
    assert "checkpoint.origin_client_id != target_client_id" in source


def test_r12_f0_existing_claim_consume_is_client_reconnect_only():
    matcher = inspect.getsource(DurableAgentStore._claim_matches_plan)
    consume = inspect.getsource(DurableAgentStore._consume_resume_claim_once)

    assert 'record.wait_reason == "CONNECTION"' in matcher
    assert "ResumeTriggerType.CLIENT_RECONNECT" in matcher
    assert "CLIENT_RECONNECT plan requires a stable target client_id" in consume
    assert "CLIENT_RECONNECT plan requires a target connection_id" in consume
    assert 'str(execution.wait_reason) != "CONNECTION"' in consume
    assert "compare_and_set_waiting_execution(" in consume
    assert '"CONNECTION"' in consume


def test_r12_f0_durable_agent_session_principal_authority_exists():
    assert AgentSessionRecord.__table__.c.owner_user_id.nullable is False
    assert AgentTaskRecord.__table__.c.session_id.nullable is False
    assert AgentTaskRecord.__table__.c.created_by.nullable is False


def test_r12_f0_r6_reconciliation_requires_owner_and_stable_client():
    source = inspect.getsource(RemoteInvocationReconciliationService.reconcile)

    assert "snapshot.user_id != invocation.owner_user_id" in source
    assert 'snapshot.metadata.get("client_id")' in source
    assert "client_id != invocation.origin_client_id" in source
    assert "self._commit_terminal(invocation, result)" in source


def test_r12_f0_existing_invocation_continuation_requires_claimed_connection():
    source = inspect.getsource(CapabilityToolExecutionAdapter.continue_invocation)

    assert "context.connection_id is None" in source
    assert "R7 continuation requires a claimed target connection" in source
    assert "target_connection_id=context.connection_id" in source


def test_r12_f0_existing_continuation_requires_fresh_connection_generation():
    source = inspect.getsource(CapabilityRuntime._resolve_continuation_target)

    assert "target_connection_id == invocation.connection_id" in source
    assert "Continuation requires a new connection generation." in source


def test_r12_f0_existing_continuation_requires_full_unique_target_predicate():
    source = inspect.getsource(CapabilityRuntime._resolve_continuation_target)
    tree = ast.parse(textwrap.dedent(source))
    function = tree.body[0]

    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert isinstance(function.body[0], ast.If)

    initial_guard = function.body[0].test
    assert isinstance(initial_guard, ast.BoolOp)
    assert isinstance(initial_guard.op, ast.Or)
    guard_terms = {ast.unparse(value) for value in initial_guard.values}
    assert guard_terms == {
        "not target_connection_id",
        "self.catalog is None",
        "self.connection_registry is None",
        "self.realtime is None",
    }
    assert "Continuation target connection is unavailable." in source

    implementation_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "list_implementations"
    ]
    assert len(implementation_calls) == 1
    routable_keywords = {
        keyword.arg: keyword.value
        for keyword in implementation_calls[0].keywords
        if keyword.arg is not None
    }
    assert "routable_only" in routable_keywords
    assert isinstance(routable_keywords["routable_only"], ast.Constant)
    assert routable_keywords["routable_only"].value is True

    required = (
        "item.location is CapabilityExecutionLocation.CLIENT",
        "item.owner_type is CapabilityOwnerType.CLIENT",
        'item.driver_kind == "REMOTE_CLIENT"',
        "item.connection_id == target_connection_id",
        "item.owner_id == invocation.owner_user_id",
        'item.metadata.get("client_id")',
        "item.version == invocation.capability_version",
        "definition.version != invocation.capability_version",
        "definition.kind is not invocation.kind",
        "definition.execution_mode is not invocation.execution_mode",
        "definition.idempotency is not invocation.idempotency",
        "if len(candidates) != 1",
        "Continuation requires exactly one matching client",
    )
    for item in required:
        assert item in source


def test_r12_f0_resume_claim_is_durable_intent_not_execution_authority():
    assert "Creation alone owns no execution authority" in (
        ResumeClaimIntent.__doc__ or ""
    )
    assert "CREATED owns no execution authority" in (ResumeClaim.__doc__ or "")


def test_r12_f0_r12_e_leaves_pending_invocations_for_later_reconciliation():
    text = _read(R12_E_DOC)

    assert (
        "unresolved/non-COMMITTED active slots are snapshotted only as evidence for"
        in text
    )
    assert "later R12-F reconciliation" in text
    assert "R12-F/G/H remain closed" in text


def test_r12_f0_r12_e_preserves_iteration_zero_recovery_cut():
    text = _read(R12_E_DOC)

    assert "A valid recovery may publish" in text
    assert "iteration=0" in text
    assert "No iteration row is synthesized" in text


def test_r12_f0_r12_e_recovery_safe_point_is_unowned_and_generation_bumped():
    text = _read(R12_E_DOC)

    assert "state = WAITING" in text
    assert "wait_reason = RECOVERY" in text
    assert "owner_instance_id = NULL" in text
    assert "lease_expires_at = NULL" in text
    assert "lease_generation = observed_lease_generation + 1" in text


def test_r12_f0_r12_a_requires_active_fence_before_dispatch_and_commit():
    text = _read(R12_A_DOC)

    assert "R12A-I14" in text
    assert "validated immediately before every externally visible provider/tool dispatch" in text
    assert "R12A-I15" in text
    assert "validated immediately before durable active-owner commits" in text
    assert "R12A-I16" in text
    assert "stop new external dispatch immediately" in text


def test_r12_f0_r12_c_fresh_acquire_and_active_fence_remain_canonical():
    text = _read(R12_C_DOC)

    required = (
        "state == RUNNING",
        "owner_instance_id IS NULL",
        "lease_expires_at IS NULL",
        "generation increments atomically by exactly 1",
        "owner == expected owner",
        "generation == expected generation",
        "expiry > now_utc",
    )
    for item in required:
        assert item in text


def test_r12_f0_d1_scanner_only_observes_owned_running_rows():
    text = _read(R12_D1_DOC)

    assert "state == RUNNING" in text
    assert "owner_instance_id IS NOT NULL" in text
    assert "lease_expires_at IS NOT NULL" in text


def test_r12_f0_standalone_lease_wrapper_owns_a_separate_commit_boundary():
    source = inspect.getsource(DurableAgentStore.acquire_execution_lease)

    assert "async with self.uow_factory() as uow" in source
    assert "uow.agents.acquire_execution_lease" in source
    assert "await uow.commit()" in source


def test_r12_f0_canonical_resume_capacity_is_incarnation_fenced():
    source = inspect.getsource(prepare_resume_capacity_in_uow)

    assert "expected_generation = int(budget_record.incarnation_generation)" in source
    assert "expected_incarnation_generation=expected_generation" in source
    assert '"task_budget_incarnation_generation": expected_generation' in source


def test_r12_f0_canonical_recovery_pins_incarnation_across_retries():
    source = inspect.getsource(TaskBudgetService.recover_task_scoped_execution)

    assert "expected_generation: int | None = None" in source
    assert "actual_generation = int(" in source
    assert "TaskBudget incarnation changed during recovery" in source
    assert "expected_incarnation_generation" in source


def test_r12_f0_contract_closes_server_recovery_consume_gap():
    text = _read(DOC)

    required = (
        "Existing R7 claim consumption is CLIENT_RECONNECT-specific",
        "cannot be reused",
        "unchanged for SERVER_RECOVERY",
        "trigger-aware extension of the same ResumeClaim consume",
        "transaction",
        "claim.wait_reason == RECOVERY",
        "claim.trigger_type == SERVER_RECOVERY",
        "CLIENT_RECONNECT behavior remains unchanged",
        "MAY be null only when the frozen",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_closes_recovery_principal_gap():
    text = _read(DOC)

    required = (
        "AgentSessionRecord.owner_user_id = recovery principal",
        "AgentTaskRecord.created_by == AgentSessionRecord.owner_user_id",
        "resolved recovery principal = AgentSessionRecord.owner_user_id",
        "invocation.owner_user_id == resolved recovery principal",
        "MUST NOT synthesize",
        "not automatically the same thing as future",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_closes_continuation_affinity_gap():
    text = _read(DOC)

    required = (
        "null / unsupported remote outcome",
        "executable only after continuation-affinity proof",
        "continuation runtime catalog is available",
        "continuation connection registry is available",
        "continuation realtime multiplexer is available",
        "Missing any",
        "MUST DEFER",
        "connection is ACTIVE / usable",
        "connection stable client_id == invocation.origin_client_id",
        "target_connection_id != invocation.connection_id",
        "fresh connection generation",
        "same authorized stable client",
        "matching capability_id + capability_version is ready",
        "catalog implementation lookup uses `routable_only=True`",
        "exactly one matching routable client-owned `REMOTE_CLIENT` implementation exists on target",
        "full canonical",
        "CapabilityRuntime._resolve_continuation_target(...)",
        "definition.kind == invocation.kind",
        "definition.execution_mode == invocation.execution_mode",
        "definition.idempotency == invocation.idempotency",
        "zero matching implementation",
        "Multiple matching implementations",
        "REJECT before claim",
        "if no compatible connection exists",
        "recovery planning remains DEFERRED",
        "DEFER before claim",
        "does not authorize silent rerouting",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_keeps_empty_recovery_cut_valid():
    text = _read(DOC)

    assert "A valid R12-E iteration-zero cut remains valid" in text
    assert "ordered_tool_call_ids = ()" in text
    assert "pending invocations = ()" in text
    assert "invocation_actions = ()" in text
    assert "MUST NOT synthesize a fake tool call" in text
    assert "valid empty recovery cut may therefore carry null client/connection" in text


def test_r12_f0_contract_freezes_no_blind_remote_replay_and_claim_reuse():
    text = _read(DOC)

    required = (
        "R12-F MUST reuse, not redefine",
        "R12-F may not reconstruct a different active order",
        "R12-F therefore needs a recovery-specific read-only planning entry point/adaptor",
        "R12-F does not invent a broader replay rule",
        "recovery planning remains DEFERRED",
        "no blind replay",
        "ResumeTriggerType.SERVER_RECOVERY",
        "claim creation owns no execution authority",
        "one final",
        "WAITING -> RUNNING execution CAS authority",
        "R12-F production CLAIM = HOLD",
        "merge authority = NONE",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_closes_recovered_activation_lease_fence_gap():
    text = _read(DOC)
    normalized = " ".join(text.split())

    assert "one commit boundary" in normalized

    required = (
        "one caller-owned durable UoW",
        "stage WAITING -> RUNNING semantic execution CAS",
        "fresh R12-C lease acquire",
        "post-acquire lease_generation == plan.expected_unowned_lease_generation + 1",
        "externally committed RUNNING-unowned state = NONE",
        "MUST NOT be invoked as an independently committing second step",
        "Before **every** externally visible provider/model/tool dispatch",
        "Execution revision equality alone is not an external-side-effect fence",
        "stop new external dispatch immediately",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_freezes_post_ubq2_taskbudget_incarnation_authority():
    text = _read(DOC)

    required = (
        "UBQ-2 is now canonical",
        "TaskBudget.incarnation_generation",
        "must not silently adopt the new incarnation",
        "expected_incarnation_generation = frozen plan generation",
        "task_budget_incarnation_generation = frozen plan generation",
        "execution_id:source_revision",
        "MUST NOT re-sample a recreated generation",
        "PostgreSQL V7 executable parity/deployment gate remains OPEN",
    )
    for item in required:
        assert item in text


def test_r12_f0_contract_keeps_production_stages_closed():
    text = _read(DOC)

    assert (
        "R12-F1  recovery-specific read-only plan construction  [PRODUCTION CLOSED]"
        in text
    )
    assert (
        "R12-F2  atomic claim + RUNNING + fresh-lease handoff    [PRODUCTION CLOSED]"
        in text
    )
    assert (
        "R12-F3  safe continuation action execution              [PRODUCTION CLOSED]"
        in text
    )
    assert "this candidate contains no production/runtime/schema/migration delta" in text
