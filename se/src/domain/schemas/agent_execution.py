from enum import Enum
from typing import Any, Dict, Optional

from .base import GatewayBaseModel
from pydantic import Field, model_validator


class AgentExecutionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    # Source-compatible aliases.  They serialize to the canonical wire value
    # and must not be used by runtime logic.
    WAITING_AGENT = "WAITING"
    WAITING_FOR_CONNECTION = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class AgentExecutionWaitReason(str, Enum):
    NONE = "NONE"
    CONNECTION = "CONNECTION"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    DEPENDENCY = "DEPENDENCY"
    RESOURCE = "RESOURCE"
    EXPLICIT_PAUSE = "EXPLICIT_PAUSE"
    RECOVERY = "RECOVERY"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    AGENT = "AGENT"


_LEGACY_WAITING_STATES = {
    "WAITING_FOR_CONNECTION": AgentExecutionWaitReason.CONNECTION,
    "WAITING_AGENT": AgentExecutionWaitReason.AGENT,
}


def normalize_execution_waiting(
    state: AgentExecutionState | str,
    wait_reason: AgentExecutionWaitReason | str | None = None,
) -> tuple[AgentExecutionState, AgentExecutionWaitReason | None]:
    """Normalize legacy execution state payloads at compatibility boundaries."""
    raw_state = state.value if isinstance(state, AgentExecutionState) else str(state)
    legacy_reason = _LEGACY_WAITING_STATES.get(raw_state)
    normalized_state = (
        AgentExecutionState.WAITING
        if legacy_reason is not None
        else AgentExecutionState(raw_state)
    )
    normalized_reason = (
        AgentExecutionWaitReason(wait_reason)
        if wait_reason not in (None, AgentExecutionWaitReason.NONE, "NONE")
        else legacy_reason
    )
    if normalized_state is AgentExecutionState.WAITING and normalized_reason is None:
        raise ValueError("WAITING execution requires wait_reason")
    if normalized_state is not AgentExecutionState.WAITING and normalized_reason is not None:
        raise ValueError("wait_reason is only valid for WAITING execution")
    return normalized_state, normalized_reason


class AgentExecution(GatewayBaseModel):
    execution_id: str
    session_id: str
    agent_id: str
    task_id: Optional[str] = None
    parent_execution_id: Optional[str] = None
    correlation_id: str
    state: AgentExecutionState = AgentExecutionState.CREATED
    wait_reason: Optional[AgentExecutionWaitReason] = None
    revision: int = 0
    request: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float

    @model_validator(mode="before")
    @classmethod
    def _normalize_waiting_contract(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        state, reason = normalize_execution_waiting(
            values.get("state", AgentExecutionState.CREATED),
            values.get("wait_reason"),
        )
        values["state"] = state
        values["wait_reason"] = reason
        return values


class AgentExecutionLimits(GatewayBaseModel):
    max_iterations: int = 8
    max_tool_calls: int = 16
    max_parallel_agents: int = 4
    max_parallel_tools: int = 4
    timeout_seconds: float = 60.0
    iteration_timeout_seconds: float = 20.0
    inference_timeout_seconds: float = 15.0
    tool_timeout_seconds: float = 10.0
    max_retry_attempts: int = 1
    max_cost: Optional[float] = None


class AgentExecutionRequest(GatewayBaseModel):
    session_id: str
    agent_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    parent_execution_id: Optional[str] = None
    limits: AgentExecutionLimits = Field(default_factory=AgentExecutionLimits)
