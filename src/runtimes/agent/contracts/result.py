from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .inference import InferenceMessage, InferenceUsage
from .loop import AgentIteration, AgentLoopState
from .tool import ToolExecutionResult


class AgentExecutionResult(BaseModel):
    """Deterministic result of one single-agent runtime execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    agent_id: str
    state: AgentLoopState
    output: Any = None
    final_message: InferenceMessage | None = None
    iterations: tuple[AgentIteration, ...] = ()
    last_tool_results: tuple[ToolExecutionResult, ...] = ()
    usage: InferenceUsage = Field(default_factory=InferenceUsage)
    error_code: str | None = None
    error_message: str | None = None