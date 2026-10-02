from __future__ import annotations

import inspect
from pathlib import Path

from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.recovery import RecoveryInferenceDisposition
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


def test_r12_f3b_contract_freezes_exact_baseline_and_closed_claim():
    text = _read(DOC)

    assert "main@ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12" in text
    assert "R12-F3-A = LANDED / CANONICAL / HEALTHY" in text
    assert "R12-F3-B production CLAIM = CLOSED" in text
    assert "**Production delta:** ZERO" in text


def test_r12_f3b_f3a_stops_before_provider_and_returns_ordered_batch():
    source = inspect.getsource(
        AgentRecoveryExecutionService.execute_active_tool_batch
    )
    class_doc = AgentRecoveryExecutionService.__doc__ or ""

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


def test_r12_f3b_normal_agent_creates_fresh_next_inference_identity():
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


def test_r12_f3b_contract_records_all_preclaim_findings():
    text = _read(DOC)

    required = (
        "P1-R12-F3B-ORCHESTRATION-HANDOFF-1",
        "P1-R12-F3B-PER-ATTEMPT-LEASE-FENCE-2",
        "P1-R12-F3B-INFERENCE-AMBIGUOUS-OUTCOME-3",
        "P1-R12-F3B-POSTPROVIDER-PROGRESSION-FENCE-4",
        "RECOVERY_INFERENCE_CUT_UNPROVEN",
        "No owner implementation may silently choose between those alternatives.",
        "F3-B production CLAIM = CLOSED",
    )
    for item in required:
        assert item in text
