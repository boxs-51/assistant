from __future__ import annotations

import asyncio

import pytest

from se.src.application.user_tool_quota import ToolQuotaAdmission
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.drivers.remote_client_driver import (
    RemoteClientDriver,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.multiplexer import RemoteConnectionLost
from se.src.runtimes.connection.registry import ConnectionStateError


class _QuotaProbe:
    enabled = True

    def __init__(self) -> None:
        self.reserve_calls = []
        self.settle_calls = []
        self.release_calls = []

    async def reserve_tool_call(self, **kwargs):
        self.reserve_calls.append(dict(kwargs))
        invocation_id = str(kwargs["invocation_id"])
        return ToolQuotaAdmission(
            owner_user_id=str(kwargs["identity"].user_id),
            invocation_id=invocation_id,
            capability_id=str(kwargs["capability_id"]),
            request_fingerprint=str(kwargs["request_fingerprint"]),
            idempotency_key=f"quota:{invocation_id}",
            reservation_id=f"reservation:{invocation_id}",
            window_epoch=1,
            reservation_state="RESERVED",
        )

    async def find_tool_call_authority(self, **kwargs):
        return None

    async def settle_tool_call(self, admission):
        self.settle_calls.append(admission)
        return admission

    async def release_tool_call(self, admission):
        self.release_calls.append(admission)
        return admission


class _Realtime:
    def __init__(self, error_factory) -> None:
        self._error_factory = error_factory
        self.calls = 0
        self.cancel_calls = 0

    async def invoke(self, envelope, timeout=None):
        self.calls += 1
        error = self._error_factory(envelope)
        raise error

    async def cancel(self, connection_id, invocation_id):
        self.cancel_calls += 1


def _runtime(error_factory, *, idempotency=CapabilityIdempotency.UNKNOWN):
    capability_id = "tool.ubq3.remote"
    definition = CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="UBQ-3 outcome evidence",
        kind=CapabilityKind.TOOL,
        idempotency=idempotency,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )
    realtime = _Realtime(error_factory)
    driver = RemoteClientDriver(
        definition,
        realtime,
        "conn-ubq3-remote",
    )
    quota = _QuotaProbe()
    runtime = CapabilityRuntime(tool_quota_service=quota)
    runtime.register_capability(driver)
    return runtime, realtime, quota


def _identity() -> Identity:
    return Identity(user_id="user-ubq3", auth_type="jwt", scopes={"*"})


@pytest.mark.asyncio
async def test_ubq3_not_dispatched_waiting_keeps_reservation_reserved() -> None:
    runtime, realtime, quota = _runtime(
        lambda envelope: ConnectionStateError(
            "connection inactive before send boundary"
        )
    )

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            "tool.ubq3.remote",
            {"value": "x"},
            _identity(),
            invocation_id="inv-ubq3-not-dispatched",
            connection_id="conn-ubq3-remote",
        )

    assert caught.value.code == "REMOTE_CONNECTION_LOST"
    persisted = await runtime.invocation_lifecycle.store.get(
        "inv-ubq3-not-dispatched"
    )
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.WAITING
    assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
    assert (
        persisted.remote_outcome_state
        is RemoteOutcomeState.NOT_DISPATCHED
    )
    assert realtime.calls == 1
    assert len(quota.reserve_calls) == 1
    assert quota.settle_calls == []
    assert quota.release_calls == []


@pytest.mark.asyncio
async def test_ubq3_remote_outcome_unknown_does_not_refund() -> None:
    runtime, realtime, quota = _runtime(
        lambda envelope: RemoteConnectionLost(
            envelope.connection_id,
            envelope.invocation_id,
        ),
        idempotency=CapabilityIdempotency.UNKNOWN,
    )

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            "tool.ubq3.remote",
            {"value": "x"},
            _identity(),
            invocation_id="inv-ubq3-unknown",
            connection_id="conn-ubq3-remote",
        )

    assert caught.value.code == "REMOTE_OUTCOME_UNKNOWN"
    persisted = await runtime.invocation_lifecycle.store.get(
        "inv-ubq3-unknown"
    )
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.WAITING
    assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
    assert (
        persisted.remote_outcome_state
        is RemoteOutcomeState.OUTCOME_UNKNOWN
    )
    assert realtime.calls == 1
    assert len(quota.reserve_calls) == 1
    assert quota.settle_calls == []
    assert quota.release_calls == []


@pytest.mark.asyncio
async def test_ubq3_remote_timeout_unknown_does_not_refund() -> None:
    runtime, realtime, quota = _runtime(
        lambda envelope: asyncio.TimeoutError()
    )

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            "tool.ubq3.remote",
            {"value": "x"},
            _identity(),
            invocation_id="inv-ubq3-timeout",
            connection_id="conn-ubq3-remote",
        )

    assert caught.value.code == "CAPABILITY_TIMEOUT"
    persisted = await runtime.invocation_lifecycle.store.get(
        "inv-ubq3-timeout"
    )
    assert persisted is not None
    assert persisted.state is CapabilityInvocationState.TIMED_OUT
    assert (
        persisted.remote_outcome_state
        is RemoteOutcomeState.OUTCOME_UNKNOWN
    )
    assert realtime.calls == 1
    assert len(quota.reserve_calls) == 1
    assert quota.settle_calls == []
    assert quota.release_calls == []
