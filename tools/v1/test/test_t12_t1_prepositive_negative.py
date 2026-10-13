"""T1 pre-positive negative regression gates for Tools #371.

Only in-process adversarial test doubles are exercised. These tests DO NOT
authenticate an OS broker, start native commands, or permit native success.
They extend the existing B0 negative suite without modifying the adapter.
The S1-A/B/C real Linux/Windows gates belong to #164/#417 and are NOT
satisfied by this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest

from tools.v1 import terminal_tool as terminal_module
from tools.v1.terminal_tool import TerminalTool


def _completion(**changes):
    baseline = terminal_module._BrokerRunCompletion(
        outcome="EXITED",
        tree_ref=object(),
        receipt=object(),
        exit_code=0,
        stdout=b"x",
        stderr=b"",
        stdout_bytes_observed=1,
        stderr_bytes_observed=0,
        duration_ms=1,
    )
    return replace(baseline, **changes)


class _AdversarialPort:
    def __init__(self, *, completion=None, run_error=None, verification=False,
                 verify_error=None):
        self.completion = _completion() if completion is None else completion
        self.run_error = run_error
        self.verification = verification
        self.verify_error = verify_error
        self.dispatches = 0
        self.verifications = 0

    async def run_preconfined(self, **kwargs):
        self.dispatches += 1
        if self.run_error is not None:
            raise self.run_error
        return self.completion

    async def verify_receipt(self, **kwargs):
        self.verifications += 1
        if self.verify_error is not None:
            raise self.verify_error
        return self.verification


def _invoke(port, **overrides):
    args = {
        "command": "test-owned-synthetic-command-never-executed",
        "timeout": 3,
        "sandbox_relative_cwd": "workspace",
        "encoding": "utf-8",
        "spawn_port": port,
        "authority": object(),
    }
    args.update(overrides)
    with patch.object(terminal_module.subprocess, "Popen") as host:
        result = asyncio.run(TerminalTool()._run_broker_owned(**args))
        host.assert_not_called()
    return result


def _assert_nonreplayable_unknown(result):
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["retryable"] is False
    assert result["error"]["details"]["replay_safe"] is False
    assert "receipt" not in repr(result)
    assert "tree_ref" not in repr(result)


@pytest.mark.parametrize("error", [
    ConnectionResetError("disconnected after possible dispatch"),
    BrokenPipeError("transport died after reservation"),
    TimeoutError("receipt lookup deadline elapsed"),
    PermissionError("foreign service identity"),
    OSError("service restart changed broker epoch"),
])
def test_t1_transport_failure_never_falls_back_or_retries(error):
    port = _AdversarialPort(run_error=error)
    result = _invoke(port)
    _assert_nonreplayable_unknown(result)
    assert port.dispatches == 1
    assert port.verifications == 0


@pytest.mark.parametrize("forged", [
    {"outcome": "EXITED", "receipt": True, "tree_ref": "fake"},
    True,
    1,
    "EXITED",
    ("EXITED", True),
    object(),
])
def test_t1_forged_completion_shape_cannot_trigger_receipt_verification(forged):
    port = _AdversarialPort(completion=forged)
    result = _invoke(port)
    _assert_nonreplayable_unknown(result)
    assert port.dispatches == 1
    assert port.verifications == 0


class _ExplosiveTruthyVerification:
    def __bool__(self):
        raise AssertionError("untrusted verification must not be bool-coerced")


@pytest.mark.parametrize("untrusted_verification", [
    None,
    False,
    1,
    "VERIFIED_EMPTY",
    {"verified_empty": True},
    _ExplosiveTruthyVerification(),
])
def test_t1_untrusted_verification_cannot_certify_native_completion(
    untrusted_verification,
):
    port = _AdversarialPort(verification=untrusted_verification)
    result = _invoke(port)
    _assert_nonreplayable_unknown(result)
    assert port.dispatches == 1
    assert port.verifications == 1


@pytest.mark.parametrize("changes", [
    {"exit_code": True},
    {"exit_code": None},
    {"stdout": "forged-string-not-bytes"},
    {"stderr": bytearray(b"not-immutable-bytes")},
    {"stdout_bytes_observed": True},
    {"stdout_bytes_observed": -1},
    {"stdout_bytes_observed": 0},
    {"stderr_bytes_observed": False},
    {"duration_ms": True},
    {"duration_ms": -1},
])
def test_t1_malformed_completion_cannot_publish_success(changes):
    # A mocked True means only that the adapter's shape check is exercised,
    # NOT that an authenticated native broker ever issued a real receipt.
    port = _AdversarialPort(completion=_completion(**changes), verification=True)
    result = _invoke(port)
    _assert_nonreplayable_unknown(result)
    assert port.dispatches == 1
    assert port.verifications == 1


@pytest.mark.parametrize("outcome", [
    "OUTCOME_UNKNOWN",
    "REVOKED",
    "DENY",
    "COMMITTED",
    None,
    True,
])
def test_t1_receipt_shaped_reply_with_nonterminal_outcome_denied(outcome):
    port = _AdversarialPort(completion=_completion(outcome=outcome), verification=True)
    _assert_nonreplayable_unknown(_invoke(port))
    assert port.dispatches == 1
    assert port.verifications == 1


def test_t1_disconnect_during_receipt_lookup_redacts_secret_and_does_not_retry():
    confidential = "/broker/private/credential/should-not-leak"
    port = _AdversarialPort(
        verify_error=ConnectionResetError("receipt lookup failed " + confidential),
    )
    result = _invoke(port)
    _assert_nonreplayable_unknown(result)
    assert confidential not in repr(result)
    assert port.dispatches == 1
    assert port.verifications == 1


def test_t1_missing_port_or_authority_denies_before_dispatch():
    port = _AdversarialPort()
    for override in ({"spawn_port": None}, {"authority": None}):
        args = {
            "command": "test-owned-synthetic-command-never-executed",
            "spawn_port": port,
            "authority": object(),
        }
        args.update(override)
        with patch.object(terminal_module.subprocess, "Popen") as host:
            result = asyncio.run(TerminalTool()._run_broker_owned(**args))
            host.assert_not_called()
        assert result["ok"] is False
        assert result["error"]["code"] == "SANDBOX_PRESPAWN_DENIED"
    assert port.dispatches == 0
