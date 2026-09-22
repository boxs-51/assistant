import time
from datetime import datetime, timedelta, timezone
from collections import deque
from types import SimpleNamespace

import pytest

from cl.src.core.client_runtime import ClientRuntime, ClientRuntimeState
from cl.src.core.realtime_client import GatewayRealtimeClient, RealtimeHandshakeError
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


def _failed(execution_id, checkpoint_id, request_id, revision=10):
    return {
        "type": "execution.resume.failed",
        "connection_id": "conn-any",
        "execution_id": execution_id,
        "payload": {
            "execution_id": execution_id,
            "checkpoint_id": checkpoint_id,
            "resume_request_id": request_id,
            "claim_id": "claim-1",
            "code": "RUNTIME_HANDOFF_FAILED",
            "state": "WAITING",
            "wait_reason": "RECOVERY",
            "recovery_checkpoint_id": "cp-recovery",
            "recovery_revision": revision,
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

        # The conflict is execution-level, not only entry-level. A repeated
        # delivery of C2@8 must remain tombstoned and cannot become eligible.
        assert not runtime._ingest_waiting_payload(
            _ticket_payload(checkpoint_id="cp-2", revision=8)
        )
        assert runtime.pending_resume_tickets == ()
        assert runtime._execution_resume_conflicts["exec-1"] == 8

        # A strictly newer authoritative revision resolves the old conflict.
        assert runtime._ingest_waiting_payload(
            _ticket_payload(checkpoint_id="cp-3", revision=9)
        )
        assert runtime._execution_resume_conflicts.get("exec-1") is None
        assert [item.key for item in runtime.pending_resume_tickets] == [
            ("exec-1", "cp-3")
        ]
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


def test_realtime_resume_sends_canonical_request_and_matches_rejected_outcome():
    client = GatewayRealtimeClient(
        "http://gateway",
        {"Authorization": "Bearer token"},
        connection_id="conn-1",
        client_id="client-1",
    )
    client._capabilities_registered.set()
    sent = []

    def fake_send(event_type, payload, invocation_id=None, **correlation):
        sent.append((event_type, payload, invocation_id, correlation))

    response = _rejected(
        "exec-1",
        "cp-1",
        "rr-1",
        "RESUME_CONFLICT",
        retryable=True,
    )
    response["connection_id"] = "conn-1"

    def fake_wait(predicate, timeout):
        assert predicate(response)
        return response

    client.send = fake_send
    client._wait_for_message = fake_wait

    result = client.resume_execution("exec-1", "cp-1", "rr-1", timeout=0.01)
    assert result is response
    assert sent == [
        (
            "execution.resume",
            {
                "execution_id": "exec-1",
                "checkpoint_id": "cp-1",
                "resume_request_id": "rr-1",
            },
            None,
            {"execution_id": "exec-1"},
        )
    ]


def test_http_and_sse_waiting_payloads_are_ingested_without_consuming_output():
    runtime = _runtime()
    waiting = _ticket_payload()
    try:
        runtime.gateway.send_request = lambda payload: dict(waiting)
        result = runtime.chat({"config": {"stream": False}})
        assert result == waiting
        assert [item.key for item in runtime.pending_resume_tickets] == [
            ("exec-1", "cp-1")
        ]

        with runtime._lock:
            runtime._invalidate_resume_state_locked()

        def stream():
            yield dict(waiting)
            yield {"status": "other"}

        runtime.gateway.send_request = lambda payload: stream()
        output = list(runtime.chat({"config": {"stream": True}}))
        assert output == [waiting, {"status": "other"}]
        assert [item.key for item in runtime.pending_resume_tickets] == [
            ("exec-1", "cp-1")
        ]
    finally:
        runtime.stop()


def test_retryable_rejection_waits_for_fresh_ticket_before_new_request():
    runtime = _runtime()
    requests = []

    def behavior(execution_id, checkpoint_id, request_id):
        requests.append(request_id)
        if len(requests) == 1:
            return _rejected(
                execution_id,
                checkpoint_id,
                request_id,
                "RESUME_CONFLICT",
                retryable=True,
            )
        return _accepted(execution_id, checkpoint_id, request_id)

    fake = _FakeRealtime("conn-1", behavior)
    _install_ready_transport(runtime, fake)

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(requests) == 1)
        time.sleep(0.1)
        assert len(requests) == 1

        # Duplicate canonical publication is a fresh eligibility signal.
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(requests) == 2)
        assert requests[0] != requests[1]
        _wait(lambda: runtime.pending_resume_tickets == ())
    finally:
        runtime.stop()


