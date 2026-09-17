"""HTTP DTOs for the capability control plane."""

from typing import Any, Dict, List, Literal
from pydantic import Field
from .base import GatewayBaseModel


class SkillDefinition(GatewayBaseModel):
    name: str
    description: str
    instruction: str
    version: str = "1.0"
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    metadata: Dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["CONTEXT_ONLY", "ONE_SHOT", "LONG_RUNNING"] = "CONTEXT_ONLY"
    effects: List[Literal["READ", "WRITE", "EXECUTE", "EXTERNAL_SIDE_EFFECT", "PRIVILEGED"]] = Field(default_factory=list)


class CapabilityRegistrationResponse(GatewayBaseModel):
    capability_id: str
    kind: str
    definition: Dict[str, Any]
    implementations: List[Dict[str, Any]] = Field(default_factory=list)


class CapabilityExecutionRequest(GatewayBaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)
    invocation_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = None
    connection_id: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionMessageEditRequest(GatewayBaseModel):
    content: Any


class SessionRegenerateRequest(GatewayBaseModel):
    model: str
    config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
