from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


RealtimeMessageType = Literal[
    "connection.register",
    "connection.registered",
    "connection.heartbeat",
    "connection.state",
    "capability.register",
    "capability.registered",
    "capability.unregister",
    "capability.invoke",
    "capability.progress",
    "capability.result",
    "capability.error",
    "capability.cancel",
    "capability.cancelled",
    "assistant.delta",
    "assistant.completed",
    "assistant.error",
    "execution.resume",
    "execution.resume.accepted",
]


class RealtimeEnvelope(BaseModel):
    """Shared envelope for multiplexed realtime connection messages."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    type: RealtimeMessageType
    message_id: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    session_id: Optional[str] = None
    connection_id: Optional[str] = None
    execution_id: Optional[str] = None
    invocation_id: Optional[str] = None
    trace_id: Optional[str] = None

    payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_correlation_contract(self) -> "RealtimeEnvelope":
        invocation_required = {
            "capability.invoke",
            "capability.progress",
            "capability.result",
            "capability.error",
            "capability.cancel",
            "capability.cancelled",
        }

        if self.type in invocation_required and not self.invocation_id:
            raise ValueError(
                f"Realtime message '{self.type}' requires invocation_id"
            )

        connection_required = invocation_required | {
            "connection.register",
            "capability.register",
            "capability.registered",
            "execution.resume",
            "execution.resume.accepted",
        }
        if self.type in connection_required and not self.connection_id:
            raise ValueError(
                f"Realtime message '{self.type}' requires connection_id"
            )

        return self
