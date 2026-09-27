"""Public, bounded projection of Agent lifecycle events for chat SSE."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...domain.schemas.event import BaseEvent
from .contracts.events import AgentEventName


AGENT_STREAM_EVENT_NAMES = (
    AgentEventName.EXECUTION_CREATED,
    AgentEventName.EXECUTION_STARTED,
    AgentEventName.CONTEXT_READY,
    AgentEventName.ITERATION_STARTED,
    AgentEventName.INFERENCE_REQUESTED,
    AgentEventName.INFERENCE_COMPLETED,
    AgentEventName.ITERATION_COMPLETED,
    AgentEventName.PROGRESS,
    AgentEventName.TOOL_REQUESTED,
    AgentEventName.TOOL_STARTED,
    AgentEventName.TOOL_COMPLETED,
    AgentEventName.TOOL_FAILED,
    AgentEventName.EXECUTION_COMPLETED,
    AgentEventName.EXECUTION_FAILED,
    AgentEventName.EXECUTION_CANCELLED,
    AgentEventName.EXECUTION_TIMEOUT,
)


class AgentStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["agent_stream_event"] = "agent_stream_event"
    event_id: str
    event_type: str
    timestamp: float
    execution_id: str
    turn_id: str | None = None
    channel: Literal["lifecycle", "progress", "tool"]
    data: dict[str, Any] = Field(default_factory=dict)


def project_agent_event(event: BaseEvent) -> AgentStreamEvent:
    correlation = event.payload.get("correlation") or {}
    name = event.event_name
    if name.startswith("agent.tool."):
        channel = "tool"
        status = name.rsplit(".", 1)[-1]
        data = {
            "tool_call_id": correlation.get("tool_call_id"),
            "invocation_id": correlation.get("invocation_id"),
            "name": event.payload.get("capability_id"),
            "purpose": event.payload.get("purpose"),
            "arguments": event.payload.get("arguments"),
            "status": status,
            "error_code": event.payload.get("error_code"),
        }
    elif name == AgentEventName.PROGRESS:
        channel = "progress"
        data = {"content": str(event.payload.get("content") or "")[:1000]}
    else:
        channel = "lifecycle"
        data = {
            "status": name.rsplit(".", 1)[-1],
            "capability_ids": event.payload.get("capability_ids") if name == AgentEventName.CONTEXT_READY else None,
            "skill_ids": event.payload.get("skill_ids") if name == AgentEventName.CONTEXT_READY else None,
            "error_code": event.payload.get("error_code"),
        }
    return AgentStreamEvent(
        event_id=event.event_id,
        event_type=name,
        timestamp=event.timestamp,
        execution_id=str(correlation.get("execution_id") or ""),
        turn_id=event.turn_id,
        channel=channel,
        data={key: value for key, value in data.items() if value is not None},
    )
