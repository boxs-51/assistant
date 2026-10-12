"""Issue #371 private broker adapter: synthetic contract checks only.

None of these tests spawn a child or assert genuine Linux/Windows OS isolation.
Real broker authority, process-tree cleanup and AE receipt persistence remain
the independent #164/#359/#417 integration/security gates.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tools.v1 import terminal_tool as terminal_module
from tools.v1.terminal_tool import TerminalTool


class _SyntheticPort:
    def __init__(self, completion=None, *, verified=True, error=None):
        self.completion = completion or terminal_module._BrokerRunCompletion(
            outcome="EXITED", tree_ref=object(), receipt=object(),
            exit_code=0, stdout=b"synthetic-ok", stderr=b"",
            stdout_bytes_observed=12, stderr_bytes_observed=0, duration_ms=7,
        )
        self.verified = verified
        self.error = error
        self.calls = []

    async def run_preconfined(self, **kwargs):
        self.calls.append(("run", kwargs))
        if self.error is not None:
            raise self.error
        return self.completion

    async def verify_receipt(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return self.verified


def _call(port, **overrides):
    kwargs = {
        "command": "synthetic-never-executed",
        "timeout": 3,
        "sandbox_relative_cwd": "subdir",
        "encoding": "utf-8",
        "spawn_port": port,
        "authority": object(),
    }
    kwargs.update(overrides)
    return asyncio.run(TerminalTool()._run_broker_owned(**kwargs))


def test_private_success_preserves_bounded_shape_and_never_calls_host_popen():
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["ok"] is True
    data = result["data"]
    assert data["stdout"] == "synthetic-ok"
    assert data["cwd"] == "subdir"
    assert data["stdout_bytes"] == 12
    assert data["exit_code"] == 0
    assert len(port.calls) == 2
    limits = port.calls[0][1]
    assert limits["max_stdout_bytes"] == terminal_module.MAX_STDOUT_BYTES
    assert limits["max_stderr_bytes"] == terminal_module.MAX_STDERR_BYTES
    assert limits["max_total_output_bytes"] == terminal_module.MAX_TOTAL_OUTPUT_BYTES
    assert "receipt" not in repr(result)
    assert "tree_ref" not in repr(result)
    assert "authority" not in repr(result)


@pytest.mark.parametrize(
    "relative_cwd",
    ["/etc", "../escape", "sub/../other", r"C:\Windows", r"sub\directory",
     "//server/share", "sub\x00dir", ""],
)
def test_private_relative_cwd_denies_ambiguous_or_escaping_input(relative_cwd):
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port, sandbox_relative_cwd=relative_cwd)
        popen.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert port.calls == []


def test_missing_private_authority_or_port_denied_before_dispatch():
    port = _SyntheticPort()
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        no_auth = _call(port, authority=None)
        no_port = _call(None)
        popen.assert_not_called()
    assert no_auth["error"]["code"] == "SANDBOX_PRESPAWN_DENIED"
    assert no_port["error"]["code"] == "SANDBOX_PRESPAWN_DENIED"
    assert port.calls == []


def test_stale_receipt_is_unknown_and_never_success():
    port = _SyntheticPort(verified=False)
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["ok"] is False
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["details"]["replay_safe"] is False


def test_broker_disconnection_after_possible_spawn_is_nonreplayable_unknown():
    port = _SyntheticPort(error=ConnectionError("private root /secret/token"))
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = _call(port)
        popen.assert_not_called()
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert "secret" not in repr(result)
    assert len(port.calls) == 1


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [("TIMEOUT", "TERMINAL_TIMEOUT"),
     ("OUTPUT_LIMIT", "TERMINAL_OUTPUT_LIMIT"),
     ("IO_ERROR", "TERMINAL_IO_ERROR")],
)
def test_verified_broker_errors_preserve_existing_tool_error_codes(outcome, expected_code):
    completion = terminal_module._BrokerRunCompletion(
        outcome=outcome, tree_ref=object(), receipt=object(),
    )
    result = _call(_SyntheticPort(completion))
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code


def test_unverified_unknown_outcome_does_not_leak_success_or_retry():
    completion = terminal_module._BrokerRunCompletion(
        outcome="OUTCOME_UNKNOWN", tree_ref=object(), receipt=object(),
    )
    result = _call(_SyntheticPort(completion))
    assert result["error"]["code"] == "TERMINAL_OUTCOME_UNKNOWN"
    assert result["error"]["details"]["replay_safe"] is False


def test_oversized_broker_output_is_rejected_after_receipt_check():
    completion = terminal_module._BrokerRunCompletion(
        outcome="EXITED", tree_ref=object(), receipt=object(), exit_code=0,
        stdout=b"x", stdout_bytes_observed=terminal_module.MAX_STDOUT_BYTES + 1,
    )
    port = _SyntheticPort(completion)
    result = _call(port)
    assert result["error"]["code"] == "TERMINAL_OUTPUT_LIMIT"
    assert [k for k, _ in port.calls] == ["run", "verify"]


def test_cancellation_is_not_mapped_to_success_or_predispatch_deny():
    port = _SyntheticPort(error=asyncio.CancelledError())
    async def invoke():
        await TerminalTool()._run_broker_owned(
            command="synthetic", spawn_port=port, authority=object(),
        )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(invoke())


def test_public_metadata_and_dispatch_have_no_broker_arguments():
    exports = {x["id"]: x for x in terminal_module.TOOL_METADATA["exports"]}
    assert set(exports) == {"terminal.run", "terminal.launch"}
    for item in exports.values():
        assert item["input_schema"]["additionalProperties"] is False
        assert "spawn_port" not in item["input_schema"]["properties"]
        assert "authority" not in item["input_schema"]["properties"]
    # The public method still uses the existing DIRECT behavior, and cannot
    # turn private kwargs into an internal broker call.
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        result = TerminalTool().execute(
            action="run", command="", spawn_port=_SyntheticPort(),
            authority=object(),
        )
        popen.assert_not_called()
    assert result["error"]["code"] == "INVALID_ARGUMENT"
