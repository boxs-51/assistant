from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXIT = ROOT / "docs/agent_execution_r11/R11_H_FINAL_EXIT_MATRIX_EDE34B9F.md"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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

    dimensions = (
        "checkpoint bytes written",
        "resume latency p50/p95/p99",
        "reconstruction latency p50/p95/p99",
        "branch create latency",
        "DB writes per Agent iteration",
        "rows per Task",
        "memory per active execution",
        "TaskBudget contention",
        "reconstruction depth",
        "SQL statement count",
        "flush count",
    )
    for dimension in dimensions:
        assert f"**{dimension}**" in text

    for disposition in (
        "IMPROVED",
        "UNCHANGED / NON-REGRESSED",
        "INTENTIONALLY TRADED OFF with rationale",
        "NOT STABLY THRESHOLDABLE with retained measurement evidence",
    ):
        assert disposition in text

    assert "No R11-A metric disappears from this final matrix." in text


def test_r11_h_freezes_every_required_correctness_exit():
    text = EXIT.read_text(encoding="utf-8")

    for phrase in (
        "Linear/bounded checkpoint storage growth",
        "Bounded reconstruction depth",
        "Historical inline checkpoint compatibility",
        "RESUME semantic equivalence",
        "RETRY semantic equivalence",
        "FORK semantic equivalence",
        "AGGREGATE semantic equivalence",
        "Retention / GC safety",
        "Transaction atomicity",
        "ClientInvocationLedger retention safety",
        "Full Architecture Linux + Windows",
    ):
        assert phrase in text


def test_r11_h_binds_to_landed_regression_surfaces():
    required = {
        "se/tests/architecture/test_r11_a_persistence_baseline.py": (
            "test_r11_a_real_writer_bytes_and_reconstruction_percentile_red_probe",
        ),
        "se/tests/architecture/test_r11_a_branch_budget_memory_baseline.py": (
            "test_r11_a_memory_per_active_execution_measurement_method",
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
