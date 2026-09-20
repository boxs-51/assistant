from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from ....domain.schemas.agent import AgentDefinition
from ....domain.schemas.agent_execution import AgentExecutionLimits
from ....domain.schemas.identity import Identity
from .clock import ExecutionClock, SystemExecutionClock
from .inference import InferenceUsage


class UnknownActiveBudgetError(RuntimeError):
    """Active execution budget is unknown and must not be regenerated."""


class _UnsetActiveBudget:
    pass


_UNSET_ACTIVE_BUDGET = _UnsetActiveBudget()


def _normalize_active_budget(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(
            "remaining_active_budget_seconds must be a finite non-negative value"
        )
    return normalized


@dataclass(slots=True)
class AgentExecutionContext:
    """Request scope for one agent execution.

    R4-A2 keeps the legacy ``deadline`` attribute as the process-local active
    monotonic deadline. Durable state stores a remaining duration instead.
    """

    execution_id: str
    agent_id: str
    session_id: str
    correlation_id: str
    identity: Identity
    limits: AgentExecutionLimits

    request_id: str | None = None
    task_id: str | None = None
    branch_id: str | None = None
    parent_execution_id: str | None = None
    retry_of_execution_id: str | None = None
    base_execution_id: str | None = None
    base_checkpoint_id: str | None = None
    workflow_id: str | None = None
    connection_id: str | None = None
    agent: AgentDefinition | None = None
    input: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    clock: ExecutionClock = field(
        default_factory=SystemExecutionClock,
        repr=False,
        compare=False,
    )
    started_monotonic: float = 0.0
    deadline: float | None = None
    remaining_active_budget_seconds: float | None = None
    wait_expires_at: datetime | None = None
    iteration: int = 0
    tool_calls_used: int = 0
    retry_attempts_used: int = 0
    usage: InferenceUsage = field(default_factory=InferenceUsage)
    causation_id: str | None = None
    trace_id: str | None = None
    resume_transcript: list[Dict[str, Any]] = field(default_factory=list)
    resume_pending_tool_calls: list[Dict[str, Any]] = field(default_factory=list)
    resume_revision: int | None = None
    cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)
    _tool_budget_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _retry_budget_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )

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
        task_id: str | None = None,
        branch_id: str | None = None,
        parent_execution_id: str | None = None,
        retry_of_execution_id: str | None = None,
        base_execution_id: str | None = None,
        base_checkpoint_id: str | None = None,
        workflow_id: str | None = None,
        connection_id: str | None = None,
        agent: AgentDefinition | None = None,
        input: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        causation_id: str | None = None,
        trace_id: str | None = None,
        remaining_active_budget_seconds: (
            float | None | _UnsetActiveBudget
        ) = _UNSET_ACTIVE_BUDGET,
        clock: ExecutionClock | None = None,
        wait_expires_at: datetime | None = None,
        activate_budget: bool = True,
        now_monotonic: float | None = None,
    ) -> "AgentExecutionContext":
        execution_clock = clock or SystemExecutionClock()
        started = (
            execution_clock.monotonic()
            if now_monotonic is None
            else float(now_monotonic)
        )

        if remaining_active_budget_seconds is _UNSET_ACTIVE_BUDGET:
            remaining_budget = _normalize_active_budget(
                limits.timeout_seconds
            )
        elif remaining_active_budget_seconds is None:
            # Explicit None means legacy/unknown durable provenance.  Do not
            # silently mint a fresh configured timeout.
            remaining_budget = None
        else:
            remaining_budget = _normalize_active_budget(
                remaining_active_budget_seconds
            )

        active_deadline = (
            None
            if remaining_budget is None or not activate_budget
            else started + remaining_budget
        )

        return cls(
            execution_id=execution_id,
            agent_id=agent_id,
            session_id=session_id,
            correlation_id=correlation_id,
            identity=identity,
            limits=limits,
            request_id=request_id,
            task_id=task_id,
            branch_id=branch_id,
            parent_execution_id=parent_execution_id,
            retry_of_execution_id=retry_of_execution_id,
            base_execution_id=base_execution_id,
            base_checkpoint_id=base_checkpoint_id,
            workflow_id=workflow_id,
            connection_id=connection_id,
            agent=agent,
            input=dict(input or {}),
            metadata=dict(metadata or {}),
            clock=execution_clock,
            started_monotonic=started,
            deadline=active_deadline,
            remaining_active_budget_seconds=remaining_budget,
            wait_expires_at=wait_expires_at,
            causation_id=causation_id,
            trace_id=trace_id,
        )

    @property
    def active_deadline_monotonic(self) -> float | None:
        """Explicit R4 name for the legacy process-local ``deadline``."""
        return self.deadline

    @active_deadline_monotonic.setter
    def active_deadline_monotonic(self, value: float | None) -> None:
        self.deadline = value

    @property
    def active_budget_running(self) -> bool:
        return self.deadline is not None

    @property
    def remaining_active_seconds(self) -> float | None:
        """Return live remaining budget, or the frozen durable duration."""
        if self.deadline is not None:
            return max(0.0, self.deadline - self.clock.monotonic())
        return self.remaining_active_budget_seconds

    @property
    def remaining_seconds(self) -> float:
        """Backward-compatible execution remaining-time facade.

        Unknown legacy budget fails closed as zero instead of becoming
        unbounded or regenerating ``limits.timeout_seconds``.
        """
        remaining = self.remaining_active_seconds
        return 0.0 if remaining is None else remaining

    @property
    def timed_out(self) -> bool:
        return self.remaining_seconds <= 0.0

    @property
    def cancelled(self) -> bool:
        return self.cancellation_event.is_set()

    def cancel(self) -> None:
        self.cancellation_event.set()

    def freeze_active_budget(self) -> float | None:
        """Freeze active elapsed-time consumption into a durable duration."""
        remaining = self.remaining_active_seconds
        self.remaining_active_budget_seconds = remaining
        self.deadline = None
        return remaining

    def restore_active_budget(
        self,
        remaining_active_budget_seconds: (
            float | None | _UnsetActiveBudget
        ) = _UNSET_ACTIVE_BUDGET,
    ) -> float:
        """Start an active monotonic deadline from a known remaining duration."""
        if remaining_active_budget_seconds is not _UNSET_ACTIVE_BUDGET:
            if remaining_active_budget_seconds is None:
                self.remaining_active_budget_seconds = None
            else:
                self.remaining_active_budget_seconds = _normalize_active_budget(
                    remaining_active_budget_seconds
                )

        remaining = self.remaining_active_budget_seconds
        if remaining is None:
            self.deadline = None
            raise UnknownActiveBudgetError(
                "Cannot restore Agent execution with unknown active budget."
            )

        self.deadline = self.clock.monotonic() + remaining
        return remaining

    def next_iteration(self) -> int:
        self.iteration += 1
        return self.iteration

    def record_tool_calls(self, count: int = 1) -> int:
        if count < 0:
            raise ValueError("tool call count increment must be non-negative")
        self.tool_calls_used += count
        return self.tool_calls_used

    async def reserve_tool_call(self) -> bool:
        """Atomically reserve one tool-call budget slot before execution."""
        async with self._tool_budget_lock:
            if self.tool_calls_used >= self.limits.max_tool_calls:
                return False
            self.tool_calls_used += 1
            return True

    async def reserve_retry_attempt(self) -> bool:
        """Atomically reserve one execution-scoped retry attempt.

        The initial tool execution is not counted here. Only additional
        attempts caused by retry policy consume this budget.
        """
        async with self._retry_budget_lock:
            if self.retry_attempts_used >= self.limits.max_retry_attempts:
                return False
            self.retry_attempts_used += 1
            return True

    def ensure_active(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError
        if self.timed_out:
            raise TimeoutError("Agent execution deadline exceeded.")

    def remaining_for(self, timeout_seconds: float | None) -> float:
        remaining = self.remaining_seconds
        if timeout_seconds is None:
            return remaining
        return max(0.0, min(remaining, float(timeout_seconds)))

    def record_usage(self, **values: int | float) -> None:
        data = self.usage.model_dump()
        for key, value in values.items():
            data[key] = data.get(key, 0) + value
        self.usage = InferenceUsage.model_validate(data)