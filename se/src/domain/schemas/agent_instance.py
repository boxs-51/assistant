"""AIC-0 P1A: durable, nonwired AgentInstance identity contract."""
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class AgentInstanceState(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class AgentInstanceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    agent_instance_id: str = Field(min_length=1, max_length=255)
    owner_user_id: str = Field(min_length=1, max_length=255)
    definition_id: str = Field(min_length=1, max_length=255)
    definition_version: str = Field(min_length=1, max_length=255)
    generation: int = Field(ge=1)
    state: AgentInstanceState


class AgentInstanceConflict(RuntimeError):
    """Reject stale generation, ownership changes and retired ID replay."""


class AgentInstanceDenied(PermissionError):
    """No authenticated active durable owner or LIVE authority."""
