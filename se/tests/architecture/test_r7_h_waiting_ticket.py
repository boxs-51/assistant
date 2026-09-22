from types import SimpleNamespace

import pytest

from se.src.runtimes.agent.waiting_ticket import build_waiting_ticket_payload
from se.src.runtimes.connection.protocol import RealtimeEnvelope


def _execution(**overrides):
    values = {
        "id": "exec-1",
        "state": "WAITING",
        "wait_reason": "CONNECTION",
        "revision": 8,
        "current_checkpoint_id": "cp-1",
        "wait_expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _checkpoint(**overrides):
    values = {
        "checkpoint_id": "cp-1",
        "execution_id": "exec-1",
        "execution_revision": 8,
        "wait_reason": "CONNECTION",
        "wait_expires_at": None,
        "origin_client_id": "client-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_waiting_ticket_projection_uses_normalized_revision_and_all_capabilities():
    payload = build_waiting_ticket_payload(
        _execution(),
        _checkpoint(),
        [
            SimpleNamespace(capability_id="tool.a"),
            SimpleNamespace(capability_id="tool.b"),
            SimpleNamespace(capability_id="tool.a"),
        ],
    )
    assert payload == {
        "execution_id": "exec-1",
        "checkpoint_id": "cp-1",
        "revision": 8,
        "wait_reason": "CONNECTION",
        "wait_expires_at": None,
        "origin_client_id": "client-1",
        "pending_capability_ids": ["tool.a", "tool.b"],
        "auto_resume_allowed": True,
    }


def test_waiting_ticket_projection_fails_closed_on_stale_checkpoint_revision():
    with pytest.raises(ValueError, match="revision"):
        build_waiting_ticket_payload(
            _execution(revision=9),
            _checkpoint(execution_revision=8),
            [SimpleNamespace(capability_id="tool.a")],
        )


def test_recovery_ticket_is_publishable_but_not_auto_resumable():
    payload = build_waiting_ticket_payload(
        _execution(wait_reason="RECOVERY"),
        _checkpoint(wait_reason="RECOVERY"),
        [SimpleNamespace(capability_id="tool.a")],
    )
    assert payload["wait_reason"] == "RECOVERY"
    assert payload["auto_resume_allowed"] is False


def test_execution_waiting_is_a_valid_connection_correlated_envelope():
    envelope = RealtimeEnvelope(
        type="execution.waiting",
        message_id="waiting-1",
        connection_id="conn-2",
        execution_id="exec-1",
        payload={
            "execution_id": "exec-1",
            "checkpoint_id": "cp-1",
            "revision": 8,
            "wait_reason": "CONNECTION",
            "origin_client_id": "client-1",
            "pending_capability_ids": ["tool.a"],
            "auto_resume_allowed": True,
        },
    )
    assert envelope.type == "execution.waiting"
    assert envelope.connection_id == "conn-2"
