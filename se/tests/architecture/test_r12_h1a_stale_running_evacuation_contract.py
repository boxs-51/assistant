from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_H1A_STALE_RUNNING_EVACUATION_CONTRACT_683C614F.md"
)
SCANNER = ROOT / "se" / "src" / "runtimes" / "agent" / "stale_lease_scanner.py"
PERSISTENCE = ROOT / "se" / "src" / "runtimes" / "agent" / "persistence.py"
TASK_BUDGET = ROOT / "se" / "src" / "runtimes" / "agent" / "task_budget.py"
AGENT_REPOSITORY = (
    ROOT
    / "se"
    / "src"
    / "infrastructure"
    / "storage"
    / "repositories"
    / "agent.py"
)
SAFE_POINT = (
    ROOT / "se" / "src" / "runtimes" / "agent" / "safe_point_reconstruction.py"
)
CHECKPOINT_TRANSCRIPT = (
    ROOT / "se" / "src" / "runtimes" / "agent" / "checkpoint_transcript.py"
)
RUNTIME = ROOT / "se" / "src" / "runtimes" / "agent" / "runtime.py"
IDENTITY = ROOT / "se" / "src" / "domain" / "schemas" / "identity.py"
AUTHORIZATION = (
    ROOT / "se" / "src" / "application" / "policy" / "authorization.py"
)


