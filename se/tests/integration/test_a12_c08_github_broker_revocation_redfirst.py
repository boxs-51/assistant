"""A12-C08 RED-first CL consumer witness; external GitHub host is UNKNOWN.

No real network, branch write, GitHub API or connector credential is exercised.
Cross-process revoke/send and ambiguous receipt MUST run at the host after
host-owner/path authorization. Never classify these skips as physical PASS.
"""
import json
from types import SimpleNamespace

import pytest

from cl.src.core.tool_executor import ExecutionContext, ToolExecutor


class _ApprovedRoutineHitl:
    def request_approval(self, *_args, **_kwargs):
        return True  # Regular HITL is not an Owner-signed A12 scope receipt.


class _FakeRegistry:
    def __init__(self, callback):
        self.callback = callback

    def get_tool(self, _tool_name):
        return {"metadata": {"base_risk": "LOW"}, "func": self.callback}


def _attempt(name, payload):
    calls = []

    def isolated_fake_github_tool(**kwargs):
        calls.append(dict(kwargs))
        return {"simulated": True, "network_requests": 0}

    executor = ToolExecutor(_FakeRegistry(isolated_fake_github_tool), _ApprovedRoutineHitl())
    call = SimpleNamespace(id="a12-isolated-call", function=SimpleNamespace(
        name=name, arguments=json.dumps(payload)
    ))
    context = ExecutionContext("a12-conversation", "a12-execution", None)
    result = executor._execute_single_tool(call, None, context)
    return calls, result


BASE = {"repository_full_name": "boxs-51/assistant", "branch": "test/a12-A-only",
        "path": "docs/a/check.md", "content": "synthetic", "sha": "simulated-blob",
        "message": "test only",
        "a12_control": {"stack": "A", "mode": "MODE2", "epoch": 12,
                        "grant_epoch": 12, "state": "ACTIVE"}}


@pytest.mark.parametrize("tool,override", [
    ("mcp__GitHub__update_file", {"branch": None}),
    ("mcp__GitHub__create_file", {"branch": "main"}),
    ("mcp__GitHub__update_ref", {"branch": "releases/stable"}),
    ("mcp__GitHub__create_file", {"a12_control": {"state": "PAUSED", "epoch": 12}}),
    ("mcp__GitHub__update_file", {"a12_control": {"state": "REVOKED", "epoch": 12}}),
    ("mcp__GitHub__create_file", {"a12_control": {"state": "ACTIVE", "epoch": 13, "grant_epoch": 12}}),
    ("mcp__GitHub__update_file", {"a12_control": {"state": "UNKNOWN", "epoch": None}}),
    ("mcp__GitHub__enable_auto_merge", {"a12_control": {"state": "ACTIVE", "epoch": 12}}),
], ids=["missing-explicit-branch", "protected-main", "protected-release",
        "paused", "revoked", "stale-epoch", "unknown-epoch", "auto-merge"])
def test_c08_denies_untrusted_control_before_simulated_dispatch(tool, override):
    calls, _ = _attempt(tool, {**BASE, **override})
    assert calls == [], (
        "C08 consumer-side RED: routine HITL let isolated simulated GitHub tool "
        "execute without trusted current-mode/ref/epoch/revocation verification. "
        "NOT a real GitHub-host physical RED."
    )


@pytest.mark.skip(reason="External connector host owner/path and two-process dispatch fence not assigned")
def test_c08_host_two_worker_pause_revocation_barrier():
    raise AssertionError("Requires separately authorized real host process oracle")


@pytest.mark.skip(reason="External connector host credential egress/ambiguous receipts not assigned")
def test_c08_host_ambiguous_network_receipt_reconciliation():
    raise AssertionError("Requires separately authorized controlled GitHub test backend")
