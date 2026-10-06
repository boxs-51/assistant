from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "ops" / "ae_r13"


def test_r13_c_rollout_operator_bundle_is_bounded_and_read_only():
    collector_path = OPS / "r13c_collect_evidence.py"
    probe_path = OPS / "r13c_client_reconnect_probe.py"
    pipeline_path = OPS / "run_r13c_rollout_evidence.ps1"

    collector = collector_path.read_text(encoding="utf-8")
    probe = probe_path.read_text(encoding="utf-8")
    pipeline = pipeline_path.read_text(encoding="utf-8")

    compile(collector, str(collector_path), "exec")
    compile(probe, str(probe_path), "exec")

    assert "tools={}" in probe
    assert ".chat(" not in probe
    assert "resume_execution(" not in probe
    assert "print(runtime.client_id" not in probe
    assert "print(runtime.owner_id" not in probe
    assert "print(runtime.connection_id" not in probe

    assert "?mode=ro" in collector
    assert "PRAGMA query_only = ON" in collector
    assert "SET TRANSACTION READ ONLY" in collector
    assert "DELETE FROM" not in collector
    assert "UPDATE agent_executions" not in collector
    assert "INSERT INTO agent_executions" not in collector
    assert "5a75ac1483e15fecb91fbdeaa8626e5b62c4c54e" in collector

    assert "$PSScriptRoot" in pipeline
    assert "r13c_client_reconnect_probe.py" in pipeline
    assert "r13c_collect_evidence.py" in pipeline
    assert "R13C_PIPELINE=PRELIMINARY_PASS" in pipeline
    assert "R13C_PIPELINE=HOLD" in pipeline


def _cycle(
    *,
    candidate_count: int,
    continuation_candidate_count: int,
    missing_continuation_count: int,
    attempted_count: int,
    materialized_count: int,
    rejected_count: int,
):
    return {
        "start_utc": "2026-10-06T00:00:00Z",
        "end_utc": "2026-10-06T00:01:00Z",
        "inventory_event_count": 1,
        "batch_event_count": 1,
        "failed_event_count": 0,
        "candidate_count": candidate_count,
        "continuation_candidate_count": continuation_candidate_count,
        "missing_continuation_count": missing_continuation_count,
        "attempted_count": attempted_count,
        "materialized_count": materialized_count,
        "rejected_count": rejected_count,
        "error_types": [],
        "sanitized_events": [],
    }


def _classify(cycle_a, cycle_b):
    import argparse
    import runpy

    collector = runpy.run_path(str(OPS / "r13c_collect_evidence.py"))
    args = argparse.Namespace(
        deployed_sha=collector["EXPECTED_C0_SHA"],
        expected_sha=collector["EXPECTED_C0_SHA"],
        server_restart_count=1,
        log_sampling="NONE",
    )
    inventory = {
        "active_legacy_waiting_without_checkpoint": 0,
        "continuation_mapping_present": 0,
        "continuation_missing_or_non_mapping": 0,
    }
    return collector["classify"](
        cycle_a,
        cycle_b,
        inventory,
        args,
        [],
        [],
    )


def test_r13_c_rollout_operator_bundle_rejects_unexplained_candidate_attempt_gap():
    cycle_a = _cycle(
        candidate_count=2,
        continuation_candidate_count=2,
        missing_continuation_count=0,
        attempted_count=1,
        materialized_count=1,
        rejected_count=0,
    )
    cycle_b = _cycle(
        candidate_count=0,
        continuation_candidate_count=0,
        missing_continuation_count=0,
        attempted_count=0,
        materialized_count=0,
        rejected_count=0,
    )

    result, reasons = _classify(cycle_a, cycle_b)

    assert result == "HOLD"
    assert any("selected-candidate/attempted gap" in reason for reason in reasons)


def test_r13_c_rollout_operator_bundle_keeps_valid_mode_z_and_mode_c():
    zero = _cycle(
        candidate_count=0,
        continuation_candidate_count=0,
        missing_continuation_count=0,
        attempted_count=0,
        materialized_count=0,
        rejected_count=0,
    )
    mode_z, mode_z_reasons = _classify(zero, zero)
    assert mode_z == "PASS-MODE-Z"
    assert mode_z_reasons == []

    cycle_a = _cycle(
        candidate_count=2,
        continuation_candidate_count=2,
        missing_continuation_count=0,
        attempted_count=2,
        materialized_count=2,
        rejected_count=0,
    )
    mode_c, mode_c_reasons = _classify(cycle_a, zero)
    assert mode_c == "PASS-MODE-C"
    assert mode_c_reasons == []
