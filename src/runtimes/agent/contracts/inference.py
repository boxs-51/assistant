from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Dict, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw(item) for item in value]
    return value


class InferenceMessage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    role: str
    content: Any = None
    tool_calls: tuple["InferenceToolCall", ...] = ()
    name: str | None = None
    tool_call_id: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", mode="after")
    @classmethod
    def freeze_content(cls, value: Any) -> Any:
        return _freeze(value)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Dict[str, Any]) -> MappingProxyType:
        return _freeze(value)

    @field_serializer("content", "metadata")
    def serialize_mutable_fields(self, value: Any) -> Any:
        return _thaw(value)


class InferenceToolCall(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="after")
    @classmethod
    def freeze_arguments(cls, value: Dict[str, Any]) -> MappingProxyType:
        return _freeze(value)

    @field_serializer("arguments")
    def serialize_arguments(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)


class InferenceToolDefinition(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters", mode="after")
    @classmethod
    def freeze_parameters(cls, value: Dict[str, Any]) -> MappingProxyType:
        return _freeze(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw(value)


class InferenceUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_invocations: int = 0
    estimated_cost_usd: float = 0.0

class InferenceRequest(BaseModel):
    """Provider-neutral input to one model inference turn."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    request_id: str
    execution_id: str
    iteration: int
    messages: Sequence[InferenceMessage]
    tools: Sequence[InferenceToolDefinition] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None
    cancellation_event: Any = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    """Provider-neutral output from one model inference turn."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    execution_id: str
    iteration: int
    message: InferenceMessage
    finish_reason: str | None = None
    usage: InferenceUsage = Field(default_factory=InferenceUsage)
    provider: str
    model: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferencePort(Protocol):
    async def complete(self, request: InferenceRequest) -> InferenceResponse:
        """Execute one non-streaming inference turn."""
        ...
