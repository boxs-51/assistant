from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_2_HORIZON_LIFECYCLE_CONTRACT_FREEZE_C7EEC511.md"
)
ROADMAP = ROOT / "docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md"
MULTI_AGENT = ROOT / "se/src/domain/schemas/multi_agent.py"
COORDINATOR = ROOT / "se/src/runtimes/agent/coordinator.py"
TASK_BUDGET = ROOT / "se/src/runtimes/agent/task_budget.py"
STATE_MACHINE = ROOT / "se/src/runtimes/agent/state_machine.py"
EXECUTION_SCHEMA = ROOT / "se/src/domain/schemas/agent_execution.py"
TIMEOUT_CONTRACT = ROOT / "docs/agent_timeout_contract.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_tbo2_freeze_binds_authority_and_exact_zero_production_scope() -> None:
    text = _read(DOC)

    assert "Issue #321" in text
    assert "Issue #85 v2.5" in text
    assert "#6031354064 / PASS / RELEASED" in text
    assert "#6031356478" in text
    assert "main@c7eec511a07cf08019ba602f5d271edf8d1f98f5" in text
    assert "Production/runtime/schema/migration/API/client delta:** ZERO" in text
    assert "Production PRE-CLAIM:** HOLD / NOT RELEASED" in text

    owned = (
        "docs/task_budget_orchestration/"
        "TBO_2_HORIZON_LIFECYCLE_CONTRACT_FREEZE_C7EEC511.md",
        "se/tests/architecture/test_tbo2_horizon_lifecycle_contract_freeze.py",
    )
    for path in owned:
        assert path in text

    assert "No third path is authorized." in text
    assert "No production authority is implied by a GREEN contract freeze." in text


def test_tbo2_binds_tbo1_durable_horizon_representation() -> None:
    domain = _read(MULTI_AGENT)
    contract = _read(DOC)

    assert 'class TaskMode(str, Enum):' in domain
    assert 'FINITE = "FINITE"' in domain
    assert 'RECURRING = "RECURRING"' in domain
    assert (
        "task_horizon_at: Optional[float] = "
        "Field(default=None, ge=0, allow_inf_nan=False)"
    ) in domain
    assert (
        "review_horizon_at: Optional[float] = "
        "Field(default=None, ge=0, allow_inf_nan=False)"
    ) in domain

    for status in (
        'CREATED = "CREATED"',
        'ASSIGNED = "ASSIGNED"',
        'RUNNING = "RUNNING"',
        'WAITING = "WAITING"',
        'COMPLETED = "COMPLETED"',
        'FAILED = "FAILED"',
        'CANCELLED = "CANCELLED"',
    ):
        assert status in domain

    assert "No new Task status is introduced by this freeze." in contract


def test_tbo2_freezes_task_and_review_horizon_decisions() -> None:
    contract = _read(DOC)

    assert "due := now >= horizon_at" in contract
    assert "TASK_HORIZON_EXPIRED" in contract
    assert "REVIEW_REQUIRED" in contract
    assert "NULL means no durable TBO Task-horizon activation fence." in contract
    assert "NULL means no review-horizon activation fence." in contract
    assert "TASK_HORIZON_EXPIRED" in contract
    assert "dominates" in contract

    normalized = _norm(contract)
    assert (
        "Horizon expiry blocks activation; it does not by itself: - cancel or "
        "terminalize an already RUNNING Execution"
    ) in normalized
    assert "`REVIEW_REQUIRED` is an internal TBO eligibility disposition only." in normalized
    assert (
        "Reaching the review horizon does not change Task status or revision"
    ) in normalized


