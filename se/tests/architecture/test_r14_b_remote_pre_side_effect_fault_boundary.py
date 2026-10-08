from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
R6_E2E = ROOT / "se" / "tests" / "e2e" / "test_r6_e_remote_reconciliation_faults.py"

CLAIMED_PATHS = (
    "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py",
    "se/tests/architecture/test_r14_b_remote_pre_side_effect_fault_boundary.py",
)

FORBIDDEN_PRODUCTION_PATHS = (
    "cl/src/core/local_capability_executor.py",
    "cl/src/core/capability_dispatcher.py",
    "cl/src/core/client_invocation_ledger.py",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_r14_b_claim_is_exactly_two_test_evidence_paths() -> None:
    assert CLAIMED_PATHS == (
        "se/tests/e2e/test_r6_e_remote_reconciliation_faults.py",
        "se/tests/architecture/test_r14_b_remote_pre_side_effect_fault_boundary.py",
    )
    assert all(path.startswith("se/tests/") for path in CLAIMED_PATHS)

    for relative in FORBIDDEN_PRODUCTION_PATHS:
        assert "R14-B injected" not in _text(relative)


def test_r14_b_binds_real_tcp_post_dispatch_pre_side_effect_fault_owner() -> None:
    source = R6_E2E.read_text(encoding="utf-8")
    name = (
        "test_r14_b_real_tcp_failure_after_running_before_target_entry_"
        "is_terminal_no_replay"
    )

    assert f"def {name}" in source
    assert "_FailAfterRunningLedger(ClientInvocationLedger)" in source
    assert "record = super().mark_running(**kwargs)" in source
    assert "R14-B injected after RUNNING before target callable entry" in source

    for proof in (
        'assert record.terminal_payload["code"] == "LOCAL_EXECUTION_FAILED"',
        'assert committed.error["code"] == "LOCAL_EXECUTION_FAILED"',
        "assert ledger.running_seen.is_set()",
        "assert ledger.failure_count == 1",
        "assert target_calls == []",
        "assert server_calls == []",
        "assert len(attempts) == 1",
        "RemoteOutcomeState.TERMINAL_COMMITTED",
        "RemoteReconciliationStatus.TERMINAL",
        'reconciled.terminal_type.value == "error"',
        "class _CloseBeforeErrorRealtime(GatewayRealtimeClient):",
        'assert raised.value.code == REMOTE_OUTCOME_UNKNOWN',
        "persisted.state is CapabilityInvocationState.WAITING",
        "persisted.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN",
        'if envelope.get("type") == "capability.reconcile":',
        "message_observer=capture_reconcile,",
        'assert len(observed_frames) == 1',
        'assert observed_frames[0]["type"] == "capability.reconcile"',
        'assert observed_frames[0]["invocation_id"] == invocation_id',
        'assert observed_frames[0]["connection_id"] == second.connection_id',
        '"request_fingerprint": record.request_fingerprint,',
        "committed.state is CapabilityInvocationState.FAILED",
    ):
        assert proof in source


def test_r14_b_preserves_parent_gap_and_zero_production_fence() -> None:
    parent = _text(
        "docs/agent_execution_r14/"
        "R14_A_HEAD_AUDIT_FAULT_MATRIX_CONTRACT_FREEZE_10071F4E.md"
    )
    source = R6_E2E.read_text(encoding="utf-8")

    assert "R14-GAP-REMOTE-PRE-SIDE-EFFECT-1" in parent
    assert "GAP / NO REPAIR AUTHORITY" in parent
    assert "R14-GAP-HITL-E2E-1" in parent

    assert "GatewayRealtimeClient" in source
    assert "_start_uvicorn(app)" in source
    assert "handle_inbound()" not in source
