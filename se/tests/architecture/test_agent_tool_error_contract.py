from se.src.runtimes.agent.tool_execution.errors import (
    AGENT_TOOL_NOT_VISIBLE,
    CANONICAL_TOOL_ERROR_CODES,
    CAPABILITY_CANCELLED,
    CAPABILITY_EXECUTION_FAILED,
    CAPABILITY_INVALID_ARGUMENT,
    CAPABILITY_TIMEOUT,
)
from se.src.runtimes.capability.contracts.error import CapabilityError


def test_capability_error_preserves_code_and_retryability():
    error = CapabilityError(
        code=CAPABILITY_TIMEOUT,
        message="tool timed out",
        category="TIMEOUT",
        retryable=True,
        safe_for_client=True,
        capability_id="tool.a",
        invocation_id="inv-tool-error",
    )

    assert error.code == CAPABILITY_TIMEOUT
    assert error.retryable is True


def test_canonical_tool_error_vocabulary_contains_core_agent_and_capability_codes():
    assert {
        CAPABILITY_INVALID_ARGUMENT,
        AGENT_TOOL_NOT_VISIBLE,
        CAPABILITY_TIMEOUT,
        CAPABILITY_CANCELLED,
        CAPABILITY_EXECUTION_FAILED,
    } <= CANONICAL_TOOL_ERROR_CODES
