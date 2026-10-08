"""AE-R14-D: composed critical-P0 remote recovery over real TCP.

This is not a final production-exit certificate. R14-C's separate real
Agent/HITL APPROVE/DENY tests have landed and passed independent
post-merge scoped audit in closed Wave #406 (main@bd521ef5).
This K1/K2 test does NOT compose HITL with Agent resume/reconciliation;
that P0 proof remains explicitly unverified.
The R9-H SQLite Task/ResumeClaim invariant is tracked as a blocking defect
under Issue #409 and is not waived by these independent remote tests.

No production fault hook or shared test fixture is modified here.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from cl.src.core.client_invocation_ledger import (
    ClientInvocationLedger,
    ClientInvocationLedgerState,
)
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.error import (
    CapabilityError,
    REMOTE_OUTCOME_UNKNOWN,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.reconciliation import (
    RemoteReconciliationStatus,
)
from se.tests.e2e import test_r6_e_remote_reconciliation_faults as r6


@pytest.mark.e2e
def test_r14_d_k1_result_loss_k2_wire_reconcile_late_k1_cannot_replay_or_rewrite(
    tmp_path,
):
    """Compose lost result, a new durable client generation, and late K1 send.

    Unlike a purely descriptive matrix, this test starts Uvicorn, connects K1
    and K2 over real sockets, inspects the actual K2 reconcile WebSocket
    frame, and checks the server's terminal revision after the late K1 send.
    This scenario does NOT assert process/server-restart SQL durability:
    the canonical R7-J SQL-backed restart suite owns that distinct proof.
    """

    async def scenario():
        app, catalog, connections = r6._build_gateway_app()
        server, server_task, port = await r6._start_uvicorn(app)
        first = None
        second = None
        ready = threading.Event()
        release_k1 = threading.Event()
        k1_result_send_finished = threading.Event()
        external_effects = []
        server_fallback_calls = []
        wire_reconciles = []
        ledger_path = tmp_path / "r14-d-k1-k2-client-terminal.sqlite3"
        invocation_id = "r14-d-composed-remote"
        first_connection_id = "r14-d-k1"
        second_connection_id = "r14-d-k2"

        class LateFrameObservedRealtime(r6._BlockingResultRealtime):
            def send_result(self, invocation_id, result, **correlation):
                try:
                    return super().send_result(
                        invocation_id, result, **correlation
                    )
                finally:
                    k1_result_send_finished.set()

        def non_idempotent_target(value, **kwargs):
            external_effects.append(value)
            return {"value": value, "effect_count": len(external_effects)}

        def capture_k2(envelope):
            if envelope.get("type") == "capability.reconcile":
                wire_reconciles.append(envelope)

        try:
            registry = r6._client_registry(
                non_idempotent_target,
                idempotency=CapabilityIdempotency.NON_IDEMPOTENT,
            )
            first = await r6._connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(ledger_path),
                connection_id=first_connection_id,
                realtime_cls=LateFrameObservedRealtime,
                realtime_kwargs={
                    "result_ready_event": ready,
                    "allow_send_event": release_k1,
                },
            )
            runtime, store = r6._server_runtime(catalog, connections)
            r6._add_server_implementation(
                runtime,
                catalog,
                lambda context, arguments: (
                    server_fallback_calls.append(context.invocation_id)
                    or {"source": "unsafe-server-fallback"}
                ),
            )
            k1_execution = asyncio.create_task(
                r6._execute(
                    runtime,
                    invocation_id=invocation_id,
                    connection_id=first_connection_id,
                    max_attempts=2,
                ),
                name="r14-d-k1-execution",
            )

            # K1 side effect has already completed and its terminal truth
            # is locally durable, but the result frame has not been sent.
            assert await asyncio.to_thread(ready.wait, 5.0)
            terminal_on_k1 = r6._ledger_record(first.ledger, invocation_id)
            assert terminal_on_k1 is not None
            assert terminal_on_k1.state is ClientInvocationLedgerState.TERMINAL
            assert external_effects == [invocation_id]

            # Sever only the K1 server correlation; the old physical socket
            # remains available to deliver its terminal result *after* K2.
            assert await connections.realtime.disconnect(first_connection_id) == 1
            with pytest.raises(CapabilityError) as initial_error:
                await asyncio.wait_for(k1_execution, timeout=10.0)
            assert initial_error.value.code == REMOTE_OUTCOME_UNKNOWN
            before = await store.get(invocation_id)
            assert before is not None
            assert before.state is CapabilityInvocationState.WAITING
            assert before.remote_outcome_state is RemoteOutcomeState.OUTCOME_UNKNOWN

            # New dispatcher + new ledger handle: the terminal result must
            # be read from actual client SQLite, not a prior in-memory cache.
            second = await r6._connect_client(
                port=port,
                registry=registry,
                ledger=ClientInvocationLedger(ledger_path),
                connection_id=second_connection_id,
                message_observer=capture_k2,
            )
            recovered_terminal = r6._ledger_record(second.ledger, invocation_id)
            assert recovered_terminal is not None
            assert recovered_terminal.state is ClientInvocationLedgerState.TERMINAL
            reconciled = await runtime.reconcile_remote_invocation(
                invocation_id, second_connection_id, timeout=5.0
            )
            assert reconciled.status is RemoteReconciliationStatus.TERMINAL
            assert len(wire_reconciles) == 1
            frame = wire_reconciles[0]
            assert frame["type"] == "capability.reconcile"
            assert frame["invocation_id"] == invocation_id
            assert frame["connection_id"] == second_connection_id

            committed = await store.get(invocation_id)
            assert committed is not None
            assert committed.state is CapabilityInvocationState.COMPLETED
            assert committed.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
            revision = committed.revision
            output = committed.output
            attempts_before = await store.list_attempts(invocation_id)
            assert len(attempts_before) == 1
            assert attempts_before[0].attempt_number == 1
            assert attempts_before[0].connection_id == first_connection_id

            # Release the old terminal result *after* authoritative K2 commit.
            # Observe the send_result completion, not an arbitrary long sleep.
            release_k1.set()
            assert await asyncio.to_thread(k1_result_send_finished.wait, 5.0)
            await asyncio.sleep(0.2)  # Give the real WebSocket read loop a turn.
            after_late = await store.get(invocation_id)
            attempts_after = await store.list_attempts(invocation_id)
            assert after_late is not None
            assert after_late.state is CapabilityInvocationState.COMPLETED
            assert after_late.remote_outcome_state is RemoteOutcomeState.TERMINAL_COMMITTED
            assert after_late.revision == revision
            assert after_late.output == output
            assert [a.attempt_number for a in attempts_after] == [1]
            assert external_effects == [invocation_id]
            assert server_fallback_calls == []
        finally:
            release_k1.set()
            if second is not None:
                await r6._close_generation(second, shutdown_dispatcher=True)
            if first is not None:
                await r6._close_generation(first, shutdown_dispatcher=True)
            await r6._stop_uvicorn(server, server_task)

    asyncio.run(scenario())
