from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from cl.src.core.capability_dispatcher import CapabilityDispatcher


class _Realtime:
    def __init__(self):
        self.connection_id = "conn-1"
        self.events = []

    def send_error(self, invocation_id, **payload):
        self.events.append((invocation_id, payload))

    def send_result(self, invocation_id, result, **correlation):
        self.events.append((invocation_id, {"output": result}))

    def send_cancelled(self, invocation_id, **correlation):
        self.events.append((invocation_id, {"cancelled": True}))


def _legacy_expected(arguments):
    payload = {
        "capability_id": "crt.echo",
        "capability_version": "1.0",
        "arguments": arguments,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _target():
    return {
        "resource_scope": "CLIENT_LOCAL",
        "resource_ref": "client://client-a/report.txt",
        "stable_client_id": "client-a",
        "fallback_policy": "NONE",
    }


def test_client_legacy_fingerprint_is_byte_compatible_and_target_changes_identity():
    arguments = {"value": "x"}
    legacy = CapabilityDispatcher._request_fingerprint(
        "crt.echo", "1.0", arguments
    )
    targeted = CapabilityDispatcher._request_fingerprint(
        "crt.echo", "1.0", arguments, _target()
    )
    assert legacy == _legacy_expected(arguments)
    assert targeted != legacy


def test_client_target_validator_rejects_noncanonical_or_incomplete_shape():
    with pytest.raises(ValueError):
        CapabilityDispatcher._canonical_target_payload(
            {"resource_scope": "CLIENT_LOCAL"}
        )
    bad = _target()
    bad["resource_ref"] = " client://client-a/report.txt"
    with pytest.raises(ValueError):
        CapabilityDispatcher._canonical_target_payload(bad)
    missing_client = _target()
    missing_client["stable_client_id"] = None
    with pytest.raises(ValueError):
        CapabilityDispatcher._canonical_target_payload(missing_client)


def test_malformed_wire_target_fails_closed_before_local_execution():
    called = []
    registry = SimpleNamespace(
        tools={
            "crt.echo": {
                "func": lambda **kwargs: called.append(kwargs),
                "metadata": {
                    "name": "crt.echo",
                    "version": "1.0",
                    "base_risk": "LOW",
                },
            }
        }
    )
    realtime = _Realtime()
    dispatcher = CapabilityDispatcher(registry, realtime)
    dispatcher.update_registration_snapshot(["crt.echo"])
    try:
        dispatcher.dispatch(
            {
                "type": "capability.invoke",
                "connection_id": "conn-1",
                "invocation_id": "inv-crt",
                "payload": {
                    "capability_id": "crt.echo",
                    "capability_version": "1.0",
                    "arguments": {"value": "x"},
                    "target": {
                        "resource_scope": "CLIENT_LOCAL",
                        "resource_ref": None,
                        "stable_client_id": None,
                        "fallback_policy": "NONE",
                    },
                    "request_fingerprint": "0" * 64,
                },
            }
        )
        assert called == []
        assert realtime.events
        assert realtime.events[0][1]["code"] == "REMOTE_INVOCATION_CONFLICT"
    finally:
        dispatcher.shutdown()

def test_client_local_target_for_different_installation_fails_closed():
    called = []
    registry = SimpleNamespace(
        tools={
            "crt.echo": {
                "func": lambda **kwargs: called.append(kwargs),
                "metadata": {
                    "name": "crt.echo",
                    "version": "1.0",
                    "base_risk": "LOW",
                },
            }
        }
    )
    realtime = _Realtime()
    dispatcher = CapabilityDispatcher(
        registry,
        realtime,
        client_id="client-a",
    )
    dispatcher.update_registration_snapshot(["crt.echo"])
    target = _target()
    target["stable_client_id"] = "client-b"
    try:
        dispatcher.dispatch(
            {
                "type": "capability.invoke",
                "connection_id": "conn-1",
                "invocation_id": "inv-foreign-client",
                "payload": {
                    "capability_id": "crt.echo",
                    "capability_version": "1.0",
                    "arguments": {"value": "x"},
                    "target": target,
                    "request_fingerprint": (
                        CapabilityDispatcher._request_fingerprint(
                            "crt.echo",
                            "1.0",
                            {"value": "x"},
                            target,
                        )
                    ),
                },
            }
        )
        assert called == []
        assert realtime.events
        assert realtime.events[0][1]["code"] == "REMOTE_INVOCATION_CONFLICT"
    finally:
        dispatcher.shutdown()

