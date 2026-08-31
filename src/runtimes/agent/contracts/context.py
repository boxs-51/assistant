from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ....domain.schemas.agent import AgentDefinition
from ....domain.schemas.agent_execution import AgentExecutionLimits
from ....domain.schemas.identity import Identity


@dataclass(slots=True)
class AgentExecutionContext:
    """Request scope for one agent execution."""

    execution_id: str
    agent_id: str
    session_id: str
    correlation_id: str
    identity: Identity
    limits: AgentExecutionLimits

    request_id: str | None = None
    parent_execution_id: str | None = None
    workflow_id: str | None = None
    agent: AgentDefinition | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    started_monotonic: float = field(default_factory=time.monotonic)
    deadline: float | None = None
    iteration: int = 0
    tool_calls_used: int = 0
    cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def create(
        cls,
        *,
        execution_id: str,
        agent_id: str,
        session_id: str,
        correlation_id: str,
        identity: Identity,
        limits: AgentExecutionLimits,
        request_id: str | None = None,
        parent_execution_id: str | None = None,
        workflow_id: str | None = None,
        agent: AgentDefinition | None = None,
        metadata: Optional[Dict[str, Any]] = None,
        now_monotonic: float | None = None,
    ) -> "AgentExecutionContext":
        started = time.monotonic() if now_monotonic is None else now_monotonic
        return cls(
            execution_id=execution_id,
            agent_id=agent_id,
            session_id=session_id,
            correlation_id=correlation_id,
            identity=identity,
            limits=limits,
            request_id=request_id,
            parent_execution_id=parent_execution_id,
            workflow_id=workflow_id,
            agent=agent,
            metadata=dict(metadata or {}),
            started_monotonic=started,
            deadline=started + limits.timeout_seconds,
        )

    @property
    def remaining_seconds(self) -> float:
        if self.deadline is None:
            return float("inf")
        return max(0.0, self.deadline - time.monotonic())

    @property
    def timed_out(self) -> bool:
        return self.remaining_seconds <= 0.0

    @property
    def cancelled(self) -> bool:
        return self.cancellation_event.is_set()

    def cancel(self) -> None:
        self.cancellation_event.set()

    def next_iteration(self) -> int:
        self.iteration += 1
        return self.iteration

    def record_tool_calls(self, count: int = 1) -> int:
        if count < 0:
            raise ValueError("tool call count increment must be non-negative")
        self.tool_calls_used += count
        return self.tool_calls_used