def test_failed_resume_advances_recovery_watermark_and_never_retries_old_checkpoint():
    runtime = _runtime()
    fake = _FakeRealtime(
        "conn-1",
        lambda e, c, r: _failed(e, c, r, revision=10),
    )
    _install_ready_transport(runtime, fake)

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        _wait(lambda: len(fake.calls) == 1)
        _wait(lambda: runtime.pending_resume_tickets == ())
        assert runtime._execution_resume_watermarks["exec-1"] == 10

        time.sleep(0.1)
        assert len(fake.calls) == 1
        assert not runtime._ingest_waiting_payload(
            _ticket_payload(checkpoint_id="cp-1", revision=8)
        )

        recovery = _ticket_payload(
            checkpoint_id="cp-recovery",
            revision=10,
            wait_reason="RECOVERY",
            auto=False,
        )
        assert runtime._ingest_waiting_payload(recovery)
        time.sleep(0.1)
        assert len(fake.calls) == 1
    finally:
        runtime.stop()


def test_lost_ack_same_request_remains_blocked_until_new_generation_capability_ack():
    runtime = _runtime()
    runtime._suppress_reconnect = True

    def lost_ack(*_):
        raise RealtimeHandshakeError("lost ACK")

    first = _FakeRealtime("conn-1", lost_ack)
    _install_ready_transport(runtime, first, generation=1)

    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(first.calls) >= 1)
        request_id = first.calls[0][2]

        with runtime._lock:
            runtime._ready = False
            runtime._state = ClientRuntimeState.DISCONNECTED
            runtime._confirmed_capability_ids = frozenset()

        second = _FakeRealtime(
            "conn-2",
            lambda e, c, r: _accepted(e, c, r),
        )
        _install_ready_transport(runtime, second, generation=2)
        with runtime._lock:
            runtime._confirmed_capability_ids = frozenset()
            runtime._resume_condition.notify_all()

        time.sleep(0.15)
        assert second.calls == []

        with runtime._lock:
            runtime._confirmed_capability_ids = frozenset({"tool.echo"})
            runtime._resume_condition.notify_all()

        _wait(lambda: len(second.calls) == 1)
        assert second.calls[0][2] == request_id
    finally:
        runtime.stop()


def test_consumed_claim_resume_conflict_preserves_same_request_id_until_settled():
    runtime = _runtime()
    calls = []

    def behavior(execution_id, checkpoint_id, request_id):
        calls.append(request_id)
        if len(calls) == 1:
            message = _rejected(
                execution_id,
                checkpoint_id,
                request_id,
                "RESUME_CONFLICT",
                retryable=True,
            )
            message["payload"]["claim_id"] = "claim-consumed"
            return message
        return _accepted(execution_id, checkpoint_id, request_id)

    fake = _FakeRealtime("conn-1", behavior)
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(calls) >= 2)
        assert calls[0] == calls[1]
        _wait(lambda: runtime.pending_resume_tickets == ())
    finally:
        runtime.stop()


def test_failed_without_recovery_revision_tombstones_old_checkpoint_by_authority_floor():
    runtime = _runtime()

    def fail_without_recovery(execution_id, checkpoint_id, request_id):
        message = _failed(execution_id, checkpoint_id, request_id, revision=10)
        message["payload"].pop("recovery_checkpoint_id", None)
        message["payload"].pop("recovery_revision", None)
        message["payload"]["state"] = "FAILED"
        message["payload"].pop("wait_reason", None)
        return message

    fake = _FakeRealtime("conn-1", fail_without_recovery)
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        _wait(lambda: runtime.pending_resume_tickets == ())
        assert runtime._execution_resume_watermarks["exec-1"] == 9
        assert not runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        time.sleep(0.05)
        assert len(fake.calls) == 1
    finally:
        runtime.stop()


