from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from .contracts.invocation import (
    TERMINAL_INVOCATION_STATES,
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationEvent,
    CapabilityInvocationState,
    CapabilityWaitReason,
)


class InvalidInvocationTransition(ValueError):
    pass


_TRANSITIONS = {
    CapabilityInvocationState.CREATED: {
        CapabilityInvocationState.DISPATCHING,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
        CapabilityInvocationState.FAILED,
    },
    CapabilityInvocationState.DISPATCHING: {
        CapabilityInvocationState.RUNNING,
        CapabilityInvocationState.WAITING,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    },
    CapabilityInvocationState.RUNNING: {
        CapabilityInvocationState.COMPLETED,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
        CapabilityInvocationState.RETRYING,
        CapabilityInvocationState.WAITING,
    },
    CapabilityInvocationState.WAITING: {
        CapabilityInvocationState.DISPATCHING,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    },
    CapabilityInvocationState.RETRYING: {
        CapabilityInvocationState.DISPATCHING,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    },
    CapabilityInvocationState.COMPLETED: set(),
    CapabilityInvocationState.FAILED: set(),
    CapabilityInvocationState.CANCELLED: set(),
    CapabilityInvocationState.TIMED_OUT: set(),
}


def transition_invocation(
    invocation: CapabilityInvocation,
    state: CapabilityInvocationState,
    *,
    wait_reason: CapabilityWaitReason | None = None,
    output: Any = None,
    error: dict[str, Any] | None = None,
) -> tuple[CapabilityInvocationState, CapabilityInvocation]:
    previous = invocation.state
    if state not in _TRANSITIONS[previous]:
        raise InvalidInvocationTransition(
            f"Invalid invocation transition: {previous.value} -> {state.value}"
        )
    if state is CapabilityInvocationState.WAITING and wait_reason is None:
        raise InvalidInvocationTransition("WAITING requires wait_reason")
    if state is not CapabilityInvocationState.WAITING and wait_reason is not None:
        raise InvalidInvocationTransition("wait_reason is only valid for WAITING")

    now = datetime.now(timezone.utc)
    invocation.state = state
    invocation.wait_reason = wait_reason
    invocation.updated_at = now
    invocation.revision += 1
    if state is CapabilityInvocationState.RUNNING and invocation.started_at is None:
        invocation.started_at = now
    if state in TERMINAL_INVOCATION_STATES:
        invocation.completed_at = now
        invocation.output = output
        invocation.error = error
    return previous, invocation


class CapabilityInvocationStore(Protocol):
    async def create(self, invocation: CapabilityInvocation) -> None: ...
    async def compare_and_set(
        self, invocation: CapabilityInvocation, expected_revision: int
    ) -> bool: ...


class InMemoryCapabilityInvocationStore:
    def __init__(self) -> None:
        self.items: dict[str, CapabilityInvocation] = {}
        self.attempts: dict[str, CapabilityInvocationAttempt] = {}

    async def create(self, invocation: CapabilityInvocation) -> None:
        if invocation.invocation_id in self.items:
            raise ValueError(f"Duplicate invocation_id: {invocation.invocation_id}")
        self.items[invocation.invocation_id] = invocation.model_copy(deep=True)

    async def compare_and_set(
        self, invocation: CapabilityInvocation, expected_revision: int
    ) -> bool:
        current = self.items.get(invocation.invocation_id)
        if current is None or current.revision != expected_revision:
            return False
        self.items[invocation.invocation_id] = invocation.model_copy(deep=True)
        return True

    async def save_attempt(self, attempt: CapabilityInvocationAttempt) -> None:
        if attempt.attempt_id in self.attempts:
            raise ValueError(f"Duplicate attempt_id: {attempt.attempt_id}")
        self.attempts[attempt.attempt_id] = attempt.model_copy(deep=True)

    async def update_attempt(self, attempt: CapabilityInvocationAttempt) -> None:
        if attempt.attempt_id not in self.attempts:
            raise KeyError(attempt.attempt_id)
        self.attempts[attempt.attempt_id] = attempt.model_copy(deep=True)


class CapabilityInvocationLifecycle:
    _EVENTS = {
        CapabilityInvocationState.CREATED: "capability.invocation.created",
        CapabilityInvocationState.DISPATCHING: "capability.invocation.dispatched",
        CapabilityInvocationState.RUNNING: "capability.invocation.started",
        CapabilityInvocationState.WAITING: "capability.invocation.waiting",
        CapabilityInvocationState.RETRYING: "capability.invocation.retrying",
        CapabilityInvocationState.COMPLETED: "capability.invocation.completed",
        CapabilityInvocationState.FAILED: "capability.invocation.failed",
        CapabilityInvocationState.CANCELLED: "capability.invocation.cancelled",
        CapabilityInvocationState.TIMED_OUT: "capability.invocation.timed_out",
    }

    def __init__(self, store=None, publisher=None) -> None:
        self.store = store or InMemoryCapabilityInvocationStore()
        self.publisher = publisher

    async def create(self, invocation: CapabilityInvocation) -> CapabilityInvocation:
        await self.store.create(invocation)
        await self._publish(invocation, previous=None)
        return invocation

    async def transition(self, invocation, state, *, attempt_id=None, **changes):
        expected_revision = invocation.revision
        previous, invocation = transition_invocation(invocation, state, **changes)
        if not await self.store.compare_and_set(invocation, expected_revision):
            raise RuntimeError(
                f"Concurrent invocation update rejected: {invocation.invocation_id}"
            )
        await self._publish(invocation, previous=previous, attempt_id=attempt_id)
        return invocation

    async def start_attempt(
        self,
        invocation: CapabilityInvocation,
        *,
        implementation_id: str,
        driver_kind: str,
        connection_id: str | None,
    ) -> CapabilityInvocationAttempt:
        attempt = CapabilityInvocationAttempt(
            attempt_id=f"att_{uuid.uuid4().hex}",
            invocation_id=invocation.invocation_id,
            attempt_number=invocation.attempt,
            implementation_id=implementation_id,
            driver_kind=driver_kind,
            connection_id=connection_id,
            state=CapabilityInvocationState.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        await self.store.save_attempt(attempt)
        return attempt

    async def finish_attempt(
        self,
        attempt: CapabilityInvocationAttempt,
        state: CapabilityInvocationState,
        *,
        error: dict[str, Any] | None = None,
    ) -> None:
        if state not in TERMINAL_INVOCATION_STATES:
            raise InvalidInvocationTransition("Attempt completion must be terminal")
        attempt.state = state
        attempt.error = error
        attempt.completed_at = datetime.now(timezone.utc)
        await self.store.update_attempt(attempt)

    async def _publish(self, invocation, *, previous, attempt_id=None):
        if self.publisher is None:
            return
        event = CapabilityInvocationEvent(
            event_id=f"capevt_{uuid.uuid4().hex}",
            event_name=self._EVENTS[invocation.state],
            invocation_id=invocation.invocation_id,
            attempt_id=attempt_id,
            capability_id=invocation.capability_id,
            implementation_id=invocation.implementation_id,
            session_id=invocation.session_id,
            turn_id=invocation.turn_id,
            execution_id=invocation.execution_id,
            previous_state=previous,
            state=invocation.state,
            correlation_id=invocation.correlation_id,
            trace_id=invocation.trace_id,
        )
        await self.publisher(event)
