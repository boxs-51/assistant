from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_3_RESOURCE_EXHAUSTION_HANDOFF_CONTRACT_C711A287.md"
)
ROADMAP = ROOT / "docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md"
TOOL_QUOTA = ROOT / "se/src/application/user_tool_quota.py"
INFERENCE_QUOTA = ROOT / "se/src/application/user_inference_quota.py"
CAPABILITY_RUNTIME = ROOT / "se/src/runtimes/capability/runtime.py"
TOOL_ADAPTER = ROOT / "se/src/runtimes/agent/adapters/tool.py"
CHAT_HANDLER = ROOT / "se/src/provider/handlers/chat_handler.py"
AGENT_RUNTIME = ROOT / "se/src/runtimes/agent/runtime.py"
EXECUTION_SCHEMA = ROOT / "se/src/domain/schemas/agent_execution.py"
TASK_SCHEMA = ROOT / "se/src/domain/schemas/multi_agent.py"
TASK_BUDGET = ROOT / "se/src/runtimes/agent/task_budget.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_tbo3_contract_binds_authority_and_exact_zero_production_scope() -> None:
    text = _read(DOC)

    assert "Issue #333" in text
    assert "Issue #85 v2.5" in text
    assert "#6033478126 / PASS / RELEASED" in text
    assert "#6033482232 / ACTIVE" in text
    assert "main@c711a2870d6d68be6cafc3d8c29a53fb834b96eb" in text
    assert "Production/runtime/schema/migration/repository/API/client delta:** ZERO" in text
    assert "Production PRE-CLAIM:** HOLD / NOT RELEASED" in text

    owned = (
        "docs/task_budget_orchestration/"
        "TBO_3_RESOURCE_EXHAUSTION_HANDOFF_CONTRACT_C711A287.md",
        "se/tests/architecture/test_tbo3_resource_exhaustion_handoff_contract.py",
    )
    for path in owned:
        assert path in text

    assert "No third path is authorized." in text
    assert "No production authority is implied by a GREEN contract freeze." in text


def test_tbo3_binds_canonical_roadmap_stage_and_no_mint_gate() -> None:
    roadmap = _read(ROADMAP)
    contract = _read(DOC)

    assert "TBO-3" in roadmap
    assert "resource-exhaustion handoff from UBQ into Task continuation policy" in roadmap
    assert "UBQ compatibility + no-mint tests" in roadmap
    assert "resource-exhaustion handoff from UBQ into Task continuation policy" in contract
    assert "UBQ compatibility + no-mint tests" in contract


def test_tbo3_freezes_exact_ubq_exhaustion_vocabulary() -> None:
    tool = _read(TOOL_QUOTA)
    inference = _read(INFERENCE_QUOTA)
    contract = _read(DOC)

    for code in (
        "USER_TOOL_QUOTA_EXHAUSTED",
        "USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED",
    ):
        assert f'code = "{code}"' in tool
        assert code in contract

    for code in (
        "USER_INFERENCE_QUOTA_EXHAUSTED",
        "USER_TOKEN_QUOTA_EXHAUSTED",
        "USER_COMPUTE_QUOTA_EXHAUSTED",
        "USER_COST_QUOTA_EXHAUSTED",
    ):
        assert f'code = "{code}"' in inference
        assert code in contract

    assert "The original UBQ code is immutable provenance" in contract
    assert "No other error is resource exhaustion merely because its message mentions" in contract


def test_tbo3_preserves_capability_scope_vs_task_scope() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED" in normalized
    assert "USER_TOOL_QUOTA_EXHAUSTED" in normalized
    assert "USER_INFERENCE_QUOTA_EXHAUSTED" in normalized
    assert "USER_TOKEN_QUOTA_EXHAUSTED" in normalized
    assert "USER_COMPUTE_QUOTA_EXHAUSTED" in normalized
    assert "USER_COST_QUOTA_EXHAUSTED" in normalized
    assert "Capability-scoped exhaustion denies that capability admission only." in contract
    assert "Task-scoped exhaustion means the requested logical operation cannot proceed" in contract


def test_tbo3_binds_current_tool_and_inference_runtime_asymmetry() -> None:
    capability = _read(CAPABILITY_RUNTIME)
    adapter = _read(TOOL_ADAPTER)
    chat = _read(CHAT_HANDLER)
    runtime = _read(AGENT_RUNTIME)
    contract = _read(DOC)

    assert "UserToolQuotaError" in capability
    assert 'category="QUOTA"' in capability
    assert "normalize_tool_exception" in adapter
    assert "return self._failure(" in adapter

    assert "async def _reserve_inference_quota(" in chat
    assert "return await quota.reserve(" in chat
    assert "Quota errors deliberately remain outside ProviderError/httpx" in chat

    assert "USER_INFERENCE_QUOTA_EXHAUSTED" not in runtime
    assert 'failure_domain = getattr(exc, "failure_domain", None) or "AGENT"' in runtime
    assert "AgentLoopState.FAILED" in runtime

    assert "Current production behavior is intentionally not changed by this freeze:" in contract
    assert "These are different current runtime outcomes." in contract


