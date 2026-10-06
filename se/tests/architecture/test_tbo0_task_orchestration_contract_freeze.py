from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs/task_budget_orchestration/"
    "TBO_0_TASK_ORCHESTRATION_CONTRACT_FREEZE_1CD05AD3.md"
)
ROADMAP = ROOT / "docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md"
MULTI_AGENT = ROOT / "se/src/domain/schemas/multi_agent.py"
TASK_SQL = ROOT / "se/src/infrastructure/storage/models/sql/agent/task.py"
EXECUTION_SCHEMA = ROOT / "se/src/domain/schemas/agent_execution.py"
TIMEOUT_CONTRACT = ROOT / "docs/agent_timeout_contract.md"
UBQ_REFREEZE = ROOT / "docs/user_budget_quota/USER_RESOURCE_BUDGET_TIMEOUT_REFREEZE.md"
PROVIDER_RETRY = ROOT / "se/src/provider/retry_contracts.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_tbo0_freeze_binds_opening_authority_and_exact_two_file_scope() -> None:
    text = _read(DOC)

    assert "Issue #150" in text
    assert "Issue #85 v2.5" in text
    assert "#6022242619 / PASS" in text
    assert "#6022371229" in text
    assert "main@1cd05ad3e5c390dfcc1612a49ad34474a2c894c8" in text
    assert "Production/runtime/schema/migration/API/client delta:** ZERO" in text
    assert "Production CLAIM:** NONE" in text

    owned = (
        "docs/task_budget_orchestration/"
        "TBO_0_TASK_ORCHESTRATION_CONTRACT_FREEZE_1CD05AD3.md",
        "se/tests/architecture/test_tbo0_task_orchestration_contract_freeze.py",
    )
    for path in owned:
        assert path in text

    assert "No third path is authorized." in text
    assert "docs/ROADMAP_NAMESPACE_REGISTRY.md = EXCLUDED FROM TBO-0" in text
    assert "UBQ-5F/T-5 terminology cleanup     = EXCLUDED FROM TBO-0" in text


def test_tbo0_binds_current_task_status_and_persistence_surface() -> None:
    domain = _read(MULTI_AGENT)
    sql = _read(TASK_SQL)
    contract = _read(DOC)

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

    assert 'WAITING_FOR_CONNECTION = "WAITING"' in domain
    assert 'if values.get("status") == "WAITING_FOR_CONNECTION":' in domain
    assert 'values.setdefault("wait_reasons", ["CONNECTION"])' in domain

    for field in (
        "session_id:",
        "created_by:",
        "assigned_agent_id:",
        "revision:",
        "parent_task_id:",
        "connection_id:",
        "client_id:",
        "status:",
        "wait_reasons:",
        "input:",
        "output:",
        "error:",
    ):
        assert field in sql

    assert "TBO-0 adds no new Task representation and performs no migration." in contract
    assert "Future representation belongs to TBO-1" in contract


def test_tbo0_preserves_ae_waiting_and_recovery_authority() -> None:
    execution = _read(EXECUTION_SCHEMA)
    contract = _read(DOC)

    assert 'WAITING = "WAITING"' in execution
    for reason in (
        'CONNECTION = "CONNECTION"',
        'HUMAN_APPROVAL = "HUMAN_APPROVAL"',
        'DEPENDENCY = "DEPENDENCY"',
        'RESOURCE = "RESOURCE"',
        'EXPLICIT_PAUSE = "EXPLICIT_PAUSE"',
        'RECOVERY = "RECOVERY"',
        'RETRY_BACKOFF = "RETRY_BACKOFF"',
    ):
        assert reason in execution

    assert "WAITING execution requires wait_reason" in execution
    assert "wait_reason is only valid for WAITING execution" in execution

    assert "legal Execution state semantics remain owned by AE" in contract
    assert "redefine AE `WAITING -> RUNNING` resume authority" in contract
    assert "Unknown external outcomes still reconcile through AE-R6 before replay." in contract


def test_tbo0_preserves_timeout_and_provider_deadline_boundaries() -> None:
    limits = _read(EXECUTION_SCHEMA)
    timeout = _read(TIMEOUT_CONTRACT)
    provider = _read(PROVIDER_RETRY)
    contract = _read(DOC)

    assert "task_timeout_seconds: Optional[float]" in limits
    assert "Optional hard wall-clock limit for the task" in limits
    assert "remaining_active_budget_seconds: Optional[float]" in limits

    normalized_timeout = _norm(timeout)
    assert (
        "agent_limits.task_timeout_seconds | optional Task wall-clock horizon | "
        "Task lifecycle deadline, owned with TBO/AE boundary"
        in normalized_timeout
    )
    assert (
        "remaining_active_budget_seconds | durable remaining active execution duration | "
        "compatibility execution-time field"
        in normalized_timeout
    )
    assert "Task lifetime != synchronous response lifetime" in timeout

    assert "class ProviderCallBudget:" in provider
    assert "deadline and retry budget for one logical provider call" in provider

    assert "TBO-0 does not rename or remove any of these fields." in contract
    assert "TBO-0 does not absorb UBQ-5F/T-5 terminology cleanup." in contract


def test_tbo0_freezes_ubq_no_mint_and_activation_ordering() -> None:
    roadmap = _read(ROADMAP)
    ubq = _read(UBQ_REFREEZE)
    contract = _read(DOC)

    assert "TBO = Task Orchestration" in roadmap
    assert "UBQ = User Budget & Quota" in roadmap
    assert "TBO MUST NOT mint renewable user resource quota." in roadmap

    assert "A Task does not own renewable user quota." in ubq
    assert (
        "Opening another client, connection, session, Task, branch or Execution "
        "MUST NOT mint a fresh user resource allowance."
        in _norm(ubq)
    )

    for token in (
        "task_mode = finite | recurring",
        "task_horizon",
        "review_horizon",
        "activation eligibility/cadence references",
        "TBO Task eligibility decision",
        "UBQ resource admission",
        "AE execution admission / activation",
    ):
        assert token in contract

    for invariant in (
        "TBO0-I01",
        "TBO0-I02",
        "TBO0-I03",
        "TBO0-I04",
        "TBO0-I05",
        "TBO0-I06",
        "TBO0-I07",
        "TBO0-I08",
        "TBO0-I09",
        "TBO0-I10",
    ):
        assert invariant in contract


def test_tbo0_exit_gate_preserves_zero_production_boundary() -> None:
    text = _read(DOC)

    for forbidden_scope in (
        "se/src/**",
        "cl/src/**",
        "migrations",
        "SQL schema",
        "runtime/API paths",
        "scheduler code",
        "provider code",
        "roadmap namespace registry",
    ):
        assert forbidden_scope in text

    assert "fresh exact-head Architecture Linux + Windows is GREEN" in text
    assert "independent FINAL finds no blocking TBO-0 P0/P1" in text
    assert "Policy #85 integration governance is satisfied" in text
    assert "No TBO-1 production/schema/migration authority is implied" in text
