from enum import Enum
from typing import Any, Dict, List, Optional

from .base import GatewayBaseModel
from pydantic import Field, model_validator


class AgentSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AgentTaskStatus(str, Enum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    WAITING_FOR_CONNECTION = "WAITING"


class AgentMessageType(str, Enum):
    USER = "user.message"
    TASK_REQUEST = "task.request"
    TASK_RESULT = "task.result"
    AGENT_MESSAGE = "agent.message"


class AgentSession(GatewayBaseModel):
    session_id: str
    owner_user_id: str
    agent_ids: List[str] = Field(default_factory=list)
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    created_at: float
    updated_at: float


class AgentMessage(GatewayBaseModel):
    message_id: str
    session_id: str
    sender_id: str
    recipient_id: Optional[str] = None
    message_type: AgentMessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: float


class AgentTask(GatewayBaseModel):
    task_id: str
    session_id: str
    created_by: str
    assigned_agent_id: str
    revision: int = Field(default=0, ge=0)
    parent_task_id: Optional[str] = None
    connection_id: Optional[str] = None
    client_id: Optional[str] = None
    status: AgentTaskStatus = AgentTaskStatus.CREATED
    wait_reasons: List[str] = Field(default_factory=list)
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_waiting(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if values.get("status") == "WAITING_FOR_CONNECTION":
            values["status"] = AgentTaskStatus.WAITING
            values.setdefault("wait_reasons", ["CONNECTION"])
        return values


class AgentSessionCreateRequest(GatewayBaseModel):
    agent_ids: List[str] = Field(default_factory=list)


class AgentJoinRequest(GatewayBaseModel):
    agent_id: str


class AgentMessageRequest(GatewayBaseModel):
    session_id: str
    sender_id: str
    message_type: AgentMessageType = AgentMessageType.AGENT_MESSAGE
    payload: Dict[str, Any] = Field(default_factory=dict)
    recipient_id: Optional[str] = None


class AgentTaskCreateRequest(GatewayBaseModel):
    session_id: str
    assigned_agent_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    parent_task_id: Optional[str] = None
    connection_id: Optional[str] = None