def test_tbo3_freezes_continuation_dispositions_without_adding_states() -> None:
    task_schema = _read(TASK_SCHEMA)
    execution_schema = _read(EXECUTION_SCHEMA)
    contract = _read(DOC)

    assert "wait_reasons: List[str]" in task_schema
    assert 'RESOURCE = "RESOURCE"' in execution_schema

    for disposition in (
        "CAPABILITY_DENIED",
        "RESOURCE_DEFERRED",
        "RESOURCE_CONTINUATION_UNAVAILABLE",
    ):
        assert disposition in contract

    assert "is a TBO continuation disposition" in contract
    assert "It is not a new AgentTaskStatus or AgentExecutionState." in contract
    assert "does not use Task" in contract
    assert "merely to encode an eligibility denial" in contract


def test_tbo3_preserves_ae_resource_waiting_as_external_authority() -> None:
    execution_schema = _read(EXECUTION_SCHEMA)
    runtime = _read(AGENT_RUNTIME)
    contract = _read(DOC)

    assert 'RESOURCE = "RESOURCE"' in execution_schema
    assert "AgentExecutionState.WAITING" in runtime
    assert "commit_waiting_checkpoint" in runtime
    assert "claim_resume" in runtime

    normalized = _norm(contract)
    assert "AgentExecutionWaitReason.RESOURCE" in normalized
    assert "Its existence is not TBO authority." in normalized
    assert "only through AE-owned authority" in contract
    assert "separately released AE/TBO bilateral production gate" in contract
    assert "cannot create a checkpoint or ResumeClaim" in contract
    assert "There is no third implicit state-machine path." in contract


def test_tbo3_freezes_non_mutating_next_eligibility_contract() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "next_eligibility = KNOWN" in contract
    assert "next_eligibility = UNKNOWN" in contract
    assert "TBO records" in contract
    assert "UNKNOWN" in contract

    for forbidden in (
        "rolling a UBQ window",
        "creating a new window",
        "resetting counters",
        "adding window_duration_seconds to a locally guessed anchor",
        "minting a new session/client/task/execution identity",
    ):
        assert forbidden in normalized

    assert "Automatic timer/event delivery belongs to TBO-5 / AAT handoff" in contract
    assert "TBO-3 does not schedule timers." in contract


def test_tbo3_preserves_taskbudget_resource_demotion() -> None:
    budget = _read(TASK_BUDGET)
    contract = _read(DOC)

    assert "if self._user_tool_quota_enabled:" in budget
    assert "proposed = min(" in budget
    assert "if self._user_inference_quota_enabled:" in budget
    assert "UBQ-4 is the renewable resource authority." in budget
    assert "saturated compatibility counter" in budget

    assert "must not restore renewable resource authority to TaskBudget" in contract
    assert "TaskBudget compatibility counters" in contract
    assert "remain saturated/read-only for canonical UBQ resources" in contract


def test_tbo3_no_mint_idempotency_matrix_is_frozen() -> None:
    contract = _read(DOC)

    for token in (
        "tool total quota exhausted",
        "capability tool quota exhausted",
        "inference-call quota exhausted",
        "token quota exhausted",
        "compute quota exhausted",
        "cost quota exhausted",
        "handoff replay",
        "retry same logical operation",
        "fork",
        "resume",
        "new client/session/connection",
        "known next eligibility",
        "unknown next eligibility",
        "TaskBudget compatibility counters",
        "prior committed result",
        "terminal Task winner",
    ):
        assert token in contract

    assert "No mutation by TBO" in contract
    assert "same logical handoff" in contract
    assert "at most one durable continuation transition" in contract
    assert "zero duplicate UBQ reservation" in contract


def test_tbo3_preserves_tbo2_horizons_and_tbo5_aat_boundary() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "TBO Task horizon/review eligibility" in normalized
    assert "UBQ resource admission" in normalized
    assert "AE activation" in normalized
    assert "Known resource eligibility MUST NOT extend" in contract
    assert "TASK_HORIZON_EXPIRED" in contract

    assert "TBO-3 does not schedule timers." in contract
    assert "TBO-5 / AAT handoff" in contract
    assert "create an AAT event" in contract
    assert "register a timer" in contract
    assert "enqueue a wakeup" in contract


def test_tbo3_contract_exit_gate_keeps_production_authority_closed() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "Production PRE-CLAIM:** HOLD / NOT RELEASED" in contract
    assert "any" in contract and "se/src/**" in contract and "cl/src/**" in contract
    assert "UBQ policy/window/reservation mutation" in contract
    assert "AgentExecution state-machine/WAITING/ResumeClaim/recovery edits" in contract
    assert "TBO-4 activation service" in contract
    assert "TBO-8 rollout/exit" in contract

    assert "exact-head Linux + Windows Architecture is GREEN/GREEN" in normalized
    assert "independent contract FINAL finds no blocking P0/P1/P2" in normalized
    assert "fresh independent audit consider releasing a separate" in normalized
    assert "production PRE-CLAIM" in normalized
