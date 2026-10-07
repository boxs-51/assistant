from __future__ import annotations

import pytest

from se.src.domain.schemas.agent_execution import (
    AgentExecutionState,
    AgentExecutionWaitReason,
    normalize_execution_waiting,
)


def test_r13_d2_dead_execution_enum_source_aliases_are_absent() -> None:
    assert not hasattr(AgentExecutionState, "WAITING_AGENT")
    assert not hasattr(AgentExecutionState, "WAITING_FOR_CONNECTION")
    assert AgentExecutionState.WAITING.value == "WAITING"


def test_r13_d3_raw_legacy_waiting_strings_fail_closed() -> None:
    with pytest.raises(ValueError):
        normalize_execution_waiting("WAITING_AGENT")
    with pytest.raises(ValueError):
        normalize_execution_waiting("WAITING_FOR_CONNECTION")


def test_r13_d3_canonical_waiting_still_requires_explicit_reason() -> None:
    state, reason = normalize_execution_waiting("WAITING", "CONNECTION")
    assert state is AgentExecutionState.WAITING
    assert reason is AgentExecutionWaitReason.CONNECTION

    state, reason = normalize_execution_waiting("WAITING", "AGENT")
    assert state is AgentExecutionState.WAITING
    assert reason is AgentExecutionWaitReason.AGENT

    with pytest.raises(ValueError, match="requires wait_reason"):
        normalize_execution_waiting("WAITING")
    with pytest.raises(ValueError, match="only valid"):
        normalize_execution_waiting("RUNNING", "CONNECTION")
