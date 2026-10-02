from __future__ import annotations

import inspect
from pathlib import Path

from se.src.provider.executor import ProviderExecutor, await_with_provider_deadline
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.recovery import RecoveryInferenceDisposition
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_execution import AgentRecoveryExecutionService
from se.src.runtimes.agent.recovery_planning import AgentRecoveryPlanningService
from se.src.runtimes.agent.runtime import AgentRuntime


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
    assert "ab20305b02ac0d2ac6af93e7c53de9464962f8a3" in text
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


def test_r12_f3b_deadline_wrapper_current_order_can_rewrite_terminal_error():
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

    text = _read(DOC)
    assert "must not overwrite a completed authority-loss exception" in text
    assert "ProviderDeadlineExceededError" in text


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
        "independent closure = PENDING",
        "No owner self-closes an independent gate.",
        "F3-B production CLAIM = CLOSED",
    )
    for item in required:
        assert item in text
