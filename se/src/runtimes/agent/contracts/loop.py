from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class AgentLoopState(str, Enum):
    PREPARING = "PREPARING"
    THINKING = "THINKING"
    TOOL_CALLING = "TOOL_CALLING"
    WAITING_TOOL = "WAITING_TOOL"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


_ALLOWED_TRANSITIONS = {
    AgentLoopState.PREPARING: {
        AgentLoopState.THINKING,
        AgentLoopState.FAILED,
        AgentLoopState.CANCELLED,
        AgentLoopState.TIMEOUT,
    },
    AgentLoopState.THINKING: {
        AgentLoopState.TOOL_CALLING,
        AgentLoopState.FINALIZING,
        AgentLoopState.FAILED,
        AgentLoopState.CANCELLED,
        AgentLoopState.TIMEOUT,
    },
    AgentLoopState.TOOL_CALLING: {
        AgentLoopState.WAITING_TOOL,
        AgentLoopState.THINKING,
        AgentLoopState.FAILED,
        AgentLoopState.CANCELLED,
        AgentLoopState.TIMEOUT,
    },
    AgentLoopState.WAITING_TOOL: {
        AgentLoopState.THINKING,
        AgentLoopState.FAILED,
        AgentLoopState.CANCELLED,
        AgentLoopState.TIMEOUT,
    },
    AgentLoopState.FINALIZING: {
        AgentLoopState.COMPLETED,
        AgentLoopState.FAILED,
        AgentLoopState.CANCELLED,
    },
    AgentLoopState.COMPLETED: set(),
    AgentLoopState.FAILED: set(),
    AgentLoopState.CANCELLED: set(),
    AgentLoopState.TIMEOUT: set(),
}


def validate_transition(
    current: AgentLoopState,
    target: AgentLoopState,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"Invalid agent loop transition: {current.value} -> {target.value}"
        )


def transition(
    current: AgentLoopState,
    target: AgentLoopState,
) -> AgentLoopState:
    validate_transition(current, target)
    return target


@dataclass(slots=True)
class AgentIteration:
    execution_id: str
    iteration: int
    state: AgentLoopState = AgentLoopState.PREPARING
    inference_request_id: Optional[str] = None
    tool_call_ids: List[str] = field(default_factory=list)
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    completed_at: Optional[datetime] = None
    error_code: Optional[str] = None

    def close(
        self,
        state: AgentLoopState,
        *,
        error_code: str | None = None,
    ) -> None:
        self.state = transition(self.state, state)
        self.error_code = error_code
        self.completed_at = datetime.now(timezone.utc)
