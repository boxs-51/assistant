from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs"
    / "agent_execution_r14"
    / "R14_A_HEAD_AUDIT_FAULT_MATRIX_CONTRACT_FREEZE_10071F4E.md"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _semantic(text: str) -> str:
    return " ".join(text.replace("**", "").replace("\x60", "").split())


def test_r14a_pins_canonical_scope_and_two_file_claim() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    semantic_contract = _semantic(contract)
    roadmap = _text("docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md")

    assert "# Phase R14 — CI / Fault Injection / Production Exit Gates" in roadmap
    assert "Turn tests into architecture evidence." in roadmap
    assert "Critical P0 flows have real integration/E2E tests, not only unit tests." in roadmap

    for suite in (
        "contract",
        "state-machine",
        "identity-lineage",
        "persistence",
        "agent-runtime",
        "capability-runtime",
        "continuation",
        "reconciliation",
        "branching",
        "TaskBudget",
        "provider-retry",
        "real-WebSocket",
        "multi-worker-race",
        "server-restart",
        "client-restart",
        "HITL",
        "fault-injection",
    ):
        assert suite in roadmap
        assert suite in contract

    for fault_point in (
        "before remote dispatch",
        "after dispatch",
        "before side effect",
        "after side effect",
        "before result send",
        "after result send",
        "before SE commit",
        "after SE commit",
        "during resume CAS",
        "during ADOPT CAS",
        "during provider retry",
        "during checkpoint persistence",
        "during TaskBudget reservation",
    ):
        assert fault_point in roadmap
        assert fault_point in contract

    assert "R14-A owns exactly two NEW files" in contract
    assert "There is no third path." in contract
    assert "Production authority: NONE" in semantic_contract
    assert "Fault-hook authority: NONE" in semantic_contract
    assert "Merge authority: NONE" in semantic_contract


def test_r14a_freezes_completed_semantic_mapping_and_bounded_gaps() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    semantic_contract = _semantic(contract)

    for classification in (
        "COVERED_ARCHITECTURE_COMPOSED",
        "COVERED_EXECUTABLE",
        "COVERED_INTEGRATION",
        "COVERED_REAL_E2E",
        "COVERED_COMPONENT_INTEGRATED",
        "COVERED_REAL_E2E_AND_INTEGRATION",
        "COMPOSED_WITH_BOUNDED_GAPS",
        "GAP_EXACT_BOUNDARY",
        "COVERED_TRANSACTIONAL",
        "COMPOSED_REAL_E2E",
    ):
        assert classification in contract

    assert "R14-GAP-REMOTE-PRE-SIDE-EFFECT-1" in contract
    assert "dispatch accepted but local side-effect execution starts" not in contract
    assert "after remote dispatch has been accepted but before local tool execution has begun" in semantic_contract

    assert "R14-GAP-HITL-E2E-1" in contract
    assert "no HITL integration or E2E owner" in contract
    assert "GAP / NO REPAIR AUTHORITY" in contract

    assert "A row classified as a GAP is not implementation authority." in semantic_contract
    assert "R14-A does not close either gap." in contract


def test_r14a_binds_real_remote_fault_evidence_without_overclaiming() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    r6 = _text("se/tests/e2e/test_r6_e_remote_reconciliation_faults.py")

    for test_name in (
        "test_r6_e1_disconnect_before_dispatch_preserves_not_dispatched",
        "test_r6_e2_disconnect_while_non_idempotent_tool_running_blocks_fallback",
        "test_r6_e3_same_process_reconnect_recovers_lost_terminal_once",
        "test_r6_e4_client_process_generation_restart_recovers_terminal_from_sqlite",
        "test_r6_e7_deduplicated_replay_preserves_key_and_external_effect_once",
        "test_r6_e10_late_k1_terminal_cannot_mutate_k2_committed_reconciliation",
        "test_r6_e11_timeout_is_not_rollback_proof_and_late_terminal_cannot_resurrect",
    ):
        assert f"def {test_name}" in r6
        assert test_name in contract

    assert "R6-E1 disconnect before send boundary proves NOT_DISPATCHED" in contract
    assert "R6-E2 disconnect while non-idempotent tool is running" in contract
    assert "R6-E3/E4 persist client terminal truth" in contract
    assert "R6-E10 freezes revision/output after K2 reconciliation" in contract

    assert "No component-level row is promoted to “real E2E”" in contract
    assert "no real external-provider E2E is inferred" in contract


