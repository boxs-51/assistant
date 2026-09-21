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
    RemoteOutcomeState,
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
        CapabilityInvocationState.COMPLETED,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    },
    CapabilityInvocationState.RETRYING: {
        CapabilityInvocationState.DISPATCHING,
        CapabilityInvocationState.WAITING,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    },
    CapabilityInvocationState.COMPLETED: set(),
    CapabilityInvocationState.FAILED: set(),
    CapabilityInvocationState.CANCELLED: set(),
    CapabilityInvocationState.TIMED_OUT: set(),
}


_REMOTE_OUTCOME_TRANSITIONS = {
    None: {
        RemoteOutcomeState.NOT_DISPATCHED,
    },
    RemoteOutcomeState.NOT_DISPATCHED: {
        RemoteOutcomeState.IN_FLIGHT,
        RemoteOutcomeState.OUTCOME_UNKNOWN,
        RemoteOutcomeState.TERMINAL_COMMITTED,
    },
    RemoteOutcomeState.IN_FLIGHT: {
        # Transport may prove that an attempted dispatch never reached the
        # send boundary.  This correction is conservative and is the only
        # backwards certainty transition R6-B permits.
        RemoteOutcomeState.NOT_DISPATCHED,
        RemoteOutcomeState.OUTCOME_UNKNOWN,
        RemoteOutcomeState.TERMINAL_COMMITTED,
    },
    RemoteOutcomeState.OUTCOME_UNKNOWN: {
        RemoteOutcomeState.TERMINAL_COMMITTED,
    },
    RemoteOutcomeState.TERMINAL_COMMITTED: set(),
}


def _validate_remote_outcome_transition(
    current: RemoteOutcomeState | None,
    target: RemoteOutcomeState,
) -> None:
    if target is current:
        return
    if target not in _REMOTE_OUTCOME_TRANSITIONS[current]:
        current_name = current.value if current is not None else "NONE"
        raise InvalidInvocationTransition(
            "Invalid remote outcome transition: "
            f"{current_name} -> {target.value}"
        )


