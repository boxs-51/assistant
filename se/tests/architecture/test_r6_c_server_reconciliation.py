from __future__ import annotations

import asyncio

import pytest

from se.src.runtimes.capability.contracts.error import (
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.reconciliation import (
    RemoteReconciliationStatus,
)
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.reconciliation import (
    RemoteInvocationReconciliationService,
)
from se.src.runtimes.connection.registry import ConnectionRegistry


class _Realtime:
    def __init__(self, payload):
        self.payload = payload
        self.envelopes = []

    async def reconcile(self, envelope, timeout=None):
        self.envelopes.append(envelope)
        return dict(self.payload)


def _invocation():
    return CapabilityInvocation(
        invocation_id="inv-1",
        capability_id="tool.echo",
        capability_version="2.0",
        kind="TOOL",
        execution_mode="ONE_SHOT",
        request_fingerprint="f" * 64,
        owner_user_id="user-1",
        origin_client_id="client-1",
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
    )


def _registry(*, user_id="user-1", client_id="client-1"):
    registry = ConnectionRegistry()
    registry.register(
        "session-2",
        user_id,
        object(),
        metadata={"client_id": client_id},
        connection_id="conn-2",
    )
    registry.activate("conn-2")
    return registry


def _terminal_payload():
    return {
        "status": "TERMINAL",
        "capability_id": "tool.echo",
        "capability_version": "2.0",
        "request_fingerprint": "f" * 64,
        "terminal_type": "result",
        "terminal_payload": {"output": {"ok": True}},
    }


def test_terminal_reconciliation_commits_waiting_invocation():
    async def scenario():
        store = InMemoryCapabilityInvocationStore()
        lifecycle = CapabilityInvocationLifecycle(store)
        await lifecycle.create(_invocation())
        service = RemoteInvocationReconciliationService(
            lifecycle,
            _registry(),
            _Realtime(_terminal_payload()),
        )

        result = await service.reconcile("inv-1", "conn-2")
        assert result.status is RemoteReconciliationStatus.TERMINAL
        persisted = store.items["inv-1"]
        assert persisted.state is CapabilityInvocationState.COMPLETED
        assert persisted.output == {"ok": True}
        assert (
            persisted.remote_outcome_state
            is RemoteOutcomeState.TERMINAL_COMMITTED
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("user_id", "client_id"),
    [
        ("user-2", "client-1"),
        ("user-1", "client-2"),
    ],
)
def test_reconciliation_requires_same_owner_and_client_installation(
    user_id,
    client_id,
):
    async def scenario():
        store = InMemoryCapabilityInvocationStore()
        lifecycle = CapabilityInvocationLifecycle(store)
        await lifecycle.create(_invocation())
        service = RemoteInvocationReconciliationService(
            lifecycle,
            _registry(user_id=user_id, client_id=client_id),
            _Realtime(_terminal_payload()),
        )
        with pytest.raises(CapabilityError) as raised:
            await service.reconcile("inv-1", "conn-2")
        assert raised.value.code == "CAPABILITY_UNAUTHORIZED"

    asyncio.run(scenario())


def test_running_reconciliation_does_not_change_waiting_invocation():
    async def scenario():
        payload = {
            "status": "RUNNING",
            "capability_id": "tool.echo",
            "capability_version": "2.0",
            "request_fingerprint": "f" * 64,
        }
        store = InMemoryCapabilityInvocationStore()
        lifecycle = CapabilityInvocationLifecycle(store)
        await lifecycle.create(_invocation())
        service = RemoteInvocationReconciliationService(
            lifecycle,
            _registry(),
            _Realtime(payload),
        )
        result = await service.reconcile("inv-1", "conn-2")
        assert result.status is RemoteReconciliationStatus.RUNNING
        assert store.items["inv-1"].state is CapabilityInvocationState.WAITING

    asyncio.run(scenario())


def test_conflicting_reconciliation_fails_closed():
    async def scenario():
        payload = {
            "status": "CONFLICT",
            "capability_id": "tool.echo",
            "capability_version": "2.0",
            "request_fingerprint": "f" * 64,
        }
        store = InMemoryCapabilityInvocationStore()
        lifecycle = CapabilityInvocationLifecycle(store)
        await lifecycle.create(_invocation())
        service = RemoteInvocationReconciliationService(
            lifecycle,
            _registry(),
            _Realtime(payload),
        )
        with pytest.raises(CapabilityError) as raised:
            await service.reconcile("inv-1", "conn-2")
        assert raised.value.code == REMOTE_INVOCATION_CONFLICT
        assert store.items["inv-1"].state is CapabilityInvocationState.WAITING

    asyncio.run(scenario())
