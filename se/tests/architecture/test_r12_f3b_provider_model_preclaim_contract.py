from __future__ import annotations

import inspect
from pathlib import Path

from se.src.provider.executor import ProviderExecutor, await_with_provider_deadline
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.recovery import RecoveryInferenceDisposition
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import TaskBudgetService
from se.src.runtimes.agent.recovery_execution import AgentRecoveryExecutionService
from se.src.runtimes.agent.recovery_planning import AgentRecoveryPlanningService
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_F3B_PROVIDER_MODEL_PRECLAIM_CONTRACT_EA6C5F00.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_r12_f3b_contract_freezes_baseline_current_main_and_closed_claim():
    text = _read(DOC)

    assert "main@ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12" in text
    assert "f577fb370f1a73a8fdcc69e4221ac41c925a0338" in text
    assert "R12-F3-A = LANDED / CANONICAL / HEALTHY" in text
    assert "R12-F3-B production CLAIM = CLOSED" in text
    assert "**Production delta:** ZERO" in text


def test_r12_f3b_f3a_stops_before_provider_and_returns_ordered_batch():
    source = inspect.getsource(
        AgentRecoveryExecutionService.execute_active_tool_batch
    )
    class_doc = " ".join(
        (inspect.getdoc(AgentRecoveryExecutionService) or "").split()
    )

    assert "stops before provider/model inference" in class_doc
    assert "normal Agent-loop progression" in class_doc
    assert "ordered_ids = tuple(plan.ordered_tool_call_ids)" in source
    assert "await self._require_exact_active_fence(" in source
    assert "return tuple(" in source


def test_r12_f3b_planner_forbids_replaying_old_inference_from_active_tool_cut():
    source = inspect.getsource(
        AgentRecoveryPlanningService._freeze_inference_identity_in_uow
    )

    assert RecoveryInferenceDisposition.NO_INFERENCE.value == "NO_INFERENCE"
    assert '"RECOVERY_INFERENCE_CUT_UNPROVEN"' in source
    assert "if not frozen_tool_call_ids:" in source
    assert "RecoveryInferenceDisposition.NO_INFERENCE" in source
    assert "inference_request_id is None" in source


def test_r12_f3b_normal_agent_mints_fresh_inference_after_iteration_start():
    source = inspect.getsource(AgentRuntime._execute_loop)

    request_assign = source.index('request_id = f"inf_{uuid.uuid4().hex}"')
    persist_id = source.index("await self._persist_iteration(record)", request_assign)
    provider_call = source.index("response = await self._inference.complete(", persist_id)
    checkpoint = source.index(
        "await self._persist_execution_checkpoint(",
        provider_call,
    )

    assert request_assign < persist_id < provider_call < checkpoint
    assert "record.inference_request_id = request_id" in source
    assert "self._task_budget_service.reserve_inference(" in source
    assert "inference_response=response" in source


def test_r12_f3b_generic_initial_tool_results_are_not_a_durable_handoff():
    source = inspect.getsource(AgentRuntime._execute_loop)

    assert "latest_tool_results: tuple[ToolExecutionResult, ...] = tuple(" in source
    assert "initial_tool_results" in source
    assert "tool_results=[]" in source
    assert "transcript.extend(_tool_results_to_messages(latest_tool_results))" in source

    text = _read(DOC)
    assert "Passing arbitrary" in text
    assert "initial_tool_results" in text
    assert "is not sufficient by itself" in text


def test_r12_f3b_current_checkpoint_update_is_not_atomic_recovery_fenced():
    source = inspect.getsource(DurableAgentStore.update_checkpoint)

    assert "get_execution(execution_id)" in source
    assert "update_execution(execution_id, values)" in source
    assert "_lock_recovery_projection_fence_in_uow" not in source
    assert "expected_lease_expires_at" not in source

    text = _read(DOC)
    assert "se/src/runtimes/agent/persistence.py" in text
    assert "sequence is forbidden" in text
    assert "atomic exact-lease durable handoff/checkpoint" in text