def transition_invocation(
    invocation: CapabilityInvocation,
    state: CapabilityInvocationState,
    *,
    wait_reason: CapabilityWaitReason | None = None,
    output: Any = None,
    error: dict[str, Any] | None = None,
    remote_outcome_state: RemoteOutcomeState | None = None,
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
    if (
        remote_outcome_state is not None
        and remote_outcome_state is not invocation.remote_outcome_state
    ):
        _validate_remote_outcome_transition(
            invocation.remote_outcome_state,
            remote_outcome_state,
        )

    now = datetime.now(timezone.utc)
    invocation.state = state
    invocation.wait_reason = wait_reason
    if remote_outcome_state is not None:
        invocation.remote_outcome_state = remote_outcome_state
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
    async def get(
        self, invocation_id: str
    ) -> CapabilityInvocation | None: ...
    async def compare_and_set(
        self, invocation: CapabilityInvocation, expected_revision: int
    ) -> bool: ...
    async def list_attempts(
        self, invocation_id: str
    ) -> list[CapabilityInvocationAttempt]: ...
    async def begin_continuation_attempt(
        self,
        invocation: CapabilityInvocation,
        expected_revision: int,
        attempt: CapabilityInvocationAttempt,
    ) -> bool: ...


class InMemoryCapabilityInvocationStore:
    def __init__(self) -> None:
        self.items: dict[str, CapabilityInvocation] = {}
        self.attempts: dict[str, CapabilityInvocationAttempt] = {}

    async def create(self, invocation: CapabilityInvocation) -> None:
        if invocation.invocation_id in self.items:
            raise ValueError(f"Duplicate invocation_id: {invocation.invocation_id}")
        self.items[invocation.invocation_id] = invocation.model_copy(deep=True)

    async def get(
        self, invocation_id: str
    ) -> CapabilityInvocation | None:
        invocation = self.items.get(invocation_id)
        return (
            invocation.model_copy(deep=True)
            if invocation is not None
            else None
        )

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

    async def begin_continuation_attempt(
        self,
        invocation: CapabilityInvocation,
        expected_revision: int,
        attempt: CapabilityInvocationAttempt,
    ) -> bool:
        current = self.items.get(invocation.invocation_id)
        if (
            current is None
            or current.revision != expected_revision
            or current.state is not CapabilityInvocationState.WAITING
        ):
            return False
        existing_numbers = [
            item.attempt_number
            for item in self.attempts.values()
            if item.invocation_id == invocation.invocation_id
        ]
        high_water = max(existing_numbers, default=0)
        if (
            current.attempt != high_water
            or attempt.attempt_number != current.attempt + 1
            or invocation.attempt != attempt.attempt_number
            or attempt.attempt_id in self.attempts
            or any(
                item.invocation_id == invocation.invocation_id
                and item.attempt_number == attempt.attempt_number
                for item in self.attempts.values()
            )
        ):
            return False
        self.items[invocation.invocation_id] = invocation.model_copy(deep=True)
        self.attempts[attempt.attempt_id] = attempt.model_copy(deep=True)
        return True

    async def update_attempt(self, attempt: CapabilityInvocationAttempt) -> None:
        if attempt.attempt_id not in self.attempts:
            raise KeyError(attempt.attempt_id)
        self.attempts[attempt.attempt_id] = attempt.model_copy(deep=True)

    async def list_attempts(
        self, invocation_id: str
    ) -> list[CapabilityInvocationAttempt]:
        return [
            item.model_copy(deep=True)
            for item in sorted(
                (
                    attempt
                    for attempt in self.attempts.values()
                    if attempt.invocation_id == invocation_id
                ),
                key=lambda attempt: attempt.attempt_number,
            )
        ]


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

    async def update_remote_outcome(
        self,
        invocation: CapabilityInvocation,
        state: RemoteOutcomeState,
    ) -> CapabilityInvocation:
        """CAS one certainty-only transition without changing lifecycle state."""
        if invocation.remote_outcome_state is state:
            return invocation
        _validate_remote_outcome_transition(
            invocation.remote_outcome_state,
            state,
        )
        expected_revision = invocation.revision
        invocation.remote_outcome_state = state
        invocation.updated_at = datetime.now(timezone.utc)
        invocation.revision += 1
        if not await self.store.compare_and_set(
            invocation,
            expected_revision,
        ):
            raise RuntimeError(
                "Concurrent invocation remote-outcome update rejected: "
                f"{invocation.invocation_id}"
            )
        return invocation

    async def begin_continuation_attempt(
        self,
        invocation: CapabilityInvocation,
        *,
        implementation_id: str,
        driver_kind: str,
        connection_id: str | None,
        continuation_mode: str,
    ) -> tuple[CapabilityInvocation, CapabilityInvocationAttempt]:
        if invocation.state is not CapabilityInvocationState.WAITING:
            raise InvalidInvocationTransition(
                "Existing invocation continuation requires WAITING state"
            )
        expected_revision = invocation.revision
        candidate = invocation.model_copy(deep=True)
        candidate.implementation_id = implementation_id
        candidate.driver_kind = driver_kind
        candidate.connection_id = connection_id
        candidate.attempt += 1
        candidate.max_attempts = max(candidate.max_attempts, candidate.attempt)
        previous, candidate = transition_invocation(
            candidate,
            CapabilityInvocationState.DISPATCHING,
        )
        attempt = CapabilityInvocationAttempt(
            attempt_id=f"att_{uuid.uuid4().hex}",
            invocation_id=candidate.invocation_id,
            attempt_number=candidate.attempt,
            implementation_id=implementation_id,
            driver_kind=driver_kind,
            connection_id=connection_id,
            state=CapabilityInvocationState.DISPATCHING,
            started_at=datetime.now(timezone.utc),
            metadata={
                "continuation_mode": continuation_mode,
                "source_revision": expected_revision,
                "source_remote_outcome_state": (
                    invocation.remote_outcome_state.value
                    if invocation.remote_outcome_state is not None
                    else None
                ),
            },
        )
        begin = getattr(self.store, "begin_continuation_attempt", None)
        if not callable(begin):
            raise RuntimeError(
                "Invocation store does not support atomic continuation attempts"
            )
        if not await begin(candidate, expected_revision, attempt):
            raise RuntimeError(
                "Concurrent invocation continuation rejected: "
                f"{candidate.invocation_id}"
            )
        await self._publish(
            candidate,
            previous=previous,
            attempt_id=attempt.attempt_id,
        )
        return candidate, attempt

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
