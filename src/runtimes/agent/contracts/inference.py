from __future__ import annotations

from typing import Any, Dict, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field


class InferenceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: Any = None
    tool_calls: list["InferenceToolCall"] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferenceToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class InferenceToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class InferenceUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class InferenceRequest(BaseModel):
    """Provider-neutral input to one model inference turn."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    execution_id: str
    iteration: int
    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[InferenceToolDefinition] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
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
