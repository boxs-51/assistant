from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

from src.application.policy.authorization import AuthorizationService
from src.domain.schemas import (
    GatewayChoice,
    GatewayMessage,
    GatewayResponse,
    GatewayToolCall,
    GatewayToolResult,
    FunctionCall,
)
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver


@dataclass(frozen=True, slots=True)
class TraceEvent:
    name: str
    sequence: int
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)


class ExecutionTrace:
    """Deterministic execution trace used by agent tool-loop assertions."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def emit(self, event_name: str, **payload: Any) -> TraceEvent:
        event = TraceEvent(
            name=event_name,
            sequence=len(self._events) + 1,
            timestamp=time.monotonic(),
            payload=dict(payload),
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    @property
    def names(self) -> list[str]:
        return [event.name for event in self._events]

    def filter(self, name: str) -> list[TraceEvent]:
        return [event for event in self._events if event.name == name]

    def assert_names(self, expected: Sequence[str]) -> None:
        actual = self.names
        assert actual == list(expected), f"trace mismatch: {actual!r} != {list(expected)!r}"


class FakeLLM:
    """Scripted, deterministic LLM stand-in.

    Each script item can be:
      * a plain string for a final assistant answer
      * a single GatewayToolCall
      * a list of GatewayToolCall objects for parallel execution
      * a callable receiving the current transcript and tool definitions
    """

    def __init__(self, script: Iterable[Any], *, model: str = "fake-agent-model") -> None:
        self._script = list(script)
        self._index = 0
        self.model = model
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[CapabilityDefinition],
    ) -> GatewayResponse:
        if self._index >= len(self._script):
            raise AssertionError("FakeLLM script exhausted before the agent completed.")

        item = self._script[self._index]
        self._index += 1
        self.calls += 1
        self.requests.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": [tool.model_dump(mode="json") for tool in tools],
            }
        )

        if callable(item):
            item = item(messages, tools)
        if asyncio.iscoroutine(item):
            item = await item

        if isinstance(item, str):
            message = GatewayMessage(role="assistant", content=item)
        else:
            calls = item if isinstance(item, list) else [item]
            message = GatewayMessage(
                role="assistant",
                content="",
                tool_calls=[self._coerce_tool_call(call) for call in calls],
            )

        return GatewayResponse(
            id=f"fake-{self.calls}",
            model=self.model,
            provider="fake-llm",
            choices=[GatewayChoice(index=0, message=message, finish_reason=None)],
        )

    @staticmethod
    def _coerce_tool_call(value: Any) -> GatewayToolCall:
        if isinstance(value, GatewayToolCall):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(f"Unsupported fake tool call: {value!r}")
        arguments = value.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, sort_keys=True)
        return GatewayToolCall(
            id=str(value.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
            function=FunctionCall(
                name=str(value["name"]),
                arguments=arguments,
            ),
        )


class FakeTool:
    """Convenience fake tool backed by the real Python capability driver."""

    def __init__(
        self,
        runtime: CapabilityRuntime,
        *,
        name: str,
        handler: Callable[..., Any],
        description: str | None = None,
        input_schema: Mapping[str, Any] | None = None,
        require_auth: bool = False,
        required_scopes: Iterable[str] = (),
    ) -> None:
        self.definition = CapabilityDefinition(
            id=name,
            name=name,
            description=description or f"Fake tool {name}",
            input_schema=dict(input_schema or {"type": "object"}),
            require_auth=require_auth,
            required_scopes=list(required_scopes),
        )
        self.handler = handler
        self.calls = 0
        self.driver = PythonCapabilityDriver(self.definition, self._invoke)
        runtime.register_capability(self.driver)

    async def _invoke(self, **arguments: Any) -> Any:
        self.calls += 1
        value = self.handler(**arguments)
        if asyncio.iscoroutine(value):
            return await value
        return value


class AgentToolLoopHarness:
    """Small production-contract harness for deterministic Agent -> Tool loops.

    It intentionally composes the real CapabilityRuntime instead of inventing a
    second ToolRegistry/execution implementation in the test suite.
    """

    def __init__(
        self,
        *,
        llm: FakeLLM,
        capability_runtime: CapabilityRuntime,
        identity: Identity,
        trace: ExecutionTrace | None = None,
        limits: AgentExecutionLimits | None = None,
        retryable_errors: bool = True,
    ) -> None:
        self.llm = llm
        self.capability_runtime = capability_runtime
        self.identity = identity
        self.trace = trace or ExecutionTrace()
        self.limits = limits or AgentExecutionLimits()
        self.retryable_errors = retryable_errors

    async def run(self, prompt: str) -> str:
        transcript: list[dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]
        execution_id = f"agent-exec-{uuid.uuid4().hex}"
        tool_calls_total = 0

        self.trace.emit("agent.started", execution_id=execution_id, prompt=prompt)
        tools = await self.capability_runtime.get_available_capabilities(self.identity)
        self.trace.emit(
            "capabilities.available",
            execution_id=execution_id,
            capability_ids=[tool.capability_id for tool in tools],
        )

        for iteration in range(1, self.limits.max_iterations + 1):
            self.trace.emit("llm.requested", iteration=iteration)
            response = await self.llm.complete(transcript, tools)
            message = response.choices[0].message if response.choices else None
            if message is None:
                raise AssertionError("FakeLLM returned no assistant message.")

            if not message.tool_calls:
                final_text = self._extract_text(message.content)
                transcript.append({"role": "assistant", "content": final_text})
                self.trace.emit("agent.completed", execution_id=execution_id, output=final_text)
                return final_text

            self.trace.emit(
                "tool.calls.requested",
                iteration=iteration,
                count=len(message.tool_calls),
            )
            if tool_calls_total + len(message.tool_calls) > self.limits.max_tool_calls:
                self.trace.emit("agent.failed", code="MAX_TOOL_CALLS_EXCEEDED")
                raise RuntimeError("MAX_TOOL_CALLS_EXCEEDED")

            transcript.append(
                {
                    "role": "assistant",
                    "content": self._extract_text(message.content),
                    "tool_calls": [call.model_dump(mode="json") for call in message.tool_calls],
                }
            )

            results = await self._execute_tool_calls(
                message.tool_calls,
                execution_id=execution_id,
            )
            tool_calls_total += len(message.tool_calls)
            for result in results:
                transcript.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "name": result.name,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
                self.trace.emit(
                    "tool.result.appended",
                    tool_call_id=result.tool_call_id,
                    name=result.name,
                    is_error=result.is_error,
                )

        self.trace.emit("agent.failed", code="MAX_ITERATIONS_EXCEEDED")
        raise RuntimeError("MAX_ITERATIONS_EXCEEDED")

    async def _execute_tool_calls(
        self,
        tool_calls: Sequence[GatewayToolCall],
        *,
        execution_id: str,
    ) -> list[GatewayToolResult]:
        async def run_one(call: GatewayToolCall) -> GatewayToolResult:
            name = call.function.name
            self.trace.emit("tool.execution.started", tool_call_id=call.id, name=name)

            record = self.capability_runtime.registry.get(name)
            if record is None:
                error = f"Capability '{name}' not found or unavailable."
                self.trace.emit("tool.execution.failed", tool_call_id=call.id, name=name, code="CAPABILITY_NOT_FOUND")
                return GatewayToolResult(tool_call_id=call.id, name=name, content=error, is_error=True)

            try:
                arguments = self._parse_arguments(call.function.arguments)
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        result = await self.capability_runtime.execute_capability(
                            capability_id=name,
                            arguments=arguments,
                            identity=self.identity,
                            execution_id=execution_id,
                            request_id=self.identity.request_id,
                            session_id=self.identity.session_id,
                            metadata={"tool_call_id": call.id, "attempt": attempts},
                        )
                        self.trace.emit(
                            "tool.execution.completed",
                            tool_call_id=call.id,
                            name=name,
                            attempt=attempts,
                        )
                        return GatewayToolResult(
                            tool_call_id=call.id,
                            name=name,
                            content=self._serialize_tool_output(result.output),
                        )
                    except Exception as exc:
                        retryable = bool(getattr(exc, "retryable", False))
                        if self.retryable_errors and retryable and attempts < 3:
                            self.trace.emit(
                                "tool.execution.retrying",
                                tool_call_id=call.id,
                                name=name,
                                attempt=attempts,
                            )
                            continue
                        raise
            except Exception as exc:
                self.trace.emit(
                    "tool.execution.failed",
                    tool_call_id=call.id,
                    name=name,
                    error_type=type(exc).__name__,
                )
                return GatewayToolResult(
                    tool_call_id=call.id,
                    name=name,
                    content=str(exc),
                    is_error=True,
                )

        return list(await asyncio.gather(*(run_one(call) for call in tool_calls)))

    @staticmethod
    def _parse_arguments(value: str) -> dict[str, Any]:
        parsed = json.loads(value or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must decode to a JSON object.")
        return parsed

    @staticmethod
    def _serialize_tool_output(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str, sort_keys=True)

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            return "".join(parts)
        return str(content or "")


def make_identity(
    *,
    user_id: str = "agent-test-user",
    scopes: Iterable[str] = (),
    auth_type: str = "api_key",
) -> Identity:
    return Identity(
        user_id=user_id,
        auth_type=auth_type,
        scopes=set(scopes),
    )
