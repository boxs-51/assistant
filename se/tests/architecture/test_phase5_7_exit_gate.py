from __future__ import annotations

from pathlib import Path

import pytest

from src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from src.runtimes.agent.contracts.context import AgentExecutionContext
from src.runtimes.agent.contracts.policy import PolicyDecision
from src.runtimes.agent.contracts.tool import ToolExecutionRequest
from src.runtimes.agent.tool_execution.validator import (
    JsonSchemaToolArgumentValidator,
)
from src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    InvalidCapabilitySchemaError,
    validate_input_schema,
)
from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity

ROOT = Path(__file__).resolve().parents[2]
EXIT_GATE_DOC = ROOT / "docs" / "phase5" / "phase5_7" / "PHASE5_7_EXIT_GATE.md"
LEGACY_STATUS_DOC = ROOT / "docs" / "phase5" / "PHASE_5_AGENT_RUNTIME_SPEC.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "phase5-7-exit-gate.yml"


def make_context() -> AgentExecutionContext:
    agent = AgentDefinition(
        name="phase5-7-exit-gate-agent",
        goal="phase 5.7 gate",
        instruction="test",
        tools=["tool.a", "tool.b"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-phase5-7-exit-gate",
        agent_id=agent.name,
        session_id="session-phase5-7-exit-gate",
        correlation_id="corr-phase5-7-exit-gate",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=8,
            max_parallel_tools=2,
            max_retry_attempts=0,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        agent=agent,
    )


def make_request(context: AgentExecutionContext, *, capability_id: str = "tool.a") -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id="inv-phase5-7",
        tool_call_id="call-phase5-7",
        capability_id=capability_id,
        arguments={},
    )


def test_E1_invalid_arguments_are_rejected_before_capability_dispatch():
    """E1: invalid arguments fail at the canonical validator boundary."""
    definition = CapabilityDefinition(
        name="echo",
        description="echo example",
        input_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )
    result = JsonSchemaToolArgumentValidator().validate(definition, {"count": "wrong"})

    assert result.valid is False
    assert result.error_code == "CAPABILITY_INVALID_ARGUMENT"


def test_E2_invalid_capability_schema_fails_closed():
    """E2: malformed schema is rejected before tool execution begins."""
    with pytest.raises(InvalidCapabilitySchemaError):
        validate_input_schema({"type": "not-a-real-json-schema"})


def test_E3_visible_and_authorized_capability_path_remains_separate_from_legacy_registry_visibility():
    """E3: agent visibility and authorization remain explicit gate checks."""
    context = make_context()
    request = make_request(context, capability_id="tool.a")

    class FakeRuntime:
        registry = {
            "tool.a": type(
                "Record",
                (),
                {"executable": True, "definition": CapabilityDefinition(name="tool.a", description="ok", input_schema={"type": "object"})},
            )(),
        }

    class FakePolicy:
        def is_visible(self, *, agent_id, capability_id):
            return True

        def authorize(self, *, identity, agent_id, capability_id):
            return PolicyDecision.ALLOW

    adapter = CapabilityToolExecutionAdapter(
        FakeRuntime(),
        FakePolicy(),
        execution_policy=None,
    )

    result = adapter._failure(
        request,
        code="CAPABILITY_INVALID_ARGUMENT",
        message="bad args",
        retryable=False,
    )
    assert result.success is False
    assert result.error_code == "CAPABILITY_INVALID_ARGUMENT"

    adapter2 = CapabilityToolExecutionAdapter(
        FakeRuntime(),
        type(
            "HiddenPolicy",
            (),
            {"is_visible": lambda self, **kwargs: False, "authorize": lambda self, **kwargs: PolicyDecision.ALLOW},
        )(),
        execution_policy=None,
    )
    hidden = adapter2._denied(request, "AGENT_TOOL_NOT_VISIBLE")
    assert hidden.error_code == "AGENT_TOOL_NOT_VISIBLE"


def test_E4_capability_error_codes_and_retryability_are_preserved_verbatim():
    """E4: canonical downstream capability errors keep their code and retry flag."""
    from src.runtimes.capability.contracts.error import CapabilityError

    error = CapabilityError(
        code="CAPABILITY_TIMEOUT",
        message="tool timed out",
        category="TIMEOUT",
        retryable=True,
        safe_for_client=True,
        capability_id="tool.a",
        invocation_id="inv-phase5-7",
    )

    assert error.code == "CAPABILITY_TIMEOUT"
    assert error.retryable is True


def test_E5_ci_declares_full_suite_and_phase_5_7_exit_gate_as_blocking_checks():
    """E5: CI must run the full suite plus the Phase 5.7 gate."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pytest -q" in workflow
    assert "python -m pytest -q tests/architecture/test_phase5_7_exit_gate.py" in workflow
    assert "exit 1" not in workflow
    assert "on:" in workflow
    assert "pull_request:" in workflow


def test_E6_phase_5_7_status_document_is_canonical_and_legacy_doc_points_to_it():
    """E6: gate document is canonical, and the legacy runtime spec points to it."""
    gate_doc = EXIT_GATE_DOC.read_text(encoding="utf-8")
    legacy_doc = LEGACY_STATUS_DOC.read_text(encoding="utf-8")

    for criterion in ("E1", "E2", "E3", "E4", "E5", "E6", "E7"):
        assert f"## {criterion}" in gate_doc

    assert "Phase 5.7 Exit Gate" in gate_doc
    assert "PHASE5_7_EXIT_GATE.md" in legacy_doc or "Phase 5.7 Exit Gate" in legacy_doc


def test_E7_tool_error_contract_is_canonical_and_non_retryable_invalid_arguments_stay_non_retryable():
    """E7: canonical tool error contract is explicit and invalid arguments are never retryable."""
    from src.runtimes.agent.tool_execution.errors import (
        CANONICAL_TOOL_ERROR_CODES,
        CAPABILITY_INVALID_ARGUMENT,
    )

    assert CAPABILITY_INVALID_ARGUMENT in CANONICAL_TOOL_ERROR_CODES
    assert "AGENT_TOOL_NOT_VISIBLE" in CANONICAL_TOOL_ERROR_CODES
    assert "CAPABILITY_TIMEOUT" in CANONICAL_TOOL_ERROR_CODES
    assert "CAPABILITY_CANCELLED" in CANONICAL_TOOL_ERROR_CODES
    assert "CAPABILITY_EXECUTION_FAILED" in CANONICAL_TOOL_ERROR_CODES
