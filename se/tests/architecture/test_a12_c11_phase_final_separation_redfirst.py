"""A12-C11 RED-first consumer contract for DEV_S0_S5 vs M3/M5/S8.

The target is actual CL ToolExecutor with a NON-NETWORK fake privileged
function. It cannot certify physical external GitHub/M3 gate state.
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


BASE = {"authority_kind": "DEV_S0_S5", "actor": "DEVELOPER",
        "head": "test-head-1", "evidence_head": "test-head-1",
        "reported_ci": "PASS", "actual_ci": "PASS"}


@pytest.mark.parametrize("tool,override", [
    ("mcp__GitHub__merge_pull_request", {"phase": "S7", "owner_merge_plan": None}),
    ("mcp__GitHub__enable_auto_merge", {"phase": "S7", "owner_merge_plan": None}),
    ("a12.production.deploy", {"phase": "S8", "production_permission": None}),
    ("a12.m3.finalize", {"phase": "S6", "reviewer": "DEVELOPER"}),
    ("a12.m3.finalize", {"phase": "S6", "reviewer": "SELF_AUDIT",
                          "claimed_independent_human": True}),
    ("a12.phase.advance", {"phase": "S5", "head": "test-head-2",
                           "evidence_head": "test-head-1"}),
    ("a12.phase.advance", {"phase": "S5", "reported_ci": "PASS", "actual_ci": "RED"}),
], ids=["phase-cannot-merge", "phase-cannot-auto-merge", "phase-cannot-deploy",
        "phase-is-not-m3", "fake-independent-review", "stale-head", "real-ci-red"])
def test_c11_dev_phase_does_not_dispatch_privileged_or_final_action(tool, override):
    calls, _ = _attempt(tool, {**BASE, **override})
    assert calls == [], (
        "C11 consumer-side RED: developer-only phase record reached isolated "
        "privileged/final action without typed M3 or Owner authorization; "
        "NOT evidence of a real GitHub merge or deployment."
    )
