from __future__ import annotations

from typing import Any, Dict, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

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

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    iteration: int
    messages: Sequence[InferenceMessage] = Field(default_factory=list)
    tools: Sequence[InferenceToolDefinition] = Field(default_factory=list)
    token_estimate: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextBuilderPort(Protocol):
    async def build(
        self,
        context: AgentExecutionContext,
        request: AgentContextRequest,
    ) -> AgentContextSnapshot:
        ...
