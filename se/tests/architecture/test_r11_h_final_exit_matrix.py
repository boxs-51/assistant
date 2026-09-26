from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXIT = ROOT / "docs/agent_execution_r11/R11_H_FINAL_EXIT_MATRIX_EDE34B9F.md"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _table_rows(text: str, header: str) -> dict[str, tuple[str, str]]:
    lines = text.splitlines()
    start = lines.index(header)
    rows: dict[str, tuple[str, str]] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 3
        rows[cells[0]] = (cells[1], cells[2])
    return rows


def test_r11_h_is_pinned_to_exact_claim_and_final_gate():
    text = EXIT.read_text(encoding="utf-8")

    for phrase in (
        "main@ede34b9f495981684edb841d3a8f28aea074ff33",
        "post-wave Architecture #1355 GREEN/GREEN",
        "Architecture #1356 / run `36231568069`",
        "Linux SUCCESS",
        "Windows SUCCESS",
        "No O(N²) checkpoint growth",
        "exact-head Architecture Linux + Windows GREEN",
        "independent exact-head audit",
        "no production/runtime/schema/migration",
    ):
        assert phrase in text

    for stale_marker in ("PRE-CI", "PENDING candidate CI"):
        assert stale_marker not in text


def test_r11_h_disposes_every_r11_a_baseline_dimension():
    text = EXIT.read_text(encoding="utf-8")
    rows = _table_rows(
        text,
        "| R11-A dimension | Final disposition | Rationale / final evidence |",
    )
    expected = {
        "**checkpoint bytes written**": "**IMPROVED**",
        "**resume latency p50/p95/p99**": (
            "**NOT STABLY THRESHOLDABLE with retained measurement evidence**"
        ),
        "**reconstruction latency p50/p95/p99**": (
            "**NOT STABLY THRESHOLDABLE with retained measurement evidence**"
        ),
        "**branch create latency**": (
            "**NOT STABLY THRESHOLDABLE with retained measurement evidence**"
        ),
        "**DB writes per Agent iteration**": "**IMPROVED**",
        "**rows per Task**": "**UNCHANGED / NON-REGRESSED**",
        "**memory per active execution**": (
            "**NOT STABLY THRESHOLDABLE with retained measurement evidence**"
        ),
        "**TaskBudget contention**": "**UNCHANGED / NON-REGRESSED**",
        "**reconstruction depth**": "**IMPROVED**",
        "**SQL statement count**": "**INTENTIONALLY TRADED OFF with rationale**",
        "**flush count**": "**IMPROVED**",
    }

    assert set(rows) == set(expected)
    for dimension, disposition in expected.items():
        actual_disposition, rationale = rows[dimension]
        assert actual_disposition == disposition
        assert rationale

    assert "No R11-A metric disappears from this final matrix." in text


def test_r11_h_freezes_every_required_correctness_exit():
    text = EXIT.read_text(encoding="utf-8")
    rows = _table_rows(
        text,
        "| Required exit evidence | Disposition | Canonical landed evidence |",
    )
    expected = {
        "Linear/bounded checkpoint storage growth": (
            "PASS",
            "test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded",
        ),
        "Bounded reconstruction depth": ("PASS", "max_delta_depth == 9"),
        "Historical inline checkpoint compatibility": (
            "PASS",
            "test_r11_c_legacy_inline_is_canonicalized",
        ),
        "RESUME semantic equivalence": ("PASS", "concurrent RESUME read pressure"),
        "RETRY semantic equivalence": ("PASS", "concurrent RETRY read pressure"),
        "FORK semantic equivalence": (
            "PASS",
            "build_fork_plan() -> consume_fork_plan() -> list_task_branches()",
        ),
        "AGGREGATE semantic equivalence": (
            "PASS / inherited",
            "R9-F durable aggregate activation/restart tests",
        ),
        "Retention / GC safety": ("PASS", "R11-F0/F1/F1-B/F1-C"),
        "Transaction atomicity": (
            "PASS",
            "test_r11_d_non_task_waiting_update_failure_rolls_back_dual_graph",
        ),
        "ClientInvocationLedger retention safety": (
            "PASS",
            "test_r11_f0_generic_ttl_collects_terminal_but_preserves_running",
        ),
        "Full Architecture Linux + Windows": (
            "PASS",
            "Architecture #1356 / run `36231568069`",
        ),
    }

    assert set(rows) == set(expected)
    for requirement, (disposition, evidence_token) in expected.items():
        actual_disposition, evidence = rows[requirement]
        assert actual_disposition == disposition
        assert evidence_token in evidence


