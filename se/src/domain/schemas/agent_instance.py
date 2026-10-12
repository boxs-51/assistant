"""Durable nonwired AgentInstance identity, separate from AE worker leases."""

from enum import Enum
from pydantic import Field
from .base import GatewayBaseModel

class AgentInstanceLifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"

class AgentInstanceView(GatewayBaseModel):
    owner_user_id: str
    agent_instance_id: str
    definition_id: str
    definition_version: str
    lifecycle_state: AgentInstanceLifecycle
    generation: int = Field(ge=1)

__all__ = ["AgentInstanceLifecycle", "AgentInstanceView"]
