import pytest
from pydantic import ValidationError

from cl.src.schemas.agent_execution import AgentExecutionLimits
from cl.src.schemas.request import GatewayChatRequest


CANONICAL_VALUES = {
    "execution_timeout_seconds": 61.0,
    "provider_call_timeout_seconds": 16.0,
    "tool_call_timeout_seconds": 11.0,
    "iteration_timeout_seconds": 21.0,
    "task_timeout_seconds": 301.0,
}


def test_ubq5e_client_accepts_canonical_names_and_keeps_legacy_wire() -> None:
    request = GatewayChatRequest(
        model="test-model",
        messages=[],
        agent_enabled=True,
        agent_limits=CANONICAL_VALUES,
    )

    limits = request.agent_limits
    assert limits is not None
    assert limits.execution_timeout_seconds == 61.0
    assert limits.provider_call_timeout_seconds == 16.0
    assert limits.tool_call_timeout_seconds == 11.0
    assert limits.iteration_timeout_seconds == 21.0
    assert limits.task_timeout_seconds == 301.0

    wire = request.model_dump(mode="json")["agent_limits"]
    assert wire["timeout_seconds"] == 61.0
    assert wire["inference_timeout_seconds"] == 16.0
    assert wire["tool_timeout_seconds"] == 11.0
    assert wire["iteration_timeout_seconds"] == 21.0
    assert wire["task_timeout_seconds"] == 301.0
    assert "execution_timeout_seconds" not in wire
    assert "provider_call_timeout_seconds" not in wire
    assert "tool_call_timeout_seconds" not in wire


def test_ubq5e_client_legacy_defaults_and_equal_dual_spelling_are_stable() -> None:
    defaults = AgentExecutionLimits()
    assert defaults.timeout_seconds == 60.0
    assert defaults.inference_timeout_seconds == 15.0
    assert defaults.tool_timeout_seconds == 10.0

    limits = AgentExecutionLimits.model_validate(
        {
            "timeout_seconds": 42.0,
            "execution_timeout_seconds": 42.0,
            "inference_timeout_seconds": 12.0,
            "provider_call_timeout_seconds": 12.0,
            "tool_timeout_seconds": 9.0,
            "tool_call_timeout_seconds": 9.0,
        }
    )
    assert limits.execution_timeout_seconds == 42.0
    assert limits.provider_call_timeout_seconds == 12.0
    assert limits.tool_call_timeout_seconds == 9.0


@pytest.mark.parametrize(
    ("canonical", "legacy"),
    [
        ("execution_timeout_seconds", "timeout_seconds"),
        ("provider_call_timeout_seconds", "inference_timeout_seconds"),
        ("tool_call_timeout_seconds", "tool_timeout_seconds"),
    ],
)
def test_ubq5e_client_rejects_conflicting_dual_spelling(
    canonical: str,
    legacy: str,
) -> None:
    with pytest.raises(ValidationError, match="Conflicting timeout fields"):
        AgentExecutionLimits.model_validate(
            {
                canonical: 9.0,
                legacy: 10.0,
            }
        )
