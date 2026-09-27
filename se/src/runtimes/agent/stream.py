"""Public assistant responses and tool calls for chat SSE."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...domain.schemas.event import BaseEvent
from .contracts.events import AgentEventName


AGENT_STREAM_EVENT_NAMES = (
    AgentEventName.PROGRESS,
    AgentEventName.TOOL_REQUESTED,
    AgentEventName.TOOL_STARTED,
    AgentEventName.TOOL_COMPLETED,
    AgentEventName.TOOL_FAILED,
)


class AgentStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["agent_stream_event"] = "agent_stream_event"
    event_id: str
    event_type: str
    timestamp: float
    execution_id: str
    turn_id: str | None = None
    channel: Literal["response", "tool"]
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
        channel = "response"
        data = {"content": str(event.payload.get("content") or ""), "final": False}
    else:
        raise ValueError(f"Unsupported public agent event: {name}")
    return AgentStreamEvent(
        event_id=event.event_id,
        event_type="agent.response" if name == AgentEventName.PROGRESS else name,
        timestamp=event.timestamp,
        execution_id=str(correlation.get("execution_id") or ""),
        turn_id=event.turn_id,
        channel=channel,
        data={key: value for key, value in data.items() if value is not None},
    )