def test_r12_f3b_provider_adapter_has_no_recovery_attempt_fence_yet():
    source = inspect.getsource(ProviderInferenceAdapter.complete)

    assert "handler.execute_with_fallback(" in source
    assert "provider_task = asyncio.create_task(" in source
    assert "recovery_dispatch_guard" not in source
    assert "pre_attempt_guard" not in source


def test_r12_f3b_fallback_layer_can_dispatch_multiple_provider_executors():
    source = inspect.getsource(ChatExecutionHandler.execute_with_fallback)

    assert "for provider in healthy_execution_chain:" in source
    assert "response = await self.executor.execute(" in source
    assert "continue" in source
    assert "pre_attempt_guard" not in source
    assert "recovery_dispatch_guard" not in source


def test_r12_f3b_retry_layer_physical_attempt_seam_has_no_r12_guard():
    source = inspect.getsource(ProviderExecutor.execute)

    assert "async def execution_func():" in source
    assert "return await provider.chat.chat(**attempt_kwargs)" in source
    assert "return await provider.chat.chat(**bounded_kwargs)" in source
    assert "self.retry_policy.apply(" in source
    assert "pre_attempt_guard" not in source
    assert "recovery_dispatch_guard" not in source


def test_r12_f3b_preserves_ae_r10_deadline_dominance():
    source = inspect.getsource(await_with_provider_deadline)

    terminal_capture = source.index("terminal_error = error")
    deadline_recheck = source.index(
        "if call_budget.remaining_seconds(",
        terminal_capture,
    )
    terminal_reraise = source.index(
        "if terminal_error is not None:",
        deadline_recheck,
    )

    assert terminal_capture < deadline_recheck < terminal_reraise

    text = " ".join(_read(DOC).split())
    assert "AE-R10 logical deadline authority remains stronger" in text
    assert "ProviderDeadlineExceededError remains dominant" in text
    assert "MUST NOT mask an already-expired AE-R10 logical deadline" in text
    assert "same canonical AE-R10 `ProviderCallBudget`" in text
    assert "live-budget guard-loss classification survives provider wrapping unchanged" in text
    assert "post-guard boundary" in text


def test_r12_f3b_contract_freezes_final_ambiguous_outcome_decision():
    text = _read(DOC)

    assert "F3-B does not add a durable provider-attempt/outcome ledger" in text
    assert "RECOVERY_INFERENCE_CUT_UNPROVEN" in text
    assert "DEFER" in text
    assert "NO BLIND REPLAY" in text
    assert "F3-B deliberately sacrifices liveness" in text


def test_r12_f3b_contract_freezes_post_provider_fence_before_taskbudget():
    text = _read(DOC)

    assert "TaskBudgetService.account_usage" in text
    assert "no stale-owner cleanup exemption" in text
    assert "preserve provider / AE-R10 / UBQ-4 / CAS truth" in text
    assert "skip stale-owner TaskBudget and Agent progression" in text


def test_r12_f3b_contract_freezes_exact_production_set_and_exclusions():
    text = _read(DOC)

    required = (
        "se/src/runtimes/agent/recovery_execution.py",
        "se/src/runtimes/agent/runtime.py",
        "se/src/runtimes/agent/persistence.py",
        "se/src/runtimes/agent/task_budget.py",
        "se/src/runtimes/agent/contracts/inference.py",
        "se/src/runtimes/agent/adapters/inference.py",
        "se/src/provider/handlers/chat_handler.py",
        "se/src/provider/executor.py",
        "se/src/provider/exceptions.py",
    )
    for path in required:
        assert path in text

    assert "se/src/main.py" in text
    assert "se/src/application/container.py" in text
    assert "schema/Alembic migrations" in text
    assert "new provider-outcome ledger" in text


