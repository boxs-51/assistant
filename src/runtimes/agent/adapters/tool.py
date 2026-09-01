from __future__ import annotations

import asyncio
import uuid
from typing import Any, Sequence

from jsonschema import SchemaError, ValidationError, validate

from ..contracts.context import AgentExecutionContext
from ..contracts.policy import (
    AgentExecutionPolicy,
    AgentToolPolicy,
    PolicyDecision,
)
from ..contracts.tool import (
    ToolExecutionPort,
    ToolExecutionRequest,
    ToolExecutionResult,
)


class CapabilityToolExecutionAdapter(ToolExecutionPort):
    """Canonical Agent tool gateway over CapabilityRuntime."""

    def __init__(
        self,
        capability_runtime: Any,
        tool_policy: AgentToolPolicy,
        execution_policy: AgentExecutionPolicy,
    ) -> None:
        self._capability_runtime = capability_runtime
        self._tool_policy = tool_policy
        self._execution_policy = execution_policy

    async def execute(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        if request.execution_id != context.execution_id:
            raise ValueError(
                "Tool request execution_id does not match execution context."
            )

        if (
            self._execution_policy.check_tool_call(context, request)
            is not PolicyDecision.ALLOW
        ):
            return self._denied(request, "AGENT_TOOL_POLICY_DENIED")

        if (
            self._tool_policy.is_visible(
                agent_id=context.agent_id,
                capability_id=request.capability_id,
            )
            is not True
        ):
            return self._denied(request, "AGENT_TOOL_NOT_VISIBLE")

        if (
            self._tool_policy.authorize(
                identity=context.identity,
                agent_id=context.agent_id,
                capability_id=request.capability_id,
            )
            is not PolicyDecision.ALLOW
        ):
            return self._denied(request, "CAPABILITY_UNAUTHORIZED")

        context.ensure_active()
        record = self._capability_runtime.registry.get(request.capability_id)
        if record is None or not record.executable:
            return self._denied(request, "CAPABILITY_NOT_FOUND")

        try:
            validate(
                instance=request.arguments,
                schema=record.definition.input_schema or {"type": "object"},
            )
        except ValidationError as exc:
            return self._failure(
                request,
                code="CAPABILITY_INVALID_ARGUMENT",
                message=str(exc.message),
                retryable=False,
            )
        except SchemaError as exc:
            return self._failure(
                request,
                code="CAPABILITY_SCHEMA_INVALID",
                message=str(exc),
                retryable=False,
            )

        timeout = context.remaining_for(
            getattr(context.limits, "tool_timeout_seconds", None)
        )
        if timeout <= 0:
            return self._failure(
                request,
                code="CAPABILITY_TIMEOUT",
                message="Agent execution deadline exceeded before tool start.",
                retryable=True,
            )

        try:
            result = await self._capability_runtime.execute_capability(
                capability_id=request.capability_id,
                arguments=request.arguments,
                identity=context.identity,
                execution_id=context.execution_id,
                request_id=context.request_id,
                session_id=context.session_id,
                workflow_id=context.workflow_id,
                timeout_seconds=timeout,
                cancellation_event=context.cancellation_event,
                metadata={
                    **context.metadata,
                    **request.metadata,
                    "tool_call_id": request.tool_call_id,
                    "invocation_id": request.invocation_id,
                },
            )
            context.record_tool_calls(1)
            context.record_usage(tool_invocations=1)
            return ToolExecutionResult(
                execution_id=context.execution_id,
                iteration=request.iteration,
                invocation_id=result.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=True,
                output=result.output,
                metadata=dict(result.metadata),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._failure(
                request,
                code=getattr(exc, "code", type(exc).__name__),
                message=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            )

    async def execute_many(
        self,
        context: AgentExecutionContext,
        requests: Sequence[ToolExecutionRequest],
        *,
        max_parallel: int,
    ) -> Sequence[ToolExecutionResult]:
        if max_parallel < 1:
            raise ValueError("max_parallel must be >= 1.")
        semaphore = asyncio.Semaphore(
            min(max_parallel, context.limits.max_parallel_tools)
        )

        async def run_one(request: ToolExecutionRequest) -> ToolExecutionResult:
            async with semaphore:
                return await self.execute(context, request)

        return list(
            await asyncio.gather(
                *(run_one(request) for request in requests)
            )
        )

    @staticmethod
    def new_request(
        *,
        context: AgentExecutionContext,
        iteration: int,
        tool_call_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ToolExecutionRequest:
        return ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=iteration,
            invocation_id=f"inv_{uuid.uuid4().hex}",
            tool_call_id=tool_call_id,
            capability_id=capability_id,
            arguments=dict(arguments),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _denied(
        request: ToolExecutionRequest,
        code: str,
    ) -> ToolExecutionResult:
        return CapabilityToolExecutionAdapter._failure(
            request, code=code, message=code, retryable=False
        )

    @staticmethod
    def _failure(
        request: ToolExecutionRequest,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            execution_id=request.execution_id,
            iteration=request.iteration,
            invocation_id=request.invocation_id,
            tool_call_id=request.tool_call_id,
            capability_id=request.capability_id,
            success=False,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )