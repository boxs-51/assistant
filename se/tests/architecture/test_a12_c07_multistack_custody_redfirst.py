"""A12-C07 RED-first consumer witness on the REAL Client ToolExecutor.

Tool function is isolated and has NO network access. Failed tests indicate
absence of A12 stack custody at THIS consumer seam, not a GitHub-host exploit.
Actual GitHub credential broker custody and source T2 remain separately HOLD.
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


BASE = {
    "repository_full_name": "boxs-51/assistant",
    "branch": "test/a12-A-only", "path": "docs/a/readme.md",
    "content": "synthetic", "sha": "test-blob", "message": "test only",
    "a12_claim": {"stack": "A", "mode_epoch": 12, "owner": "A"},
}


@pytest.mark.parametrize("override", [
    {"branch": "test/a12-B-only"},
    {"branch": "test/a12-unknown"},
    {"path": "docs/b/foreign.py"},
    {"path": "docs/shared/schema.py"},
    {"path": "docs/a/%2e%2e/b/traversal"},
    {"a12_claim": {"stack": "A", "mode_epoch": 12, "ancestry_valid": False}},
    {"a12_claim": {"stack": "A", "mode_epoch": 12, "shared_custody": "UNKNOWN"}},
    {"path": "docs/b/renamed.py", "a12_claim": {
        "stack": "A", "rename_from": "docs/a/readme.md", "rename_to": "docs/b/renamed.py"}},
], ids=["other-stack", "unassigned-ref", "foreign-owner", "shared-no-joint-grant",
        "encoded-path", "stale-parent", "unproven-dependency", "rename-cross-owner"])
def test_c07_denies_invalid_stack_custody_before_simulated_dispatch(override):
    calls, _ = _attempt("mcp__GitHub__update_file", {**BASE, **override})
    assert calls == [], (
        "C07 consumer-side RED: actual ToolExecutor dispatched isolated simulated "
        "GitHub handler without a trusted stack/path custody receipt; "
        "NOT evidence that actual external GitHub host dispatched."
    )


@pytest.mark.parametrize("stack,branch,path", [
    ("A", "test/a12-A-only", "docs/a/readme.md"),
    ("B", "test/a12-B-only", "docs/b/readme.md"),
])
def test_c07_positive_isolated_tool_routing_controls(stack, branch, path):
    payload = {**BASE, "branch": branch, "path": path,
               "a12_claim": {"stack": stack, "mode_epoch": 12, "owner": stack}}
    calls, result = _attempt("mcp__GitHub__update_file", payload)
    assert len(calls) == 1
    assert result.role == "tool"  # routing positive; NOT an authorization proof.
