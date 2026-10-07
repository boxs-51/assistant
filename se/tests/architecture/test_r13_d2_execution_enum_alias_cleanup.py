from __future__ import annotations

from se.src.domain.schemas.agent_execution import (
    AgentExecutionState,
    AgentExecutionWaitReason,
    normalize_execution_waiting,
)


def test_r13_d2_dead_execution_enum_source_aliases_are_absent() -> None:
    assert not hasattr(AgentExecutionState, "WAITING_AGENT")
    assert not hasattr(AgentExecutionState, "WAITING_FOR_CONNECTION")
    assert AgentExecutionState.WAITING.value == "WAITING"


def test_r13_d2_raw_legacy_waiting_strings_remain_compatible() -> None:
    state, reason = normalize_execution_waiting("WAITING_AGENT")
    assert state is AgentExecutionState.WAITING
    assert reason is AgentExecutionWaitReason.AGENT

    state, reason = normalize_execution_waiting("WAITING_FOR_CONNECTION")
    assert state is AgentExecutionState.WAITING
    assert reason is AgentExecutionWaitReason.CONNECTION

    state, reason = normalize_execution_waiting("WAITING", "CONNECTION")
    assert state is AgentExecutionState.WAITING
    assert reason is AgentExecutionWaitReason.CONNECTION
