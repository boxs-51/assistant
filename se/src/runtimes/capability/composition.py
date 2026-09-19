from __future__ import annotations

import re
from typing import Any, Mapping

from ...domain.schemas.workflow import WorkflowDefinition
from ...domain.schemas.identity import Identity
from .contracts.context import CapabilityExecutionContext
from .contracts.definition import CapabilityDefinition
from .drivers.base import BaseCapabilityDriver


_PLACEHOLDER = re.compile(r"^\{\{([^{}]+)\}\}$")


class DeclarativeWorkflowDriver(BaseCapabilityDriver):
    """Compose capability invocations from a declarative workflow definition."""

    def __init__(
        self,
        definition: CapabilityDefinition,
        workflow: WorkflowDefinition,
        capability_runtime: Any,
    ) -> None:
        super().__init__(definition)
        self._workflow = workflow
        self._capability_runtime = capability_runtime
        step_ids = [step.step_id for step in workflow.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Workflow step_id values must be unique")

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        initial_input = dict(arguments)
        step_outputs: dict[str, Any] = {}

        for step in self._workflow.steps:
            resolved_arguments = self._resolve(
                step.arguments,
                initial_input=initial_input,
                step_outputs=step_outputs,
            )
            result = await self._capability_runtime.execute_capability(
                capability_id=step.tool_name,
                arguments=resolved_arguments,
                identity=context.identity,
                execution_id=context.execution_id,
                request_id=context.request_id,
                session_id=context.session_id,
                task_id=context.task_id,
                branch_id=context.branch_id,
                correlation_id=context.correlation_id,
                trace_id=context.trace_id,
                workflow_id=context.workflow_id or self.name,
                timeout_seconds=context.remaining_seconds,
                cancellation_event=context.cancellation_event,
                metadata=context.metadata,
            )
            step_outputs[step.step_id] = result.output

        last_output = (
            step_outputs[self._workflow.steps[-1].step_id]
            if self._workflow.steps
            else None
        )
        return self._render_output(
            self._workflow.output_template,
            initial_input=initial_input,
            step_outputs=step_outputs,
            last_output=last_output,
        )

    @staticmethod
    def _resolve(
        value: Any,
        *,
        initial_input: Mapping[str, Any],
        step_outputs: Mapping[str, Any],
    ) -> Any:
        if isinstance(value, Mapping):
            return {
                key: DeclarativeWorkflowDriver._resolve(
                    item,
                    initial_input=initial_input,
                    step_outputs=step_outputs,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                DeclarativeWorkflowDriver._resolve(
                    item,
                    initial_input=initial_input,
                    step_outputs=step_outputs,
                )
                for item in value
            ]
        if not isinstance(value, str):
            return value

        match = _PLACEHOLDER.match(value)
        if match:
            return DeclarativeWorkflowDriver._lookup(
                match.group(1), initial_input, step_outputs
            )
        return value

    @staticmethod
    def _lookup(
        expression: str,
        initial_input: Mapping[str, Any],
        step_outputs: Mapping[str, Any],
    ) -> Any:
        parts = expression.split(".")
        if parts[0] == "initial_input":
            current: Any = initial_input
            parts = parts[1:]
        elif parts[0] == "steps":
            if len(parts) < 2:
                raise ValueError(f"Invalid workflow reference: {expression}")
            if parts[1] == "last":
                current = (
                    next(reversed(step_outputs.values()))
                    if step_outputs
                    else None
                )
            else:
                if parts[1] not in step_outputs:
                    raise KeyError(f"Unknown workflow step: {parts[1]}")
                current = step_outputs[parts[1]]
            parts = parts[2:]
        else:
            raise ValueError(f"Invalid workflow reference: {expression}")

        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                raise KeyError(f"Missing workflow value: {expression}")
            current = current[part]
        return current

    @classmethod
    def _render_output(
        cls,
        template: str,
        *,
        initial_input: Mapping[str, Any],
        step_outputs: Mapping[str, Any],
        last_output: Any,
    ) -> Any:
        if template == "{{steps.last.output}}":
            return last_output
        return cls._resolve(
            template,
            initial_input=initial_input,
            step_outputs=step_outputs,
        )