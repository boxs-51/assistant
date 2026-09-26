from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs/agent_execution_r12/R12_A_HEAD_AUDIT_CRASH_RECOVERY_LEASE_CONTRACT_FREEZE_6228734A.md"
DOMAIN = ROOT / "se/src/domain/schemas/agent_execution.py"
SQL_EXECUTION = ROOT / "se/src/infrastructure/storage/models/sql/agent/execution.py"
SUPERVISOR = ROOT / "se/src/runtimes/agent/supervisor.py"
RUNTIME = ROOT / "se/src/runtimes/agent/runtime.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_r12_a_freeze_binds_exact_claim_baseline_and_two_file_scope() -> None:
    text = _read(DOC)

    assert "Issue #107" in text
    assert "main@6228734ae7a380719bb14fa520e3307c5330aa31" in text
    assert "Architecture #1383 GREEN/GREEN" in text
    assert "Issue #85 v2" in text
    assert "Production/runtime/schema/migration delta:** ZERO" in text

    owned = (
        "docs/agent_execution_r12/"
        "R12_A_HEAD_AUDIT_CRASH_RECOVERY_LEASE_CONTRACT_FREEZE_6228734A.md",
        "se/tests/architecture/test_r12_a_crash_recovery_contract_freeze.py",
    )
    for path in owned:
        assert path in text

    assert "R12-A must not modify production/runtime/schema/migration/client/provider/CAS/CTX files." in text


def test_r12_a_freezes_existing_recovery_primitives_without_claiming_distributed_lease() -> None:
    domain = _read(DOMAIN)
    runtime = _read(RUNTIME)
    supervisor = _norm(_read(SUPERVISOR))
    contract = _read(DOC)

    assert 'RECOVERY = "RECOVERY"' in domain
    assert "async def recover_claimed_resume(" in runtime
    assert "AgentExecutionWaitReason.RECOVERY" in runtime

    assert "process-local reservation token" in supervisor
    assert "must never be confused with a distributed execution lease" in supervisor

    assert "This is inherited R7 behavior." in contract
    assert "It is not a distributed crash lease" in contract
    assert (
        "Process-local supervisor ownership != durable distributed execution lease."
        in contract
    )


def test_r12_a_freezes_current_durable_owner_and_lease_gap_without_future_absence_lock() -> None:
    sql = _read(SQL_EXECUTION)
    contract = _read(DOC)

    for existing_field in (
        "state:",
        "wait_reason:",
        "revision:",
        "current_checkpoint_id:",
        "remaining_active_budget_seconds:",
        "wait_expires_at:",
    ):
        assert existing_field in sql


    # Baseline absence is historical contract evidence, not a permanent live-source invariant.
    assert (
        "It does not currently store `owner_instance_id`, `lease_expires_at`, "
        "or an equivalent durable distributed execution-owner lease surface."
        in contract
    )
    assert "R12-B  durable owner/lease/fence representation + migration" in contract

    for finding in (
        "P0-R12-A-OWNERSHIP-1",
        "P0-R12-A-LEASE-2",
        "P0-R12-A-RECOVERY-3",
        "P0-R12-A-RECONCILIATION-4",
        "P1-R12-A-RACE-5",
        "P1-R12-A-BUDGET-6",
        "P1-R12-A-LEASE-LOSS-FENCING-2",
    ):
        assert finding in contract

    assert "durable lease              = NOT IMPLEMENTED" in contract
    assert "stale-RUNNING coordinator  = NOT IMPLEMENTED" in contract


def test_r12_a_freezes_inherited_authority_and_no_blind_replay() -> None:
    text = _read(DOC)

    inherited = (
        "AE-R6",
        "AE-R7",
        "AE-R8",
        "AE-R9",
        "AE-R10",
        "AE-R11",
        "CapabilityInvocation / ClientInvocationLedger",
        "ResumeClaim",
        "TaskBudget",
        "provider retry/fallback",
        "checkpoint/transcript physical representation",
    )
    for token in inherited:
        assert token in text

    invariants = (
        "R12A-I01",
        "R12A-I02",
        "R12A-I03",
        "R12A-I04",
        "R12A-I05",
        "R12A-I06",
        "R12A-I07",
        "R12A-I08",
        "R12A-I09",
        "R12A-I10",
        "R12A-I11",
        "R12A-I12",
        "R12A-I13",
        "R12A-I14",
        "R12A-I15",
        "R12A-I16",
    )
    for invariant in invariants:
        assert invariant in text

    assert "IN_FLIGHT / OUTCOME_UNKNOWN" in text
    assert "must reconcile through R6 before runtime/model continuation" in text
    assert "replay IN_FLIGHT or OUTCOME_UNKNOWN without R6 reconciliation" in text
    assert "Terminal executions are never recovered or resurrected." in text
    assert "RECOVERY remains a wait_reason" in text
    assert "immediately before every externally visible provider/tool dispatch" in text
    assert "revision CAS alone is not the external-side-effect fence" in text
    assert "stop new external dispatch immediately" in text


def test_r12_a_freezes_cross_track_boundaries() -> None:
    text = _read(DOC)

    for token in (
        "FileAsset/FileBlob/FileReference/FileProviderBinding",
        "physical CAS GC",
        "Memory admission/promotion",
        "Context source lifecycle",
        "A CAS or CTX lifecycle state cannot become proof of stale Agent execution ownership.",
        "PR #105",
        "CAS-F5-D-P1 is landed",
    ):
        assert token in text


def test_r12_a_staged_roadmap_and_final_exit_evidence_are_complete() -> None:
    text = _read(DOC)

    stages = (
        "R12-A  HEAD audit + crash-recovery / lease contract freeze",
        "R12-B  durable owner/lease/fence representation + migration",
        "R12-C  lease acquire / renew / release authority",
        "R12-D  stale-RUNNING classification + scanner",
        "R12-E  atomic recovery ownership + WAITING(RECOVERY) transition",
        "R12-F  pending invocation reconciliation + recovery activation",
        "R12-G  restart / multi-worker / recovery-vs-resume race matrix",
        "R12-H  full fault/exit matrix + final CI/freeze",
    )
    for stage in stages:
        assert stage in text

    required_evidence = (
        "kill worker during provider call",
        "kill worker during remote invocation",
        "restart with orphan durable RUNNING execution",
        "competing recovery workers",
        "paused/partitioned old owner resumes after lease loss and newer recovery ownership",
        "stale old owner cannot dispatch provider/tool side effects after fencing authority changes",
        "recovery vs user RESUME",
        "OUTCOME_UNKNOWN no-blind-replay behavior",
        "TaskBudget active-slot preservation",
        "terminal execution cannot resurrect",
        "no duplicate AgentRuntime activation",
        "recovery through R11 ref-backed checkpoint reconstruction",
        "Linux + Windows full Architecture",
    )
    for evidence in required_evidence:
        assert evidence in text

    assert "Server crash cannot leave a zombie RUNNING execution indefinitely." in text
    assert "Only after R12-A FINAL GREEN may independent audit release R12-B" in text
