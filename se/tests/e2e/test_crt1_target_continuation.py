from __future__ import annotations

import pytest

from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    ExistingInvocationContinuationMode,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.runtime import CapabilityRuntime


def _invocation(target):
    fingerprint = capability_request_fingerprint(
        capability_id="desktop.echo",
        capability_version="1.0",
        arguments={"value": "x"},
        target=target,
    )
    return CapabilityInvocation(
        invocation_id="inv-crt1-cont",
        capability_id="desktop.echo",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint=fingerprint,
        target=target,
        owner_user_id="user-1",
        origin_client_id="client-a",
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        execution_id="exec-1",
        tool_call_id="call-1",
        arguments={"value": "x"},
        revision=4,
    )


def test_crt1_target_aware_continuation_recomputes_same_semantics():
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    invocation = _invocation(target)
    CapabilityRuntime()._validate_existing_continuation(
        invocation,
        mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
        expected_revision=4,
        expected_request_fingerprint=invocation.request_fingerprint,
    )

    mutated = invocation.model_copy(
        update={
            "target": target.model_copy(
                update={"stable_client_id": "client-b"}
            )
        }
    )
    with pytest.raises(CapabilityError):
        CapabilityRuntime()._validate_existing_continuation(
            mutated,
            mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
            expected_revision=4,
            expected_request_fingerprint=invocation.request_fingerprint,
        )


def test_crt1_legacy_null_target_continuation_keeps_legacy_identity():
    invocation = _invocation(None)
    CapabilityRuntime()._validate_existing_continuation(
        invocation,
        mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
        expected_revision=4,
        expected_request_fingerprint=invocation.request_fingerprint,
    )
