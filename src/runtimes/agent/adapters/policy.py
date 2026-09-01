from __future__ import annotations

import asyncio
from typing import Any

from ....application.policy.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from ....agent.registry import AgentRegistry
from ....runtimes.capability.registry import CapabilityRegistry
from ..contracts.context import AgentExecutionContext
from ..contracts.policy import (
    AgentExecutionPolicy,
    AgentToolPolicy,
    PolicyDecision,
)
from ..contracts.tool import ToolExecutionRequest


class RegistryAgentToolPolicy(AgentToolPolicy):
    """Canonical Agent -> Capability visibility/authorization policy."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        capability_registry: CapabilityRegistry,
        authorization: AuthorizationService,
    ) -> None:
        self._agents = agent_registry
        self._capabilities = capability_registry
        self._authorization = authorization

    def is_visible(self, *, agent_id: str, capability_id: str) -> bool:
        agent = self._agents.get(agent_id)
        record = self._capabilities.get(capability_id)
        if agent is None or record is None or not record.executable:
            return False
        return capability_id in set(agent.tools or [])

    def authorize(
        self,
        *,
        identity,
        agent_id: str,
        capability_id: str,
    ) -> PolicyDecision:
        if not self.is_visible(agent_id=agent_id, capability_id=capability_id):
            return PolicyDecision.DENY
        record = self._capabilities.get(capability_id)
        if record is None:
            return PolicyDecision.DENY
        return (
            PolicyDecision.ALLOW
            if self._authorization.authorize(identity, record.driver)
            is AuthorizationDecision.ALLOW
            else PolicyDecision.DENY
        )


class DefaultAgentExecutionPolicy(AgentExecutionPolicy):
    """Admission/budget policy for one Agent execution."""

    def check_start(self, context: AgentExecutionContext) -> PolicyDecision:
        try:
            context.ensure_active()
        except (asyncio.CancelledError, TimeoutError):
            return PolicyDecision.DENY
        return PolicyDecision.ALLOW

    def check_iteration(
        self,
        context: AgentExecutionContext,
        iteration: int,
    ) -> PolicyDecision:
        if iteration < 1 or iteration > context.limits.max_iterations:
            return PolicyDecision.DENY
        try:
            context.ensure_active()
        except (asyncio.CancelledError, TimeoutError):
            return PolicyDecision.DENY
        return PolicyDecision.ALLOW

    def check_tool_call(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> PolicyDecision:
        if request.execution_id != context.execution_id:
            return PolicyDecision.DENY
        if context.tool_calls_used >= context.limits.max_tool_calls:
            return PolicyDecision.DENY
        try:
            context.ensure_active()
        except (asyncio.CancelledError, TimeoutError):
            return PolicyDecision.DENY
        return PolicyDecision.ALLOW

    def limits(self, context: AgentExecutionContext):
        return context.limits