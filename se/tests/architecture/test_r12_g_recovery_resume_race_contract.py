from __future__ import annotations

import inspect
from pathlib import Path

from se.src.runtimes.agent.contracts.resume import ResumeTriggerType
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.recovery_activation import AgentRecoveryActivationService
from se.src.transport.gateway.api.v1 import events_router


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_G0_RESTART_MULTIWORKER_RECOVERY_RESUME_RACE_CONTRACT_FREEZE_BA2B33CC.md"
)
INTEGRATION = (
    ROOT
    / "se"
    / "tests"
    / "integration"
    / "test_r12_g_recovery_resume_race_matrix.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_r12_g0_keeps_existing_trigger_vocabulary_and_no_new_manual_path():
    assert ResumeTriggerType.CLIENT_RECONNECT.value == "CLIENT_RECONNECT"
    assert ResumeTriggerType.SERVER_RECOVERY.value == "SERVER_RECOVERY"

    router = inspect.getsource(events_router)
    assert "ResumeTriggerType.CLIENT_RECONNECT" in router
    assert "ResumeTriggerType.MANUAL" not in router


def test_r12_g0_reuses_durable_resume_claim_and_final_execution_cas_seams():
    matcher = inspect.getsource(DurableAgentStore._claim_matches_plan)
    reconnect = inspect.getsource(DurableAgentStore._consume_resume_claim_once)
    recovery_once = inspect.getsource(
        DurableAgentStore._consume_recovery_claim_once
    )
    recovery = inspect.getsource(DurableAgentStore.consume_recovery_claim)
    activation = inspect.getsource(AgentRecoveryActivationService.activate)

    assert "ResumeTriggerType.CLIENT_RECONNECT" in matcher
    assert 'record.wait_reason == "CONNECTION"' in matcher
    assert "compare_and_set_waiting_execution" in reconnect
    assert '"CONNECTION"' in reconnect
    assert "compare_and_set_waiting_execution" in recovery_once
    assert '"RECOVERY"' in recovery_once
    assert "acquire_execution_lease" in recovery_once
    assert "ResumeClaimState.CONSUMED" in recovery_once
    assert "_consume_recovery_claim_once" in recovery
    assert "ResumeClaimState.CONSUMED" in activation
    assert "consume_recovery_claim" in activation


def test_r12_g0_contract_freezes_race_and_budget_lineage_invariants():
    text = _read(DOC)
    required = (
        "G0-I01 — one durable winner",
        "G0-I02 — process restart is not authority loss",
        "G0-I03 — CLIENT_RECONNECT and SERVER_RECOVERY cannot both own one cut",
        "G0-I04 — one canonical arbitration seam",
        "G0-I05 — TaskBudget and lineage preservation",
        "G0-I06 — terminal state and external-side-effect fences remain inherited",
        "production/runtime delta = ZERO",
        "R12-G production repair authority = NONE",
        "R12-H authority = NONE",
    )
    for item in required:
        assert item in text


def test_r12_g0_integration_matrix_uses_real_durable_primitives_only():
    text = _read(INTEGRATION)

    required = (
        "DurableAgentStore",
        "AgentRecoveryPlanningService",
        "AgentRecoveryActivationService",
        "consume_resume_claim",
        "commit_recovery_waiting_checkpoint",
        "TaskBudgetService",
        "asyncio.gather",
        "ResumeTriggerType.CLIENT_RECONNECT",
        "ResumeTriggerType.SERVER_RECOVERY",
    )
    for item in required:
        assert item in text

    forbidden = (
        "monkeypatch",
        "FakeResume",
        "FakeRecovery",
        "ResumeTriggerType.MANUAL",
    )
    for item in forbidden:
        assert item not in text


def test_r12_g0_scope_contains_no_production_file_edits_by_contract():
    text = _read(DOC)
    assert "production/runtime delta = ZERO" in text
    assert "schema/migration delta = ZERO" in text
    assert "no second recovery-specific claim lifecycle" in text.lower()
    assert "no new MANUAL API" in text
