import time
from collections import deque
from types import SimpleNamespace

import pytest

from cl.src.core.client_runtime import ClientRuntime, ClientRuntimeState
from cl.src.core.realtime_client import RealtimeHandshakeError
from cl.src.core.resume_ticket import (
    PendingResumeEntry,
    PendingResumeTicket,
    ResumeProtocolOutcome,
    ResumeTicketState,
)


def _ticket_payload(
    *,
    execution_id="exec-1",
    checkpoint_id="cp-1",
    revision=8,
    client_id="client-1",
    pending=("tool.echo",),
    wait_reason="CONNECTION",
    auto=True,
):
    return {
        "execution_id": execution_id,
        "checkpoint_id": checkpoint_id,
        "revision": revision,
        "wait_reason": wait_reason,
        "wait_expires_at": None,
        "origin_client_id": client_id,
        "pending_capability_ids": list(pending),
        "auto_resume_allowed": auto,
    }


def _accepted(execution_id, checkpoint_id, request_id, revision=9):
    return {
        "type": "execution.resume.accepted",
        "connection_id": "conn-any",
        "execution_id": execution_id,
        "payload": {
            "execution_id": execution_id,
            "checkpoint_id": checkpoint_id,
            "resume_request_id": request_id,
            "claim_id": "claim-1",
            "accepted_revision": revision,
            "state_at_accept": "RUNNING",
        },
    }


def _rejected(execution_id, checkpoint_id, request_id, code, *, retryable=False):
    return {
        "type": "execution.resume.rejected",
        "connection_id": "conn-any",
        "execution_id": execution_id,
        "payload": {
            "execution_id": execution_id,
            "checkpoint_id": checkpoint_id,
            "resume_request_id": request_id,
            "code": code,
            "retryable": retryable,
        },
    }


class _FakeRealtime:
    def __init__(self, connection_id, behavior):
        self.connection_id = connection_id
        self.behavior = behavior
        self.calls = []
        self.timeout = 0.05

    def resume_execution(self, execution_id, checkpoint_id, resume_request_id, **kwargs):
        self.calls.append((execution_id, checkpoint_id, resume_request_id))
        return self.behavior(execution_id, checkpoint_id, resume_request_id)

    def close(self):
        return None


def _wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for R7-H condition")


def _runtime():
    registry = SimpleNamespace(
        settings={"loaded": True},
        tools={
            "tool.echo": {
                "func": lambda **kwargs: kwargs,
                "metadata": {"version": "1.0", "idempotency": "IDEMPOTENT"},
            }
        },
    )
    return ClientRuntime(
        "http://gateway",
        registry,
        client_id="client-1",
        owner_id="user-1",
    )


def _install_ready_transport(runtime, realtime, *, generation=None):
    with runtime._lock:
        if generation is not None:
            runtime._generation = generation
        runtime.connection_id = realtime.connection_id
        runtime.realtime = realtime
        runtime.dispatcher.set_realtime(realtime)
        runtime.capabilities.realtime = realtime
        runtime._confirmed_capability_ids = frozenset({"tool.echo"})
        runtime._ready = True
        runtime._started = True
        runtime._state = ClientRuntimeState.READY
        runtime._stopping = False
        runtime._resume_condition.notify_all()


def test_ticket_contract_allocates_request_id_once_until_explicit_new_attempt():
    ticket = PendingResumeTicket.from_payload(_ticket_payload())
    entry = PendingResumeEntry(ticket=ticket, principal_id="user-1")
    first = entry.ensure_resume_request_id(lambda: "rr-1")
    second = entry.ensure_resume_request_id(lambda: "rr-2")
    assert first == second == "rr-1"

    entry.state = ResumeTicketState.RETRY_SAME_REQUEST
    assert entry.ensure_resume_request_id(lambda: "rr-3") == "rr-1"

    entry.clear_resume_request_for_new_attempt()
    assert entry.ensure_resume_request_id(lambda: "rr-2") == "rr-2"


def test_resume_protocol_outcome_requires_full_correlation():
    outcome = ResumeProtocolOutcome.from_envelope(
        _accepted("exec-1", "cp-1", "rr-1")
    )
    assert outcome.kind == "ACCEPTED"
    assert outcome.execution_id == "exec-1"
    assert outcome.checkpoint_id == "cp-1"
    assert outcome.resume_request_id == "rr-1"
    assert outcome.accepted_revision == 9