def test_r12_f3b_contract_records_all_preclaim_findings_without_self_closure():
    text = _read(DOC)

    required = (
        "P1-R12-F3B-ORCHESTRATION-HANDOFF-1",
        "P1-R12-F3B-PER-ATTEMPT-LEASE-FENCE-2",
        "P1-R12-F3B-INFERENCE-AMBIGUOUS-OUTCOME-3",
        "P1-R12-F3B-POSTPROVIDER-PROGRESSION-FENCE-4",
        "P1-R12-F3B-NEXT-TOOL-DISPATCH-FENCE-5",
        "P1-R12-F3B-TASKBUDGET-MUTATION-FENCE-6",
        "independent closure = PENDING",
        "No owner self-closes an independent gate.",
        "F3-B production CLAIM = CLOSED",
    )
    for item in required:
        assert item in text


def test_r12_f3b_cas_projection_precedes_provider_executor_on_current_main():
    source = inspect.getsource(ChatExecutionHandler.execute_with_fallback)

    projection_predicate = source.index(
        "asset_attempt_terminal = ("
    )
    projection_call = source.index(
        "attempt_body = await self._project_asset_attempt(",
        projection_predicate,
    )
    executor_call = source.index(
        "response = await self.executor.execute(",
        projection_call,
    )

    assert projection_predicate < projection_call < executor_call


def test_r12_f3b_contract_defers_asset_bearing_recovery_before_projection():
    text = _read(DOC)

    assert "CAS-F5-D pre-send side-effect boundary" in text
    assert "FAIL CLOSED / DEFER BEFORE _project_asset_attempt()" in text
    assert "ZERO CanonicalAssetHydrationService.hydrate()" in text
    assert "ZERO provider.files.upload_file_outcome()" in text
    assert "ZERO new/updated provider binding" in text
    assert "Ordinary non-recovery F5-D projection/hydration remains unchanged." in text


def test_r12_f3b_contract_freezes_non_stream_only_scope():
    text = _read(DOC)

    assert "F3-B recovery provider progression is **NON-STREAM ONLY**." in text
    assert "ChatExecutionHandler.stream_with_fallback = OUT OF SCOPE / UNCHANGED" in text
    assert "ProviderExecutor.execute_stream = OUT OF SCOPE / UNCHANGED" in text
    assert "GeneratedAssetStreamAssembler.observe = UNCHANGED" in text
    assert "GeneratedAssetStreamAssembler.finalize = UNCHANGED" in text
    assert "GeneratedAssetStreamAssembler.media_seen = UNCHANGED" in text
    assert "Any future recovery support for streaming is a separate MATERIAL CAS overlap" in text


def test_r12_f3b_contract_keeps_cas_bilateral_release_preserved():
    text = _read(DOC)

    assert "P1-CAS-R12-F3B-F5D-PRESEND-SIDE-EFFECT-1" in text
    assert "P1-CAS-R12-F3B-STREAM-SCOPE-2" in text
    assert text.count("independent CAS closure = CLOSED / PASS / PRESERVED") >= 2
    assert "refresh is required only if CAS semantics/path scope changes" in text


def test_r12_f3b_contract_freezes_tri_state_durable_handoff_replay():
    text = _read(DOC)

    assert "strict tri-state inside the SAME locked UoW" in text
    assert "durable state == exact frozen pre-handoff checkpoint/snapshot" in text
    assert "durable state == exact deterministic F3-B handoff" in text
    assert "IDEMPOTENT REUSE" in text
    assert "any other revision/checkpoint/transcript/handoff identity" in text
    assert "FAIL CLOSED / CONFLICT / DEFER" in text
    assert "never a caller-generated marker" in text
    assert "requires no new schema" in text


def test_r12_f3b_contract_requires_fresh_fence_per_public_or_durable_boundary():
    text = _read(DOC)

    assert "a prior fence never authorizes a later boundary across an" in text
    assert "Every externally visible recovery-owner publication/dispatch requires" in text
    assert "Every durable active-owner mutation requires the exact fence in the SAME UoW" in text
    assert "A successful fence at one item above does not authorize the next item." in text
    assert "ITERATION_COMPLETED" in text
    assert "EXECUTION_COMPLETED" in text


