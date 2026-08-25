from enum import Enum
from typing import Any, Dict, Optional

from .base import GatewayBaseModel
from pydantic import Field


class AgentExecutionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_AGENT = "WAITING_AGENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class AgentExecution(GatewayBaseModel):
    execution_id: str
    session_id: str
    agent_id: str
    task_id: Optional[str] = None
    parent_execution_id: Optional[str] = None
    correlation_id: str
    state: AgentExecutionState = AgentExecutionState.CREATED
    request: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float


class AgentExecutionLimits(GatewayBaseModel):
    max_iterations: int = 8
    max_tool_calls: int = 16
    max_parallel_agents: int = 4
    timeout_seconds: float = 60.0
    max_cost: Optional[float] = None


class AgentExecutionRequest(GatewayBaseModel):
    session_id: str
    agent_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    parent_execution_id: Optional[str] = None
    limits: AgentExecutionLimits = Field(default_factory=AgentExecutionLimits)