def test_auto_resume_waits_for_server_confirmed_capability_ack():
    runtime = _runtime()
    fake = _FakeRealtime(
        "conn-1",
        lambda e, c, r: _accepted(e, c, r),
    )
    _install_ready_transport(runtime, fake)
    with runtime._lock:
        runtime._confirmed_capability_ids = frozenset()

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        time.sleep(0.1)
        assert fake.calls == []

        with runtime._lock:
            runtime._confirmed_capability_ids = frozenset({"tool.echo"})
            runtime._resume_condition.notify_all()

        _wait(lambda: len(fake.calls) == 1)
        _wait(lambda: runtime.pending_resume_tickets == ())
    finally:
        runtime.stop()


def test_lost_ack_retries_same_request_id_on_new_connection_generation():
    runtime = _runtime()
    runtime._suppress_reconnect = True

    def lost_ack(*_):
        raise RealtimeHandshakeError("accepted ACK was lost")

    first = _FakeRealtime("conn-1", lost_ack)
    _install_ready_transport(runtime, first, generation=1)

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(first.calls) >= 1)
        first_request_id = first.calls[0][2]

        with runtime._lock:
            runtime._ready = False
            runtime._state = ClientRuntimeState.DISCONNECTED
            runtime._confirmed_capability_ids = frozenset()

        second = _FakeRealtime(
            "conn-2",
            lambda e, c, r: _accepted(e, c, r),
        )
        _install_ready_transport(runtime, second, generation=2)

        _wait(lambda: len(second.calls) == 1)
        assert second.calls[0][2] == first_request_id
        _wait(lambda: runtime.pending_resume_tickets == ())
    finally:
        runtime.stop()


def test_claim_expired_creates_new_request_id_for_next_known_attempt():
    runtime = _runtime()
    calls = deque()

    def behavior(execution_id, checkpoint_id, request_id):
        calls.append(request_id)
        if len(calls) == 1:
            return _rejected(
                execution_id,
                checkpoint_id,
                request_id,
                "CLAIM_EXPIRED",
                retryable=True,
            )
        return _accepted(execution_id, checkpoint_id, request_id)

    fake = _FakeRealtime("conn-1", behavior)
    _install_ready_transport(runtime, fake)

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(calls) >= 2)
        assert calls[0] != calls[1]
        _wait(lambda: runtime.pending_resume_tickets == ())
    finally:
        runtime.stop()


def test_stale_waiting_event_cannot_resurrect_after_newer_revision():
    runtime = _runtime()
    try:
        with runtime._lock:
            runtime._ready = False
            runtime._state = ClientRuntimeState.DISCONNECTED
        assert runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        with runtime._lock:
            runtime._advance_resume_watermark_locked("exec-1", 9)
        assert runtime.pending_resume_tickets == ()
        assert not runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        assert runtime.pending_resume_tickets == ()
    finally:
        runtime.stop()


def test_equal_revision_different_checkpoint_fails_closed():
    runtime = _runtime()
    try:
        with runtime._lock:
            runtime._ready = False
            runtime._state = ClientRuntimeState.DISCONNECTED
        assert runtime._ingest_waiting_payload(
            _ticket_payload(checkpoint_id="cp-1", revision=8)
        )
        assert not runtime._ingest_waiting_payload(
            _ticket_payload(checkpoint_id="cp-2", revision=8)
        )
        assert runtime.pending_resume_tickets == ()
    finally:
        runtime.stop()


def test_recovery_waiting_ticket_is_never_auto_resumed():
    runtime = _runtime()
    fake = _FakeRealtime(
        "conn-1",
        lambda e, c, r: _accepted(e, c, r),
    )
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(
            _ticket_payload(
                wait_reason="RECOVERY",
                auto=False,
            )
        )
        time.sleep(0.1)
        assert fake.calls == []
    finally:
        runtime.stop()


def test_principal_drain_invalidates_pending_tickets_and_watermarks():
    runtime = _runtime()
    try:
        with runtime._lock:
            runtime._ready = False
            runtime._state = ClientRuntimeState.DISCONNECTED
        assert runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        assert runtime.pending_resume_tickets
        with runtime._lock:
            runtime._invalidate_resume_state_locked()
        assert runtime.pending_resume_tickets == ()
        assert runtime._execution_resume_watermarks == {}
    finally:
        runtime.stop()
