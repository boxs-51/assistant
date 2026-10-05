from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = (
    ROOT
    / "docs"
    / "agent_execution_r12"
    / "R12_H0_FULL_FAULT_EXIT_MATRIX_FREEZE_522B543E.md"
)


EVIDENCE_BINDINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "H0-01": (
        "se/tests/unit/test_r12_f3b_recovered_next_iteration.py",
        (
            "test_f3b_external_caller_cancellation_preserves_half_open_no_failure",
            "test_f3b_reused_handoff_never_mints_or_sends_fresh_inference",
        ),
    ),
    "H0-02": (
        "se/tests/unit/test_r12_f3a_recovery_execution.py",
        (
            "test_r12_f3a_lease_loss_after_dispatch_preserves_r6_but_blocks_agent_projection",
        ),
    ),
    "H0-03": (
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py",
        ("test_r12_g_restart_fresh_service_consumes_durable_created_claim",),
    ),
    "H0-04": (
        "se/tests/integration/test_r12_e_atomic_recovery_ownership.py",
        ("test_r12_e_iteration_zero_recovery_checkpoint_can_resume",),
    ),
    "H0-05": (
        "se/tests/integration/test_r12_c_execution_lease_authority.py",
        ("test_r12_c_acquire_renew_release_reacquire_preserves_semantics",),
    ),
    "H0-06": (
        "se/tests/integration/test_r12_c_execution_lease_authority.py",
        (
            "test_r12_c_renew_and_fence_fail_closed_for_stale_or_expired_authority",
        ),
    ),
    "H0-07": (
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py",
        (
            "test_r12_g_multi_worker_task_recovery_has_one_winner_and_one_budget_slot",
        ),
    ),
    "H0-08": (
        "se/tests/unit/test_r12_f3a_recovery_execution.py",
        ("test_r12_f3a_initial_fence_loss_causes_zero_external_dispatch",),
    ),
    "H0-09": (
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py",
        (
            "test_r12_g_stale_reconnect_claim_cannot_activate_newer_recovery_cut",
            "test_r12_g_stale_recovery_claim_cannot_activate_after_valid_reconnect",
        ),
    ),
    "H0-10": (
        "se/tests/integration/test_r12_e_atomic_recovery_ownership.py",
        ("test_r12_e_terminalization_before_final_recovery_cas_wins",),
    ),
    "H0-11": (
        "se/tests/integration/test_r9_h_race_matrix.py",
        ("test_r9_h_retry_vs_task_cancel_is_fail_closed",),
    ),
    "H0-12": (
        "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py",
        (
            "test_r6_e5_running_after_client_restart_reconciles_unknown_and_never_reexecutes",
        ),
    ),
    "H0-13": (
        "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py",
        (
            "test_r6_e6_idempotent_unknown_outcome_uses_same_invocation_for_controlled_replay",
        ),
    ),
    "H0-14": (
        "se/tests/providers/test_r10_h_exit_matrix.py",
        ("test_r10_h_public_stream_visible_failure_sequence_is_terminal_no_replay",),
    ),
    "H0-15": (
        "se/tests/integration/test_r12_f2_recovery_activation.py",
        ("test_r12_f2_task_scoped_activation_reacquires_capacity_once",),
    ),
    "H0-16": (
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py",
        ("test_r12_g_terminal_execution_rejects_stale_recovery_activation",),
    ),
    "H0-17": (
        "se/tests/integration/test_r12_f2_recovery_activation.py",
        (
            "test_r12_f2_atomic_activation_and_consumed_replay_do_not_remint_lease",
        ),
    ),
    "H0-18": (
        "se/tests/architecture/test_r11_g1_deterministic_load_matrix.py",
        ("test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded",),
    ),
    "H0-20": (
        "se/tests/integration/test_r12_h1a_stale_running_evacuation.py",
        (
            "test_h1a_non_task_expired_running_evacuates_to_waiting",
            "test_h1a_allowlisted_non_task_corruption_evacuates_to_failed",
            "test_h1a_start_is_immediate_periodic_and_quiesce_drains_worker",
        ),
    ),
}


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.is_file(), relative_path
    return path.read_text(encoding="utf-8")


