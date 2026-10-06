from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
