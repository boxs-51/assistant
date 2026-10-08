from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_4_ACTIVATION_ORCHESTRATION_CONTRACT_C86BCC5E.md"
)
ROADMAP = ROOT / "docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md"
TBO2 = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_2_HORIZON_LIFECYCLE_CONTRACT_FREEZE_C7EEC511.md"
)
TBO3 = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_3_RESOURCE_EXHAUSTION_HANDOFF_CONTRACT_C711A287.md"
)
TASK_BUDGET = ROOT / "se/src/runtimes/agent/task_budget.py"
COORDINATOR = ROOT / "se/src/runtimes/agent/coordinator.py"
SUPERVISOR = ROOT / "se/src/runtimes/agent/supervisor.py"
R8F = (
    ROOT
    / "docs/agent_execution_r8/"
    "R8_F_EXECUTION_ACTIVATION_CONTROL_PLANE_CONTRACT_FREEZE_79A1CB3C.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_tbo4_contract_binds_exact_zero_production_authority() -> None:
    contract = _read(DOC)

    assert "Issue #381" in contract
    assert "Issue #85 v2.5" in contract
    assert "#6059226191 / PASS / RELEASED" in contract
    assert "#6059231200 / ACTIVE" in contract
    assert "main@c86bcc5e5f5496a77de4613cf9ef933e06391634" in contract
    assert "Production/runtime/schema/migration/repository/API/client delta: ZERO." in contract
    assert "Production PRE-CLAIM: HOLD / NOT RELEASED." in contract

    for path in (
        "docs/task_budget_orchestration/"
        "TBO_4_ACTIVATION_ORCHESTRATION_CONTRACT_C86BCC5E.md",
        "se/tests/architecture/test_tbo4_activation_orchestration_contract.py",
    ):
        assert path in contract

    assert "No third path is authorized." in contract
    assert "No production authority is implied by a GREEN contract freeze." in contract


def test_tbo4_binds_roadmap_stage_and_exit_gate() -> None:
    roadmap = _read(ROADMAP)
    contract = _read(DOC)

    assert "TBO-4" in roadmap
    assert "activation eligibility service and idempotent orchestration decision" in roadmap
    assert "multi-worker/CAS/restart tests" in roadmap

    normalized = _norm(contract)
    assert "activation eligibility service + idempotent orchestration decision" in normalized
    assert "multi-worker / CAS / restart tests" in normalized
    assert (
        "trigger / request -> TBO Task eligibility -> UBQ activation gate "
        "-> AE/R8 execution admission -> process-local execution start"
    ) in normalized


def test_tbo4_preserves_tbo2_locked_horizon_eligibility_seam() -> None:
    budget = _read(TASK_BUDGET)
    coordinator = _read(COORDINATOR)
    tbo2 = _read(TBO2)
    contract = _read(DOC)

    assert "class _TaskActivationEligibilityError" in budget
    assert "async def transition_task(" in budget
    assert "get_task_for_update(task_id)" in budget
    assert "compare_and_set_task(" in budget
    assert "TASK_HORIZON_EXPIRED" in budget
    assert "REVIEW_REQUIRED" in budget

    assert 'allowed_source_states=("ASSIGNED",)' in coordinator
    assert 'target_state="RUNNING"' in coordinator

    assert "A coordinator-only precheck is insufficient." in tbo2
    assert "TBO-2 eligibility remains authoritative" in contract
    assert "TASK_HORIZON_EXPIRED dominates activation" in contract
    assert "REVIEW_REQUIRED denies activation without manufacturing Task WAITING" in contract


def test_tbo4_rejects_process_local_maps_as_distributed_authority() -> None:
    coordinator = _read(COORDINATOR)
    supervisor = _read(SUPERVISOR)
    contract = _read(DOC)

    assert "self._running_tasks: Dict[str, asyncio.Task] = {}" in coordinator
    assert "Own all process-local tasks running" in supervisor
    assert "Durable AgentExecution state remains owned by AgentRuntime." in supervisor
    assert "process-local duplicate exclusion" in supervisor

    assert "MultiAgentCoordinator._running_tasks[task_id]" in contract
    assert "root/legacy process-local convenience only" in contract
    assert "It is not TBO-4 multi-worker idempotency authority." in contract
    assert "AgentExecutionSupervisor remains process-local." in contract
    assert "is not an R12 lease" in contract


def test_tbo4_freezes_activation_decision_identity_and_replay() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "ActivationDecisionKey =" in contract
    assert "(task_id, activation_request_id)" in contract
    assert "opaque immutable idempotency key issued by a trusted trigger adapter" in contract
    assert "same (task_id, activation_request_id) MUST converge on the same durable activation decision" in contract
    assert "replay MUST NOT create a second AgentExecution" in contract
    assert "reusing one activation_request_id for a different Task is a conflict" in contract
    assert "the key is idempotency identity, not authorization" in contract
    assert "ACTIVATION_REPLAY` is a response indicating that a pre-existing" in contract
    assert "not a third durable allow/deny outcome" in contract
    assert "allowed replay preserves the same bound execution" in contract
    assert "denied replay preserves zero executions" in contract

    for disposition in (
        "ACTIVATION_ALLOWED",
        "ACTIVATION_REPLAY",
        "TASK_NOT_ACTIVATION_READY",
        "TASK_ALREADY_ACTIVE",
        "TASK_TERMINAL",
        "TASK_HORIZON_EXPIRED",
        "REVIEW_REQUIRED",
        "DEFER_TO_AE_CONTINUATION",
        "UBQ_ACTIVATION_DEFERRED",
        "UBQ_OWNER_UNRESOLVED",
        "ACTIVATION_CONFLICT",
    ):
        assert disposition in normalized


def test_tbo4_freezes_source_state_matrix_without_stealing_ae_continuation() -> None:
    tbo3 = _read(TBO3)
    contract = _read(DOC)

    assert "RESOURCE_DEFERRED" in tbo3
    assert "AgentExecutionWaitReason.RESOURCE" in tbo3

    assert "WAITING/RESOURCE" in contract
    assert "DEFER_TO_AE_CONTINUATION" in contract
    assert "existing Execution resume remains AE-owned" in contract
    assert "RETRY/FORK/RESUME path" in contract
    assert "TASK_TERMINAL; no resurrection" in contract
    assert "task_mode = RECURRING does not by itself make a WAITING or terminal Task activation-ready" in contract
    assert "TBO-4 does not invent recurrence scheduling/cadence or terminal resurrection" in contract
    assert "It cannot perform the resume transition itself." in contract


def test_tbo4_freezes_non_mutating_ubq_activation_gate() -> None:
    contract = _read(DOC)
    normalized = _norm(contract)

    for token in (
        "UBQ_ACTIVATION_ELIGIBLE",
        "UBQ_ACTIVATION_DEFERRED",
        "UBQ_OWNER_UNRESOLVED",
    ):
        assert token in contract

    assert "non-mutating activation gate" in contract
    assert "creates no UBQ window" in contract
    assert "does not roll a window" in contract
    assert "does not debit, reserve, settle, refund, reset, or mint resource quota" in contract
    assert "unknown future inference/tool demand MUST NOT be guessed at activation time" in contract
    assert "actual resource-specific admission remains in canonical UBQ inference/tool seams" in contract
    assert "independently released UBQ bilateral path" in contract
    assert "An allow decision does not itself grant inference/tool resource quota." in normalized


def test_tbo4_reuses_r8_admission_and_freezes_multiworker_cas_semantics() -> None:
    budget = _read(TASK_BUDGET)
    r8f = _read(R8F)
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "async def start_root_task_scoped_execution(" in budget
    assert "CAS loser" in r8f
    assert "AgentExecutionSupervisor" in r8f

    assert "R8/AE remains the authority that creates or admits the durable AgentExecution." in contract
    assert (
        "bind ActivationDecisionKey -> UBQ non-mutating activation gate "
        "-> R8 atomic root execution admission"
    ) in normalized
    assert "all same-key races: exactly one canonical durable decision" in normalized
    assert (
        "if persisted canonical decision outcome == ALLOW: "
        "exactly one canonical execution binding "
        "at most one local execution start "
        "allowed ACTIVATION_REPLAY observes the original execution binding"
    ) in normalized
    assert (
        "if persisted canonical decision outcome == DENY: "
        "exactly zero execution bindings "
        "exactly zero local execution starts "
        "same-key denied races converge on the same durable denial"
    ) in normalized
    assert "if canonical disposition != ACTIVATION_ALLOWED:" not in contract
    assert "response disposition differs from `ACTIVATION_ALLOWED`" in contract
    assert (
        "A denied request (including terminal, WAITING, horizon-expired, "
        "review-required, UBQ-deferred or unresolved-owner) MUST NOT create "
        "or bind an AgentExecution."
    ) in normalized
    assert (
        "ACTIVATION_REPLAY reports an existing canonical decision; "
        "it is not an independent execution-admission grant."
    ) in normalized
    assert "creates no duplicate AgentExecution" in contract
    assert "performs no winner cleanup mutation" in contract
    assert "different key is never permission to create a sibling root execution" in contract


def test_tbo4_freezes_crash_restart_without_stealing_r12() -> None:
    contract = _read(DOC)

    for token in (
        "before any durable decision/admission commit",
        "after a denial is durably recorded",
        "after decision key is bound to R8 admission but before local Supervisor start",
        "after durable RUNNING activation but before local runtime work",
        "after local start succeeds",
        "after process death with stale RUNNING lease",
    ):
        assert token in contract

    assert "R12 stale-RUNNING recovery authority applies" in contract
    assert "R12 only; TBO-4 cannot adopt/recover by itself" in contract
    assert "committed durable ACTIVATION_ALLOWED" in contract
    assert "no canonical execution binding" in contract
    assert "retry is allowed to mint a new execution" in contract
    assert "fails the TBO-4 contract" in contract


def test_tbo4_preserves_aat_tbo5_boundary_and_exit_gate() -> None:
    tbo3 = _read(TBO3)
    contract = _read(DOC)
    normalized = _norm(contract)

    assert "Automatic timer/event delivery belongs to TBO-5 / AAT handoff" in tbo3
    assert "Automatic timer/event delivery belongs to TBO-5 / AAT handoff." in contract

    for forbidden in (
        "register timers",
        "enqueue wakeups",
        "create AAT events",
        "interpret clock passage as an activation request",
        "grant admission merely because an event is due",
    ):
        assert forbidden in contract

    for token in (
        "same decision key, many workers",
        "replay after process restart",
        "WAITING/RESOURCE",
        "unresolved UBQ owner",
        "Supervisor collision",
        "future AAT duplicate delivery",
    ):
        assert token in contract

    # Every acceptance case must preserve the Section 9 ALLOW-versus-denial split.
    # A denial replay must never create an AgentExecution merely to pass a race test.
    assert (
        "same decision key, one worker, repeated call -> same durable decision; "
        "if persisted outcome ALLOW, the original canonical execution identity "
        "even on ACTIVATION_REPLAY; if persisted outcome DENY, zero execution identities"
    ) in normalized
    assert (
        "same decision key, many workers -> one durable canonical decision; "
        "if persisted outcome ALLOW, exactly one execution binding and at most one "
        "local start even on ACTIVATION_REPLAY; "
        "if persisted outcome DENY, zero execution bindings and zero local starts"
    ) in normalized
    assert (
        "replay after process restart -> same durable decision; "
        "if persisted outcome ALLOW, the original bound execution identity even "
        "when response is ACTIVATION_REPLAY; "
        "if persisted outcome DENY, the same durable denial with zero execution"
    ) in normalized
    assert "same decision key, many workers -> exactly one execution;" not in normalized
    assert "replay after process restart -> same durable decision/execution;" not in normalized

    assert "exact scope remains 2 NEW / 2" in normalized
    assert "exact-head Linux + Windows Architecture is GREEN/GREEN" in normalized
    assert "independent contract FINAL finds no blocking P0/P1/P2" in normalized
    assert "separate TBO-4 production PRE-CLAIM with exact paths" in normalized
