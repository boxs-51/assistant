from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Dict, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .context import AgentExecutionContext
from .inference import InferenceMessage, InferenceToolDefinition
from .tool import ToolExecutionResult


class AgentContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    iteration: int
    input: Mapping[str, Any] = Field(default_factory=dict)
    prior_messages: Sequence[Mapping[str, Any]] = Field(default_factory=list)
    tool_results: Sequence[ToolExecutionResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentContextSnapshot(BaseModel):
    """Snapshot consumed by exactly one inference turn."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    execution_id: str
    iteration: int
    messages: tuple[InferenceMessage, ...] = ()
    tools: tuple[InferenceToolDefinition, ...] = ()
    token_estimate: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Dict[str, Any]) -> MappingProxyType:
        from .inference import _freeze
        return _freeze(value)

    @field_serializer("metadata")
    def serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        from .inference import _thaw
        return _thaw(value)


class ContextBuilderPort(Protocol):
    async def build(
        self,
        context: AgentExecutionContext,
        request: AgentContextRequest,
    ) -> AgentContextSnapshot:
        ...
