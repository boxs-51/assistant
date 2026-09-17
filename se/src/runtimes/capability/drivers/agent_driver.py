from __future__ import annotations

from typing import Any, Mapping

from ....domain.schemas.agent import AgentDefinition
from ....domain.schemas.agent_execution import AgentExecutionLimits
from ...agent.contracts.context import AgentExecutionContext
from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition
from .base import BaseCapabilityDriver


class AgentCapabilityDriver(BaseCapabilityDriver):
    """Expose one registered server Agent through CapabilityRuntime."""

    def __init__(self, definition, agent: AgentDefinition, agent_runtime: Any):
        super().__init__(definition)
        self._agent = agent
        self._agent_runtime = agent_runtime

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        execution_context = AgentExecutionContext.create(
            execution_id=context.execution_id,
            agent_id=self._agent.name,
            session_id=context.session_id or "",
            correlation_id=(
                context.metadata.get("correlation_id")
                or context.invocation_id
            ),
            identity=context.identity,
            limits=AgentExecutionLimits(),
            request_id=context.request_id,
            agent=self._agent,
            input=dict(arguments),
            connection_id=context.connection_id,
            workflow_id=context.workflow_id,
            metadata={**context.metadata, "invocation_id": context.invocation_id},
        )
        execution_context.cancellation_event = context.cancellation_event
        result = await self._agent_runtime.execute(execution_context)
        if result.error_code:
            error = RuntimeError(result.error_message or result.error_code)
            error.code = result.error_code
            raise error
        return result.model_dump(mode="json")