def test_tbo2_preserves_ae_waiting_resume_and_terminal_authority() -> None:
    coordinator = _read(COORDINATOR)
    machine = _read(STATE_MACHINE)
    contract = _read(DOC)

    assert "AgentTaskStatus.WAITING" in coordinator
    assert "is WAITING and must resume its" in coordinator
    assert "existing AgentExecution" in coordinator
    assert "AgentExecutionState.WAITING" in machine
    assert "AgentExecutionState.RUNNING" in machine

    normalized = _norm(contract)
    assert "WAITING -> RUNNING AE-owned existing-Execution resume authority" in normalized
    assert "NOT redefined by TBO-2" in contract
    assert "TBO-2 MUST NOT use Task `WAITING` as a generic" in contract
    assert "terminal / no resurrection" in contract


def test_tbo2_identifies_atomic_locked_activation_seam_without_implementing_it() -> None:
    budget = _read(TASK_BUDGET)
    coordinator = _read(COORDINATOR)
    contract = _read(DOC)

    assert "async def transition_task(" in budget
    assert "get_task_for_update(task_id)" in budget
    assert "compare_and_set_task(" in budget
    assert 'allowed_source_states=("ASSIGNED",)' in coordinator
    assert 'target_state="RUNNING"' in coordinator

    normalized = _norm(contract)
    assert "A coordinator-only precheck is insufficient." in contract
    assert (
        "sample injected wall clock exactly once -> evaluate task_horizon_at "
        "-> evaluate review_horizon_at"
    ) in normalized
    assert "no Task mutation, no UBQ reservation, no Execution creation" in contract
    assert "This freeze does not authorize editing `task_budget.py`" in contract


def test_tbo2_freezes_dual_horizon_compatibility_without_reinterpretation() -> None:
    limits = _read(EXECUTION_SCHEMA)
    timeout = _norm(_read(TIMEOUT_CONTRACT))
    contract = _read(DOC)

    assert "task_timeout_seconds: Optional[float]" in limits
    assert "`agent_limits.task_timeout_seconds`" in timeout
    assert "optional Task wall-clock horizon" in timeout
    assert "Task lifecycle deadline, owned with TBO/AE boundary" in timeout

    normalized_contract = _norm(contract)
    assert "TBO-2 activation eligibility reads durable `task_horizon_at`." in normalized_contract
    assert (
        "TBO-2 does not derive `task_horizon_at` from "
        "`task_timeout_seconds`."
    ) in normalized_contract
    assert (
        "TBO-2 does not derive `task_timeout_seconds` from "
        "`task_horizon_at`."
    ) in normalized_contract
    assert "Existing consumers of `task_timeout_seconds` continue unchanged" in normalized_contract
    assert "no implicit numeric precedence conversion" in contract


def test_tbo2_preserves_tbo_ubq_ae_ordering_and_stage_boundaries() -> None:
    roadmap = _read(ROADMAP)
    contract = _read(DOC)

    assert "TBO-2" in roadmap
    assert "Task horizon/review-horizon enforcement and legal lifecycle transitions" in roadmap
    assert "Task horizon expiry blocks new Task activation but does not rewrite UBQ history." in roadmap

    normalized = _norm(contract)
    assert "trigger / request -> TBO Task eligibility -> UBQ resource admission -> AE execution admission / activation" in normalized
    assert "TBO-2 cannot mint, reset, refund, extend, or roll a UBQ usage window." in contract
    assert "TBO-3" in contract
    assert "TBO-4" in contract
    assert "TBO-5" in contract
    assert "TBO-7" in contract


def test_tbo2_fake_clock_matrix_and_exit_gate_are_frozen() -> None:
    contract = _read(DOC)

    for token in (
        "both horizons NULL",
        "task horizon just before",
        "task horizon exactly at",
        "task horizon just after",
        "review horizon just before",
        "review horizon exactly at",
        "review horizon just after",
        "both due",
        "ASSIGNED activation denied",
        "already RUNNING crosses either horizon",
        "terminal Task",
        "concurrent activation near boundary",
    ):
        assert token in contract

    normalized = _norm(contract)
    assert "exact-head Linux + Windows Architecture is GREEN" in normalized
    assert "independent contract FINAL finds no blocking P0/P1/P2" in normalized
    assert "fresh independent audit consider releasing a" in normalized
    assert "production PRE-CLAIM" in normalized
