from __future__ import annotations

from typing import Any, Dict, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .context import AgentExecutionContext


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    iteration: int
    invocation_id: str
    tool_call_id: str
    capability_id: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    iteration: int
    invocation_id: str
    tool_call_id: str
    capability_id: str
    success: bool
    output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolExecutionPort(Protocol):
    async def execute(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        ...

    async def execute_many(
        self,
        context: AgentExecutionContext,
        requests: Sequence[ToolExecutionRequest],
        *,
        max_parallel: int,
    ) -> Sequence[ToolExecutionResult]:
        ...