def test_r11_h_binds_to_landed_regression_surfaces():
    required = {
        "se/tests/architecture/test_r11_a_persistence_baseline.py": (
            "test_r11_a_real_writer_bytes_and_reconstruction_percentile_red_probe",
        ),
        "se/tests/architecture/test_r11_a_branch_budget_memory_baseline.py": (
            "test_r11_a_memory_per_active_execution_measurement_method",
            "test_r11_a_branch_rows_and_synchronized_contention_red_probe",
            "test_r11_a_direct_task_budget_cas_contention_baseline",
        ),
        "se/tests/architecture/test_r11_a_resume_performance_baseline.py": (
            "test_r11_a_resume_materialize_claim_percentile_red_probe",
        ),
        "se/tests/architecture/test_r11_c_checkpoint_materialization.py": (
            "test_r11_c_legacy_inline_is_canonicalized",
            "test_r11_c_dual_compares_canonical_semantics_not_raw_shape",
            "test_r11_c_runtime_readers_do_not_require_inline_snapshot",
        ),
        "se/tests/architecture/test_r7_b_atomic_waiting.py": (
            "test_r11_d_non_task_waiting_update_failure_rolls_back_dual_graph",
        ),
        "cl/tests/test_r11_f0_client_ledger_retention.py": (
            "test_r11_f0_generic_ttl_collects_terminal_but_preserves_running",
            "test_r11_f0_running_crash_evidence_survives_repeated_far_future_gc",
        ),
        "se/tests/architecture/test_r11_f1_gc_root_closure_contract.py": (
            "test_f1_preserves_r6_client_ledger_terminal_vs_running_fence",
        ),
        "se/tests/architecture/test_r11_f1c_gc_executor.py": (
            "test_r11_f1c_executor_locks_then_revalidates_before_delete",
            "test_r11_f1c_executor_is_fail_closed_on_rowcount_and_scope_drift",
        ),
        "se/tests/architecture/test_r11_g1_deterministic_load_matrix.py": (
            "test_r11_g1_task_budget_contention_stress_preserves_cas_semantics",
            "test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded",
            "test_r11_g1_large_pending_batch_is_atomic_ordered_and_batched",
            "test_r11_g1_concurrent_resume_retry_fork_read_pressure_is_stable",
        ),
        "se/tests/integration/test_r9_f_aggregate.py": (
            "test_r9_f_explicit_aggregate_is_durable_and_never_adopts",
            "test_r9_f_restart_bootstrap_and_single_activation_owner",
        ),
    }

    for path, tokens in required.items():
        source = _text(path)
        for token in tokens:
            assert token in source

    for path in (
        "se/tests/integration/test_r11_d_checkpoint_backfill.py",
        "se/tests/integration/test_r11_d_checkpoint_cutover_migration.py",
        "se/tests/architecture/test_r11_e2_query_plan_baseline.py",
        "se/tests/architecture/test_r11_f0_retention_contract.py",
        "se/tests/architecture/test_r11_f1b_gc_dry_run.py",
        "se/tests/integration/test_r11_f1c_gc_executor.py",
        "se/tests/architecture/test_r11_g_contention_load_performance.py",
    ):
        assert (ROOT / path).is_file()


def test_r11_h_keeps_cross_track_and_recovery_authority_closed():
    text = EXIT.read_text(encoding="utf-8")

    for phrase in (
        "R12 owner-instance",
        "R6 CapabilityInvocation/ClientInvocationLedger lifecycle redefinition",
        "CAS FileAsset/FileBlob/FileReference/FileProviderBinding lifecycle",
        "CTX/Memory lifecycle or promotion authority",
        "Agent transcript refs remain Agent persistence identities.",
        "Policy #85 v2",
        "NON_MATERIAL drift does not force a re-anchor",
        "MATERIAL drift invalidates the affected H gate",
    ):
        assert phrase in text
