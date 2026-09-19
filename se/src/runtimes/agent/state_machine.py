from typing import Dict, Set

from ...domain.schemas.agent_execution import (
    AgentExecutionState,
    AgentExecutionWaitReason,
    normalize_execution_waiting,
)


_ALLOWED: Dict[AgentExecutionState, Set[AgentExecutionState]] = {
    AgentExecutionState.CREATED: {AgentExecutionState.RUNNING, AgentExecutionState.CANCELLED},
    AgentExecutionState.RUNNING: {
        AgentExecutionState.WAITING,
        AgentExecutionState.COMPLETED,
        AgentExecutionState.FAILED,
        AgentExecutionState.CANCELLED,
        AgentExecutionState.TIMEOUT,
    },
    AgentExecutionState.WAITING: {
        AgentExecutionState.RUNNING,
        AgentExecutionState.FAILED,
        AgentExecutionState.CANCELLED,
        AgentExecutionState.TIMEOUT,
    },
    AgentExecutionState.COMPLETED: set(),
    AgentExecutionState.FAILED: set(),
    AgentExecutionState.CANCELLED: set(),
    AgentExecutionState.TIMEOUT: set(),
}


class AgentExecutionStateMachine:
    @staticmethod
    def can_transition(current: AgentExecutionState, target: AgentExecutionState) -> bool:
        return target in _ALLOWED[current]

    @staticmethod
    def transition(current: AgentExecutionState, target: AgentExecutionState) -> AgentExecutionState:
        if not AgentExecutionStateMachine.can_transition(current, target):
            raise ValueError(f"Invalid agent execution transition: {current} -> {target}")
        return target

    @staticmethod
    def validate_state(
        state: AgentExecutionState | str,
        wait_reason: AgentExecutionWaitReason | str | None,
    ) -> tuple[AgentExecutionState, AgentExecutionWaitReason | None]:
        return normalize_execution_waiting(state, wait_reason)