def test_r12_f3b_ordinary_fresh_tool_stack_has_no_recovery_send_guard():
    runtime_source = inspect.getsource(AgentRuntime._execute_loop)
    coordinator_source = inspect.getsource(AgentToolExecutionCoordinator.execute_many)

    assert "response.message.tool_calls" in runtime_source
    assert "self._tool_execution.execute_many(" in runtime_source
    assert "recovery_dispatch_guard" not in coordinator_source
    assert "continuation_dispatch_guard" not in coordinator_source


def test_r12_f3b_contract_defers_fresh_recovery_tool_calls_before_admission():
    text = _read(DOC)

    assert "Fresh next-inference tool calls — Option A / DEFER" in text
    assert "fresh recovered next-inference response contains tool_calls" in text
    assert "FAIL CLOSED / DEFER before any NEW logical tool-call construction" in text
    assert "zero new logical tool dispatch" in text
    assert "ordinary tool-dispatch scope expansion = NO" in text
    assert "ordinary fresh-tool dispatch seams for recovered next-inference tool_calls" in text


def test_r12_f3b_contract_records_current_ctx210_as_non_material():
    text = _read(DOC)

    assert "CTX-F5-3I-B3 production PR #210 is LANDED at current main" in text
    assert "f577fb370f1a73a8fdcc69e4221ac41c925a0338" in text
    assert "classified NON_MATERIAL inbound to this F3-B contract" in text



def test_r12_f3b_taskbudget_mutations_currently_lack_recovery_fence_input():
    reserve = inspect.getsource(TaskBudgetService.reserve_inference)
    usage = inspect.getsource(TaskBudgetService.account_usage)

    assert "_mutate_with_reservation(" in reserve
    assert "_mutate_with_reservation(" in usage
    assert "recovery_fence" not in reserve
    assert "recovery_fence" not in usage
    assert "expected_incarnation_generation" not in reserve


def test_r12_f3b_contract_requires_taskbudget_same_uow_recovery_fence():
    text = _read(DOC)

    assert "Task-scoped inference accounting — exact same-UoW recovery fence" in text
    assert "plan.task_budget_incarnation_generation" in text
    assert "se/src/runtimes/agent/task_budget.py" in text
    assert "lock/load the AgentExecution row" in text
    assert "current TaskBudget incarnation_generation equals the frozen plan" in text
    assert "rollback all TaskBudget mutation/reservation work if the R12 fence fails" in text
    assert "no quota refund/release lifecycle" in text
    assert "exact maximum production set of **nine** files" in text


def test_r12_f3b_taskbudget_scope_preserves_ubq_authority():
    text = _read(DOC)

    assert "P1-R12-F3B-TASKBUDGET-MUTATION-FENCE-6" in text
    assert "ADD task_budget.py / SAME-UoW EXACT R12 FENCE" in text
    assert "UBQ bilateral closure = PENDING" in text
    assert "does not redefine renewable UBQ accounting" in text



def test_r12_f3b_current_task_terminal_transition_lacks_recovery_fence():
    finish = inspect.getsource(TaskBudgetService.finish_task_scoped_execution)
    transition = inspect.getsource(TaskBudgetService._transition_execution_with_budget)

    assert "_transition_execution_with_budget(" in finish
    assert "compare_and_set_task_budget(" in transition
    assert "compare_and_set_execution(" in transition
    assert "recovery_fence" not in finish
    assert "recovery_fence" not in transition


def test_r12_f3b_contract_fences_task_and_non_task_terminal_commits():
    text = _read(DOC)

    assert "Task-scoped recovery terminalization is governed by the same rule." in text
    assert "finish_task_scoped_execution()" in text
    assert "_transition_execution_with_budget()" in text
    assert "zero TaskBudget capacity release and zero AgentExecution terminalization" in text
    assert "DurableAgentStore.compare_and_set_execution()" in text
    assert "An outer runtime check is not durable commit authority." in text
