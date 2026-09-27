"""Client DTO for Agent execution budgets used by chat requests."""

from typing import Optional
from pydantic import Field

from .base import GatewayBaseModel


class AgentExecutionLimits(GatewayBaseModel):
    max_iterations: int = 8
    max_tool_calls: int = 16
    max_parallel_agents: int = 4
    max_parallel_tools: int = 4
    timeout_seconds: float = 60.0
    iteration_timeout_seconds: float = 20.0
    inference_timeout_seconds: float = 15.0
    tool_timeout_seconds: float = 10.0
    task_timeout_seconds: Optional[float] = Field(default=None, gt=0)
    max_retry_attempts: int = 1
    max_cost: Optional[float] = None