def test_wait_ttl_does_not_abort_same_request_ack_recovery():
    runtime = _runtime()
    expired_payload = _ticket_payload()
    expired_payload["wait_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    ticket = PendingResumeTicket.from_payload(expired_payload)
    entry = PendingResumeEntry(
        ticket=ticket,
        principal_id="user-1",
        state=ResumeTicketState.RETRY_SAME_REQUEST,
        active_resume_request_id="rr-issued-before-expiry",
    )

    try:
        with runtime._lock:
            runtime._execution_resume_watermarks["exec-1"] = 8
            runtime._confirmed_capability_ids = frozenset({"tool.echo"})
            runtime._ready = True
            runtime._state = ClientRuntimeState.READY
            runtime._classify_resume_entry_locked(entry)

        assert entry.state is ResumeTicketState.RETRY_SAME_REQUEST
        assert entry.active_resume_request_id == "rr-issued-before-expiry"
    finally:
        runtime.stop()


def test_duplicate_waiting_event_cannot_reopen_in_flight_ticket():
    runtime = _runtime()
    ticket = PendingResumeTicket.from_payload(_ticket_payload())
    entry = PendingResumeEntry(
        ticket=ticket,
        principal_id="user-1",
        state=ResumeTicketState.IN_FLIGHT,
        active_resume_request_id="rr-in-flight",
    )

    try:
        with runtime._lock:
            runtime._pending_resume_tickets[ticket.key] = entry
            runtime._execution_resume_watermarks[ticket.execution_id] = ticket.revision
            runtime._confirmed_capability_ids = frozenset({"tool.echo"})
            runtime._ready = True
            runtime._state = ClientRuntimeState.READY

        assert runtime._ingest_waiting_payload(_ticket_payload())
        with runtime._lock:
            current = runtime._pending_resume_tickets[ticket.key]
            assert current.state is ResumeTicketState.IN_FLIGHT
            assert current.active_resume_request_id == "rr-in-flight"
            assert runtime._claim_resume_attempt_locked(ticket.key) is None
    finally:
        runtime.stop()


def test_malformed_resume_outcome_fails_closed_without_replay_loop():
    runtime = _runtime()

    def malformed(execution_id, checkpoint_id, request_id):
        return {
            "type": "execution.resume.accepted",
            "connection_id": "conn-1",
            "execution_id": execution_id,
            "payload": {
                "execution_id": execution_id,
                "checkpoint_id": checkpoint_id,
                # Missing resume_request_id is a semantic protocol violation.
                "accepted_revision": 9,
            },
        }

    fake = _FakeRealtime("conn-1", malformed)
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(fake.calls) == 1)
        time.sleep(0.1)
        assert len(fake.calls) == 1
        with runtime._lock:
            entry = runtime._pending_resume_tickets[("exec-1", "cp-1")]
            assert entry.state is ResumeTicketState.CONFLICT
            assert entry.last_outcome_code == "RESUME_PROTOCOL_CONFLICT"
    finally:
        runtime.stop()


def test_nonretryable_rejection_tombstones_duplicate_waiting_ticket():
    runtime = _runtime()
    calls = []

    def reject(execution_id, checkpoint_id, request_id):
        calls.append(request_id)
        return _rejected(
            execution_id,
            checkpoint_id,
            request_id,
            "STALE_CHECKPOINT",
            retryable=False,
        )

    fake = _FakeRealtime("conn-1", reject)
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(_ticket_payload())
        _wait(lambda: len(calls) == 1)
        time.sleep(0.05)

        # A duplicate publication for the exact same durable checkpoint must
        # not turn a terminal rejection into a new logical resume attempt.
        assert runtime._ingest_waiting_payload(_ticket_payload())
        time.sleep(0.1)
        assert len(calls) == 1
        with runtime._lock:
            entry = runtime._pending_resume_tickets[("exec-1", "cp-1")]
            assert entry.state is ResumeTicketState.REJECTED
            assert entry.active_resume_request_id is None
    finally:
        runtime.stop()


def test_terminal_authority_ack_never_regresses_execution_watermark():
    runtime = _runtime()
    responses = deque(
        [
            _accepted("exec-1", "cp-1", "placeholder", revision=8),
            _failed("exec-2", "cp-2", "placeholder", revision=7),
        ]
    )

    def behavior(execution_id, checkpoint_id, request_id):
        message = responses.popleft()
        message["execution_id"] = execution_id
        message["payload"]["execution_id"] = execution_id
        message["payload"]["checkpoint_id"] = checkpoint_id
        message["payload"]["resume_request_id"] = request_id
        return message

    fake = _FakeRealtime("conn-1", behavior)
    _install_ready_transport(runtime, fake)
    try:
        assert runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        _wait(lambda: runtime._execution_resume_watermarks.get("exec-1") == 9)

        assert runtime._ingest_waiting_payload(
            _ticket_payload(
                execution_id="exec-2",
                checkpoint_id="cp-2",
                revision=8,
            )
        )
        _wait(lambda: runtime._execution_resume_watermarks.get("exec-2") == 9)

        assert not runtime._ingest_waiting_payload(_ticket_payload(revision=8))
        assert not runtime._ingest_waiting_payload(
            _ticket_payload(
                execution_id="exec-2",
                checkpoint_id="cp-2",
                revision=8,
            )
        )
    finally:
        runtime.stop()