def test_r14a_binds_resume_branch_retry_checkpoint_and_budget_owners() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")

    evidence = {
        "se/tests/e2e/test_r7_j_real_network_exit_gate.py": (
            "test_r7_j_server_restart_while_waiting_resumes_from_sql_only",
            "test_r7_j_lost_accepted_ack_retries_same_request_on_k3",
            "test_r7_j_real_tcp_two_resume_requests_have_one_authority_winner",
        ),
        "se/tests/integration/test_r7_f_resume_claim_atomicity.py": (
            "test_r7_f_claim_cas_loss_rolls_back_execution_budget_and_reservation",
            "test_r7_f_two_distinct_claims_have_at_most_one_consumed_winner",
        ),
        "se/tests/integration/test_r8_d_atomic_fork_consume.py": (
            "test_r8_d_failure_at_each_write_boundary_rolls_back_everything",
            "test_r8_d_concurrent_same_request_has_one_durable_winner",
        ),
        "se/tests/integration/test_r9_h_race_matrix.py": (
            "test_r9_h_adopt_vs_late_loser_completion_freezes_output",
            "test_r9_h_resume_claim_consume_vs_adopt_leaves_no_created_claim",
        ),
        "se/tests/providers/test_r10_c_deadline_retry_policy.py": (
            "test_r10_c_cancellation_during_backoff_consumes_no_retry_token",
            "test_r10_c_deadline_expiring_during_backoff_does_not_consume_token",
        ),
        "se/tests/providers/test_r10_h_exit_matrix.py": (
            "test_r10_h_public_stream_visible_failure_sequence_is_terminal_no_replay",
        ),
        "se/tests/architecture/test_r7_b_atomic_waiting.py": (
            "test_r7_b_execution_update_failure_rolls_back_checkpoint_and_budget_release",
        ),
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py": (
            "test_r12_g_multi_worker_task_recovery_has_one_winner_and_one_budget_slot",
            "test_r12_g_restart_fresh_service_consumes_durable_created_claim",
        ),
    }

    for relative, names in evidence.items():
        source = _text(relative)
        for name in names:
            assert f"def {name}" in source

    for phrase in (
        "during resume CAS",
        "during ADOPT CAS",
        "during provider retry",
        "during checkpoint persistence",
        "during TaskBudget reservation",
        "R7-F ResumeClaim / WAITING-to-RUNNING",
        "R9-H ADOPT / resume / branch-resolution race matrix",
        "R10-C cancellation/deadline during backoff",
        "R7-B and R11-D atomic checkpoint/execution/budget rollback",
        "R5-C atomic reservation/slot transitions",
    ):
        assert phrase in contract


def test_r14a_preserves_r13_ci_and_cross_track_authority_fences() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    semantic_contract = _semantic(contract)
    r13 = _text(
        "docs/agent_execution_r13/"
        "R13_H_COMPATIBILITY_REMOVAL_EXIT_MATRIX_B51F32ED.md"
    )

    for classification in (
        "MIGRATE_FIRST",
        "KEEP_CROSS_TRACK",
        "KEEP_STABLE_ERROR",
        "KEEP_HISTORICAL_MIGRATION",
        "SUPERSEDED_ALREADY",
    ):
        assert classification in r13
        assert classification in contract

    for phrase in (
        "Issue #16 is historical pre-roadmap CI / exit-gate cleanup and is CLOSED.",
        "Architecture Baseline remains the canonical Linux/Windows architecture health owner.",
        "MUST NOT recreate retired duplicate Phase 5.6/5.7-style workflows",
        "zero R14 fault-hook transfer",
        "R14 may not derive #156 sandbox authority",
        "Each later stage requires a fresh current-main audit and its own PRE-CLAIM.",
    ):
        assert phrase in semantic_contract

    assert "R14-B  exact remote pre-side-effect gap closure proposal" in contract
    assert "R14-C  HITL real integration/E2E gap closure proposal" in contract
    assert "R14-D  composed critical-P0 integration/E2E matrix" in contract
    assert "R14-E  final production-exit evidence + handoff" in contract
