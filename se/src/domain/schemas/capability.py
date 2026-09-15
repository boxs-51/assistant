"""HTTP DTOs for the capability control plane."""

from typing import Any, Dict, List
from pydantic import Field
from .base import GatewayBaseModel


class SkillDefinition(GatewayBaseModel):
    name: str
    description: str
    instruction: str
    version: str = "1.0"
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapabilityRegistrationResponse(GatewayBaseModel):
    capability_id: str
    kind: str
    definition: Dict[str, Any]
    implementations: List[Dict[str, Any]] = Field(default_factory=list)


class SessionMessageEditRequest(GatewayBaseModel):
    content: Any


class SessionRegenerateRequest(GatewayBaseModel):
    model: str
    config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
