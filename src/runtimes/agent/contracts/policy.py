from __future__ import annotations

from enum import Enum
from typing import Protocol

from ....domain.schemas.agent_execution import AgentExecutionLimits
from ....domain.schemas.identity import Identity

from .context import AgentExecutionContext
from .tool import ToolExecutionRequest


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AgentToolPolicy(Protocol):
    """Controls tool visibility and authorization for an agent."""

    def is_visible(
        self,
        *,
        agent_id: str,
        capability_id: str,
    ) -> bool:
        ...

    def authorize(
        self,
        *,
        identity: Identity,
        agent_id: str,
        capability_id: str,
    ) -> PolicyDecision:
        ...


class AgentExecutionPolicy(Protocol):
    """Controls execution-level budgets and admission decisions."""

    def check_start(self, context: AgentExecutionContext) -> PolicyDecision:
        ...

    def check_iteration(
        self,
        context: AgentExecutionContext,
        iteration: int,
    ) -> PolicyDecision:
        ...

    def check_tool_call(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> PolicyDecision:
        ...

    def limits(self, context: AgentExecutionContext) -> AgentExecutionLimits:
        ...
