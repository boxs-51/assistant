from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_H0_FULL_FAULT_EXIT_MATRIX_FREEZE_522B543E.md"
)
INTEGRATION = (
    ROOT
    / "se"
    / "tests"
    / "integration"
    / "test_r12_h0_full_fault_exit_matrix.py"
)


def _read(path: Path) -> str:
    assert path.is_file()
    return path.read_text(encoding="utf-8")


def test_r12_h0_freezes_exact_release_and_zero_production_authority():
    text = _read(DOC)

    required = (
        "Issue #107",
        "Issue #85 v2.5",
        "comments #5979088017 and #5979528597",
        "main@522b543e66f309085729e57a2248258b7c4f2599",
        "main@854ae02d3f896c5f40a51bd2734e2eb46992493e",
        "main@c6b312342509de00eb24f27e1bc435bd5d0d9400",
        "production/runtime delta = ZERO",
        "schema/migration delta = ZERO",
        "TaskBudget/ResumeClaim/lease production delta = ZERO",
        "R12-H production repair authority = NONE",
        "merge authority = NONE",
    )
    for phrase in required:
        assert phrase in text


def test_r12_h0_disposes_every_required_fault_exit_row():
    text = _read(DOC)
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| H0-")
    ]

    assert len(rows) == 20
    assert [line.split("|")[1].strip() for line in rows] == [
        f"H0-{index:02d}" for index in range(1, 21)
    ]
    assert sum("| PASS" in line for line in rows) == 20
    assert any(
        "H0-19" in line
        and "PASS / EXACT-HEAD CI" in line
        and "37200717067" in line
        for line in rows
    )


def test_r12_h0_binds_inherited_evidence_without_test_only_authority():
    source = _read(INTEGRATION)

    required = (
        "EVIDENCE_BINDINGS",
        "test_r12_h0_zombie_running_exit_chain_is_composed_from_canonical_seams",
        "test_r12_d1_observation_does_not_mutate_durable_execution",
        "test_r12_e_competing_recoverers_advance_generation_once",
        "test_r12_f2_atomic_activation_and_consumed_replay_do_not_remint_lease",
        "test_r12_g_restart_fresh_service_consumes_durable_created_claim",
        "test_r12_g_terminal_execution_rejects_stale_recovery_activation",
        "OUTCOME_UNKNOWN",
        "visible_failure_sequence_is_terminal_no_replay",
        "test_r11_h_freezes_every_required_correctness_exit",
    )
    for token in required:
        assert token in source

    forbidden = (
        "monkeypatch",
        "FakeRecovery",
        "FakeResume",
        "ResumeTriggerType.MANUAL",
        "test-only arbitration",
    )
    for token in forbidden:
        assert token not in source


def test_r12_h0_contract_keeps_all_external_authority_closed():
    text = _read(DOC)

    required = (
        "lease, scanner, recovery, ResumeClaim or TaskBudget production changes",
        "retry/replay permission from lease expiry or process loss",
        "replay outside R6 IN_FLIGHT/OUTCOME_UNKNOWN authority",
        "provider retry/fallback/deadline/visible-output changes",
        "MANUAL resume API",
        "checkpoint representation or retention changes owned by R11",
        "CAS lifecycle/generated-media",
        "CTX Memory/promotion",
        "UBQ accounting",
        "#156 routing/sandbox authority",
    )
    for phrase in required:
        assert phrase in text


def test_r12_h0_final_gate_is_fail_closed():
    text = _read(DOC)

    assert "H0-19 is updated from PENDING to PASS" in text
    assert "no row is GAP" in text
    assert "independent audit finds no blocking P0/P1/P2" in text
    assert "not R12-H closure" in text
