from se.src.runtimes.capability.contracts.reconciliation import (
    RemoteReconciliationResult,
    RemoteReconciliationStatus,
)


def test_restart_unknown_preserves_client_ledger_state():
    result = RemoteReconciliationResult.model_validate(
        {
            "invocation_id": "inv-1",
            "status": "UNKNOWN",
            "capability_id": "tool.side_effect",
            "capability_version": "3.0",
            "request_fingerprint": "f" * 64,
            "ledger_state": "RUNNING",
        }
    )
    assert result.status is RemoteReconciliationStatus.UNKNOWN
    assert result.ledger_state == "RUNNING"


def test_prepared_restart_is_explicit_not_fabricated_terminal():
    result = RemoteReconciliationResult.model_validate(
        {
            "invocation_id": "inv-1",
            "status": "UNKNOWN",
            "capability_id": "tool.side_effect",
            "capability_version": "3.0",
            "request_fingerprint": "f" * 64,
            "ledger_state": "PREPARED",
        }
    )
    assert result.status is RemoteReconciliationStatus.UNKNOWN
    assert result.terminal_type is None
    assert result.terminal_payload is None
