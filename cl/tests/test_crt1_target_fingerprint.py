from __future__ import annotations

import pytest

from cl.src.core.capability_dispatcher import CapabilityDispatcher
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint


def _wire_target():
    return {
        "resource_scope": "CLIENT_LOCAL",
        "resource_ref": "desktop:primary",
        "stable_client_id": "client-a",
        "fallback_policy": "NONE",
    }


def test_crt1_client_and_server_target_fingerprints_are_identical():
    wire = _wire_target()
    server = capability_request_fingerprint(
        capability_id="desktop.echo",
        capability_version="1.0",
        arguments={"value": "x"},
        target=CapabilityInvocationTarget(
            resource_scope=ResourceScope.CLIENT_LOCAL,
            resource_ref="desktop:primary",
            stable_client_id="client-a",
            fallback_policy=FallbackPolicy.NONE,
        ),
    )
    client = CapabilityDispatcher._request_fingerprint(
        "desktop.echo",
        "1.0",
        {"value": "x"},
        target=wire,
    )
    assert client == server

    # Physical routing generations never enter semantic target identity.
    assert CapabilityDispatcher._request_fingerprint(
        "desktop.echo",
        "1.0",
        {"value": "x"},
        target=wire,
    ) == client


@pytest.mark.parametrize(
    "target",
    [
        {"resource_scope": "CLIENT_LOCAL"},
        {
            "resource_scope": "CLIENT_LOCAL",
            "resource_ref": None,
            "stable_client_id": " client-a",
            "fallback_policy": "NONE",
        },
        {
            "resource_scope": "UNKNOWN",
            "resource_ref": None,
            "stable_client_id": None,
            "fallback_policy": "NONE",
        },
    ],
)
def test_crt1_client_rejects_malformed_or_noncanonical_target(target):
    with pytest.raises((TypeError, ValueError)):
        CapabilityDispatcher._request_fingerprint(
            "desktop.echo",
            "1.0",
            {"value": "x"},
            target=target,
        )
