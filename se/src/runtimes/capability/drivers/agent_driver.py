from __future__ import annotations

from typing import Any, Mapping

from ....domain.schemas.agent import AgentDefinition
from ....domain.schemas.agent_execution import AgentExecutionLimits
from ...agent.contracts.context import AgentExecutionContext
from ...agent.ids import AgentExecutionIdFactory
from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition
from .base import BaseCapabilityDriver


class AgentCapabilityDriver(BaseCapabilityDriver):
    """Expose one registered server Agent through CapabilityRuntime."""

    def __init__(
        self,
        definition,
        agent: AgentDefinition,
        agent_runtime: Any,
        execution_id_factory: AgentExecutionIdFactory | None = None,
        execution_supervisor: Any | None = None,
    ):
        super().__init__(definition)
        self._agent = agent
        self._agent_runtime = agent_runtime
        self._execution_supervisor = execution_supervisor
        self._execution_id_factory = (
            execution_id_factory or AgentExecutionIdFactory()
        )

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        if (
            context.caller_agent_execution_id is not None
            and context.caller_agent_execution_id != context.execution_id
        ):
            raise ValueError(
                "Delegating Agent execution must own the capability invocation."
            )

        child_limits = AgentExecutionLimits()
        budget_bounds = [float(child_limits.timeout_seconds)]
        for candidate in (
            context.caller_execution_remaining_seconds,
            context.caller_iteration_remaining_seconds,
            context.remaining_seconds,
        ):
            if candidate is not None:
                budget_bounds.append(max(0.0, float(candidate)))
        child_budget = min(budget_bounds)
        child_limits = child_limits.model_copy(
            update={"timeout_seconds": child_budget}
        )

        execution_context = AgentExecutionContext.create(
            execution_id=self._execution_id_factory.new_id(),
            agent_id=self._agent.name,
            session_id=context.session_id or "",
            correlation_id=(
                context.correlation_id
                or context.metadata.get("correlation_id")
                or context.invocation_id
            ),
            identity=context.identity,
            limits=child_limits,
            remaining_active_budget_seconds=child_budget,
            request_id=context.request_id,
            task_id=context.task_id,
            branch_id=context.branch_id,
            parent_execution_id=context.caller_agent_execution_id,
            agent=self._agent,
            input=dict(arguments),
            connection_id=context.connection_id,
            workflow_id=context.workflow_id,
            metadata={**context.metadata, "invocation_id": context.invocation_id},
            causation_id=context.invocation_id,
            trace_id=(
                context.trace_id
                or context.metadata.get("trace_id")
            ),
        )
        if self._execution_supervisor is None:
            result = await self._agent_runtime.execute(execution_context)
        else:
            result = await self._execution_supervisor.run(
                execution_context,
                lambda: self._agent_runtime.execute(execution_context),
            )
        if result.error_code:
            error = RuntimeError(result.error_message or result.error_code)
            error.code = result.error_code
            raise error
        return result.model_dump(mode="json")
