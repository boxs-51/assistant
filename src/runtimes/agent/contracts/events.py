from __future__ import annotations

import time
from typing import Any, Dict, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AgentEventName:
    EXECUTION_CREATED = "agent.execution.created"
    EXECUTION_STARTED = "agent.execution.started"
    ITERATION_STARTED = "agent.iteration.started"
    INFERENCE_REQUESTED = "agent.inference.requested"
    INFERENCE_COMPLETED = "agent.inference.completed"
    TOOL_REQUESTED = "agent.tool.requested"
    TOOL_STARTED = "agent.tool.started"
    TOOL_COMPLETED = "agent.tool.completed"
    TOOL_FAILED = "agent.tool.failed"
    ITERATION_COMPLETED = "agent.iteration.completed"
    EXECUTION_COMPLETED = "agent.execution.completed"
    EXECUTION_FAILED = "agent.execution.failed"
    EXECUTION_CANCELLED = "agent.execution.cancelled"
    EXECUTION_TIMEOUT = "agent.execution.timeout"


class CorrelationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    execution_id: str
    request_id: str | None = None
    parent_execution_id: str | None = None
    iteration_id: str | None = None
    tool_call_id: str | None = None
    invocation_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None


class AgentEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_name: str
    event_version: str = "1.0"
    timestamp: float = Field(default_factory=time.time)
    correlation: CorrelationContext
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentEventPublisher(Protocol):
    async def publish(self, event: AgentEventEnvelope) -> None:
        ...