def _read(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def test_h1a_freezes_exact_zero_production_candidate_authority() -> None:
    text = _read(CONTRACT)

    required = (
        "Issue #107",
        "Issue #85 v2.5",
        "comment #5981136394",
        "main@683c614f20c7ce1dbf0e9736008eeac33f1c97b1",
        "Architecture #2053",
        "CONTRACT + ARCHITECTURE EVIDENCE ONLY",
        "production/runtime delta in this candidate = ZERO",
        "schema/migration delta = ZERO",
        "config delta = ZERO",
        "H1-A production CLAIM = NONE",
        "merge authority = NONE",
        "R12_H1A_STALE_RUNNING_EVACUATION_CONTRACT_683C614F.md",
        "test_r12_h1a_stale_running_evacuation_contract.py",
    )
    for phrase in required:
        assert phrase in text


def test_h1a_closes_identity_blocker_only_by_stopping_before_reactivation() -> None:
    contract = _read(CONTRACT)
    runtime = _read(RUNTIME)
    identity = _read(IDENTITY)
    authorization = _read(AUTHORIZATION)

    for phrase in (
        "CLOSED FOR H1-A / REMAINS OPEN FOR H1-B",
        "RUNNING -> WAITING(RECOVERY)",
        "RUNNING -> FAILED",
        "automatic SERVER_RECOVERY activation = CLOSED",
        "construction or fabrication of `Identity`",
        "H1-B and CLOSED",
    ):
        assert phrase in contract

    context_start = runtime.index("context_state = {")
    context_end = runtime.index("values = {", context_start)
    context_state_writer = runtime[context_start:context_end]
    assert '"identity"' not in context_state_writer

    for authority_field in (
        "auth_type",
        "roles",
        "permissions",
        "scopes",
        "organization_id",
        "tenant_id",
    ):
        assert authority_field in identity
    assert "required_scopes" in authorization
    assert "required_permissions" in authorization


def test_h1a_consumes_existing_exact_stale_waiting_authority() -> None:
    contract = _read(CONTRACT)
    persistence = _read(PERSISTENCE)
    task_budget = _read(TASK_BUDGET)
    repository = _read(AGENT_REPOSITORY)

    assert "commit_recovery_waiting_checkpoint" in persistence
    assert "recover_task_scoped_execution" in task_budget
    assert "compare_and_set_recovery_waiting_execution" in repository

    for exact_predicate in (
        "AgentExecutionRecord.revision == expected_revision",
        'AgentExecutionRecord.state == "RUNNING"',
        "AgentExecutionRecord.owner_instance_id",
        "== observed_owner_instance_id",
        "AgentExecutionRecord.lease_generation",
        "== observed_lease_generation",
        "AgentExecutionRecord.lease_expires_at",
        "== observed_lease_expires_at",
        "AgentExecutionRecord.lease_expires_at <= takeover_now_utc",
    ):
        assert exact_predicate in repository

    assert "Successful R12-E takeover is final for H1-A" in contract
    assert "H1-A MUST NOT continue into F1/F2/F3" in contract


def test_h1a_freezes_unrecoverable_stale_terminal_disposition() -> None:
    contract = _read(CONTRACT)
    repository = _read(AGENT_REPOSITORY)
    safe_point = _read(SAFE_POINT)
    checkpoint = _read(CHECKPOINT_TRANSCRIPT)

    required_terminal = (
        "CONTRACT-CLOSED / PRODUCTION SEAM STILL UNIMPLEMENTED",
        "RUNNING@N exact stale receipt",
        "-> FAILED@N+1",
        "state = FAILED",
        "revision = source_revision + 1",
        "owner_instance_id = null",
        "lease_expires_at = null",
        "lease_generation = observed_lease_generation + 1",
        "R12_STALE_RECOVERY_UNRECOVERABLE:<reason_code>",
        "SAFE_POINT_INVOCATION_REPOSITORY_MISSING` is explicitly NOT terminalizable",
        "kind = RELEASE_EXECUTION",
        "active_executions -= 1",
        "active_parallel_agents -= 1 only when parent_execution_id != null",
    )
    for phrase in required_terminal:
        assert phrase in contract

    deterministic_safe_point_codes = (
        "SAFE_POINT_TRANSCRIPT_CORRUPT",
        "SAFE_POINT_ACTIVE_BATCH_AMBIGUOUS",
        "SAFE_POINT_CHECKPOINT_MISSING",
        "SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN",
        "SAFE_POINT_INVOCATION_MISSING",
        "SAFE_POINT_ACTIVE_BATCH_AUTHORITY_MISSING",
        "SAFE_POINT_TRANSCRIPT_DIVERGENCE",
    )
    for code in deterministic_safe_point_codes:
        assert code in safe_point
        assert code in contract

    for code in (
        "INVALID_CHECKPOINT_REPRESENTATION_STATE",
        "MISSING_TRANSCRIPT_REPRESENTATION",
        "TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH",
        "TRANSCRIPT_REPRESENTATION_CORRUPT",
        "DUAL_TRANSCRIPT_MISMATCH",
    ):
        assert code in checkpoint
        assert code in contract

    # This candidate freezes the missing seam; it must not pretend production
    # already owns stale-receipt terminalization.
    assert "compare_and_set_expired_execution_terminal" not in repository


def test_h1a_freezes_cross_sweep_cursor_fairness_without_granting_mutation() -> None:
    contract = _read(CONTRACT)
    scanner = _read(SCANNER)

    assert "after_expiry: datetime | None = None" in scanner
    assert "after_execution_id: str | None = None" in scanner
    assert "list_expired_execution_leases" in scanner
    assert "asyncio.create_task" not in scanner

    for phrase in (
        "CONTRACT-CLOSED / PRODUCTION CURSOR STILL UNIMPLEMENTED",
        "(after_expiry, after_execution_id)",
        "lease_expires_at ASC, execution_id ASC",
        "process restart initializes cursor to null",
        "EXHAUSTED",
        "explicit wrap",
        "poison row cannot permanently occupy the prefix",
        "unexpected/transient row failure is isolated",
        "eventually observed after finitely many cursor advances",
    ):
        assert phrase in contract


def test_h1a_freezes_lifecycle_and_external_authority_closed() -> None:
    contract = _read(CONTRACT)

    for phrase in (
        "exactly one H1-A control-plane worker",
        "one immediate bounded sweep",
        "no overlapping sweep tasks",
        "quiesce H1-A first",
        "await worker drain before disposing persistence/storage dependencies",
        "shutdown never starts F1/F2/F3 activation",
        "F1/F2/F3 automatic SERVER_RECOVERY activation",
        "authenticated Identity persistence/reconstruction",
        "R6 invocation reconciliation",
        "Capability routing",
        "CAS generated-media lifecycle",
        "CTX Memory/promotion/Personalization authority",
        "UBQ refund/reset/recharge/accounting policy",
        "schema or migration changes",
    ):
        assert phrase in contract


def test_h1a_exit_gate_requires_fresh_production_preclaim() -> None:
    contract = _read(CONTRACT)
    normalized_contract = " ".join(contract.split())

    assert "fresh independent production PRE-CLAIM" in normalized_contract
    assert "No path is pre-authorized by this planning section." in normalized_contract
    assert "Passing this contract gate does NOT close H0-20." in normalized_contract
    assert "post-merge Architecture is healthy" in normalized_contract