def test_r12_h0_binds_every_inherited_row_to_landed_executable_evidence():
    for row_id, (path, tokens) in EVIDENCE_BINDINGS.items():
        source = _read(path)
        assert row_id in DOC.read_text(encoding="utf-8")
        for token in tokens:
            assert token in source, f"{row_id}: {token} missing from {path}"


def test_r12_h0_matrix_is_complete_and_fail_closed_before_final():
    text = DOC.read_text(encoding="utf-8")

    expected_rows = {f"H0-{index:02d}" for index in range(1, 21)}
    actual_rows = {
        line.split("|")[1].strip()
        for line in text.splitlines()
        if line.startswith("| H0-")
    }

    assert actual_rows == expected_rows
    assert "| H0-20 | Server crash cannot leave zombie RUNNING indefinitely | PASS / COMPOSED |" in text
    assert "H0-19" in text
    assert "PRIOR EXACT-HEAD CI / REFRESH REQUIRED" in text
    assert "37205039948" in text
    assert "If any executable row fails, that row becomes GAP / HOLD." in text


def test_r12_h0_zombie_running_liveness_closure_is_bound_to_h1a():
    chain = {
        "se/tests/integration/test_r12_d1_expired_execution_lease_observation.py": (
            "test_r12_d1_observation_does_not_mutate_durable_execution",
        ),
        "se/tests/integration/test_r12_e_atomic_recovery_ownership.py": (
            "test_r12_e_competing_recoverers_advance_generation_once",
            "test_r12_e_task_budget_release_once_and_cumulative_usage_preserved",
        ),
        "se/tests/integration/test_r12_f2_recovery_activation.py": (
            "test_r12_f2_atomic_activation_and_consumed_replay_do_not_remint_lease",
            "test_r12_f2_task_scoped_activation_reacquires_capacity_once",
        ),
        "se/tests/integration/test_r12_g_recovery_resume_race_matrix.py": (
            "test_r12_g_restart_fresh_service_consumes_durable_created_claim",
            "test_r12_g_multi_worker_task_recovery_has_one_winner_and_one_budget_slot",
            "test_r12_g_terminal_execution_rejects_stale_recovery_activation",
        ),
        "se/tests/integration/test_r12_h1a_stale_running_evacuation.py": (
            "test_h1a_non_task_expired_running_evacuates_to_waiting",
            "test_h1a_allowlisted_non_task_corruption_evacuates_to_failed",
            "test_h1a_waiting_and_failed_same_receipt_have_one_durable_winner",
            "test_h1a_start_is_immediate_periodic_and_quiesce_drains_worker",
        ),
    }

    for path, tokens in chain.items():
        source = _read(path)
        for token in tokens:
            assert token in source

    main = _read("se/src/main.py")
    control_plane = _read("se/src/runtimes/agent/recovery_control_plane.py")
    assert "H1ARecoveryControlPlane" in main
    assert "StaleLeaseScanCoordinator" in main
    assert "await recovery_control_plane.start()" in main
    assert "await recovery_control_plane.quiesce()" in main
    assert "async def start(self) -> H1ASweepReport:" in control_plane
    assert "await self.sweep_once()" in control_plane
    assert "self._run_loop()" in control_plane

    text = DOC.read_text(encoding="utf-8")
    assert "| H0-20 | Server crash cannot leave zombie RUNNING indefinitely | PASS / COMPOSED |" in text
    assert "test_h1a_start_is_immediate_periodic_and_quiesce_drains_worker" in text


def test_r12_h0_preserves_external_outcome_and_checkpoint_authority():
    required = {
        "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py": (
            "OUTCOME_UNKNOWN",
            "never_reexecutes",
            "controlled_replay",
        ),
        "se/tests/providers/test_r10_h_exit_matrix.py": (
            "visible_failure_sequence_is_terminal_no_replay",
        ),
        "se/tests/architecture/test_r11_h_final_exit_matrix.py": (
            "test_r11_h_freezes_every_required_correctness_exit",
            "test_r11_h_keeps_cross_track_and_recovery_authority_closed",
        ),
    }

    for path, tokens in required.items():
        source = _read(path)
        for token in tokens:
            assert token in source
