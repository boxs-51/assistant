from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REAL_E2E = (
    ROOT
    / "se"
    / "tests"
    / "e2e"
    / "test_phase6_10_true_websocket_agent_client_loop.py"
)

CLAIMED_PATHS = (
    "se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py",
    "se/tests/architecture/test_r14_c_hitl_real_e2e_boundary.py",
)

FORBIDDEN_PRODUCTION_PREFIXES = (
    "cl/src/hitl/",
    "cl/src/ui/",
    "cl/src/core/",
    "se/src/",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_r14_c_claim_is_exactly_two_test_evidence_paths() -> None:
    assert CLAIMED_PATHS == (
        "se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py",
        "se/tests/architecture/test_r14_c_hitl_real_e2e_boundary.py",
    )
    assert all(path.startswith("se/tests/") for path in CLAIMED_PATHS)
    assert all(
        not path.startswith(FORBIDDEN_PRODUCTION_PREFIXES)
        for path in CLAIMED_PATHS
    )


def test_r14_c_binds_approve_and_deny_to_real_agent_websocket_path() -> None:
    source = REAL_E2E.read_text(encoding="utf-8")

    for name in (
        "test_r14_c_hitl_approve_real_websocket_agent_path",
        "test_r14_c_hitl_deny_real_websocket_agent_path_fails_closed",
    ):
        assert f"def {name}" in source

    for proof in (
        "HITLManager()",
        'hitl.set_approval_callback(approve_callback)',
        '"base_risk": "HIGH"',
        "GatewayRealtimeClient(",
        "CapabilityDispatcher(",
        "AgentRuntime(",
        'assert approval["risk_level"] == "HIGH"',
        "assert len(approvals) == 1",
        "assert len(invocations) == 1",
        "assert invocation.attempt == 1",
        "attempts = await store.list_attempts(invocation.invocation_id)",
        "assert len(attempts) == 1",
        "assert attempts[0].attempt_number == 1",
        "assert attempts[0].implementation_id == implementation_id",
        "assert attempts[0].connection_id == CONNECTION_ID",
        "assert tool_message.role == \"tool\"",
        "assert tool_message.name == CAPABILITY_ID",
        'assert tool_message.tool_call_id == "call-e2e-1"',
        'assert tool_message.metadata["success"] is True',
        'assert tool_message.metadata["retryable"] is False',
        "assert dict(tool_message.content) == {",
        '"echo": "hello-from-agent",',
        '"executed_on": "client",',
        'assert executed_tools == []',
        'assert tool_message.metadata["success"] is False',
        'assert dict(tool_message.content)["error_code"] == "HITL_DENIED"',
        'dict(tool_message.content)["error_message"] == f"Local user denied capability',
    ):
        assert proof in source

    r14c = source[source.index("def _build_r14_c_high_risk_client_registry") :]
    assert ".dispatch(" not in r14c
    assert "handle_inbound(" not in r14c
    assert "send_result(" not in r14c
    assert "manually injected capability.result" in source


def test_r14_c_preserves_parent_gap_and_production_authority_fence() -> None:
    parent = _text(
        "docs/agent_execution_r14/"
        "R14_A_HEAD_AUDIT_FAULT_MATRIX_CONTRACT_FREEZE_10071F4E.md"
    )
    source = REAL_E2E.read_text(encoding="utf-8")

    assert "R14-GAP-HITL-E2E-1" in parent
    assert "GAP / NO REPAIR AUTHORITY" in parent
    assert "R14-GAP-REMOTE-PRE-SIDE-EFFECT-1" in parent

    assert "cl.src.hitl.hitl_manager import HITLManager" in source
    assert "Respect explicit human approval." in source
    assert "r14-c-hitl-approve-execution" in source
    assert "r14-c-hitl-deny-execution" in source
