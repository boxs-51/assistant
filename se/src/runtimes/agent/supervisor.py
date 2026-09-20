from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from .contracts.context import AgentExecutionContext
from .contracts.result import AgentExecutionResult


class AgentExecutionOwnershipError(RuntimeError):
    """Process-local Agent execution ownership contract violation."""


class AgentExecutionSupervisorClosedError(AgentExecutionOwnershipError):
    """Raised when new work is submitted after supervisor shutdown begins."""


@dataclass(frozen=True, slots=True)
class ExecutionOwnershipToken:
    """Opaque, process-local reservation token.

    This token is intentionally not durable and must never be confused with a
    distributed execution lease or resume claim.
    """

    execution_id: str
    token: str


@dataclass(slots=True)
class AgentExecutionHandle:
    execution_id: str
    task_id: str | None
    parent_execution_id: str | None
    cancellation_event: asyncio.Event
    task: asyncio.Task[AgentExecutionResult]


@dataclass(frozen=True, slots=True)
class _ExecutionReservation:
    token: ExecutionOwnershipToken
    task_id: str | None
    parent_execution_id: str | None
    cancellation_event: asyncio.Event


class AgentExecutionSupervisor:
    """Own all process-local tasks running ``AgentRuntime.execute``.

    Durable AgentExecution state remains owned by AgentRuntime.  This class
    only provides process-local duplicate exclusion, cancellation fan-out and
    task draining.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._handles: dict[str, AgentExecutionHandle] = {}
        self._reservations: dict[str, _ExecutionReservation] = {}
        self._reserved_by_execution: dict[str, str] = {}
        self._children: dict[str, set[str]] = {}
        self._task_index: dict[str, set[str]] = {}
        self._closing = False

    @property
    def closing(self) -> bool:
        return self._closing

    def is_running(self, execution_id: str) -> bool:
        handle = self._handles.get(execution_id)
        return bool(handle is not None and not handle.task.done())

    def active_execution_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                execution_id
                for execution_id, handle in self._handles.items()
                if not handle.task.done()
            )
        )

    async def quiesce(self) -> None:
        """Reject new reservations; already-admitted reservations may start."""
        async with self._lock:
            self._closing = True

    async def reserve(
        self,
        context: AgentExecutionContext,
    ) -> ExecutionOwnershipToken:
        """Reserve one execution ID without starting AgentRuntime.

        Resume paths use this to close the process-local duplicate race before
        performing their durable WAITING -> RUNNING claim.
        """
        async with self._lock:
            self._ensure_open_locked()
            self._ensure_execution_available_locked(context.execution_id)
            self._ensure_unique_cancellation_event_locked(
                context.cancellation_event
            )

            token = ExecutionOwnershipToken(
                execution_id=context.execution_id,
                token=uuid.uuid4().hex,
            )
            reservation = _ExecutionReservation(
                token=token,
                task_id=context.task_id,
                parent_execution_id=context.parent_execution_id,
                cancellation_event=context.cancellation_event,
            )
            self._reservations[token.token] = reservation
            self._reserved_by_execution[context.execution_id] = token.token
            return token

    async def release_reserved(
        self,
        token: ExecutionOwnershipToken,
    ) -> bool:
        """Release a reservation that has not been started.

        The operation is idempotent.  It never cancels an already-started
        execution if the token has already been consumed.
        """
        async with self._lock:
            reservation = self._reservations.get(token.token)
            if reservation is None or reservation.token != token:
                return False
            self._drop_reservation_locked(reservation)
            return True

    async def start_reserved(
        self,
        token: ExecutionOwnershipToken,
        context: AgentExecutionContext,
        runner: Callable[[], Awaitable[AgentExecutionResult]],
    ) -> asyncio.Task[AgentExecutionResult]:
        """Consume one reservation and start its owned runtime task."""
        async with self._lock:
            reservation = self._reservations.get(token.token)
            if reservation is None or reservation.token != token:
                raise AgentExecutionOwnershipError(
                    "Execution ownership reservation is missing or stale."
                )
            if token.execution_id != context.execution_id:
                raise AgentExecutionOwnershipError(
                    "Execution ownership token does not match context execution_id."
                )
            if (
                reservation.task_id != context.task_id
                or reservation.parent_execution_id
                != context.parent_execution_id
                or reservation.cancellation_event
                is not context.cancellation_event
            ):
                raise AgentExecutionOwnershipError(
                    "Reserved execution identity/cancellation scope changed before start."
                )
            if context.execution_id in self._handles:
                raise AgentExecutionOwnershipError(
                    f"Execution '{context.execution_id}' is already running."
                )

            task = asyncio.create_task(
                self._run_owned(context, runner),
                name=f"agent-execution:{context.execution_id}",
            )
            handle = AgentExecutionHandle(
                execution_id=context.execution_id,
                task_id=context.task_id,
                parent_execution_id=context.parent_execution_id,
                cancellation_event=context.cancellation_event,
                task=task,
            )
            self._handles[context.execution_id] = handle
            if context.task_id is not None:
                self._task_index.setdefault(context.task_id, set()).add(
                    context.execution_id
                )
            if context.parent_execution_id is not None:
                self._children.setdefault(
                    context.parent_execution_id,
                    set(),
                ).add(context.execution_id)

            self._drop_reservation_locked(reservation)
            return task

    async def run(
        self,
        context: AgentExecutionContext,
        runner: Callable[[], Awaitable[AgentExecutionResult]],
    ) -> AgentExecutionResult:
        """Reserve, start and await one Agent execution under explicit ownership."""
        token = await self.reserve(context)
        try:
            task = await self.start_reserved(token, context, runner)
        except BaseException:
            await self.release_reserved(token)
            raise

        try:
            # Shield keeps caller cancellation from implicitly abandoning the
            # owned runtime task.  The supervisor performs explicit cascade +
            # drain below instead.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.cancel_execution(context.execution_id, cascade=True)
            raise

    async def cancel_execution(
        self,
        execution_id: str,
        *,
        cascade: bool = True,
    ) -> None:
        """Cancel one live execution and, by default, its descendants."""
        tasks = await self._cancel_execution_set(
            {execution_id},
            cascade=cascade,
        )
        await self._drain(tasks)

    async def cancel_task(self, task_id: str) -> None:
        """Cancel all process-local execution scopes belonging to one Task."""
        if not task_id:
            raise ValueError("task_id must be non-empty")

        async with self._lock:
            roots = set(self._task_index.get(task_id, ()))
            reserved_ids = {
                reservation.token.execution_id
                for reservation in self._reservations.values()
                if reservation.task_id == task_id
            }
            roots.update(reserved_ids)
            execution_ids = self._expand_descendants_locked(roots)

            self._drop_reservations_for_executions_locked(execution_ids)
            tasks = self._signal_and_cancel_locked(execution_ids)

        await self._drain(tasks)

    async def shutdown(self) -> None:
        """Reject new work, cancel every owned execution and drain all tasks."""
        async with self._lock:
            if self._closing and not self._handles and not self._reservations:
                return

            self._closing = True
            execution_ids = self._expand_descendants_locked(
                set(self._handles) | set(self._reserved_by_execution)
            )
            self._drop_reservations_for_executions_locked(execution_ids)
            tasks = self._signal_and_cancel_locked(execution_ids)

        await self._drain(tasks)

        # All cancelled runner tasks remove their handles in _run_owned.finally.
        # A final locked sweep handles already-completed tasks and guarantees a
        # deterministic empty registry at shutdown return.
        async with self._lock:
            for execution_id, handle in list(self._handles.items()):
                if handle.task.done():
                    self._forget_handle_locked(execution_id, handle.task)
            self._reservations.clear()
            self._reserved_by_execution.clear()

    async def _run_owned(
        self,
        context: AgentExecutionContext,
        runner: Callable[[], Awaitable[AgentExecutionResult]],
    ) -> AgentExecutionResult:
        task = asyncio.current_task()
        if task is None:
            raise AgentExecutionOwnershipError(
                "Owned Agent execution must run inside an asyncio.Task."
            )
        try:
            return await runner()
        finally:
            async with self._lock:
                self._forget_handle_locked(context.execution_id, task)

    async def _cancel_execution_set(
        self,
        roots: set[str],
        *,
        cascade: bool,
    ) -> list[asyncio.Task[AgentExecutionResult]]:
        async with self._lock:
            execution_ids = (
                self._expand_descendants_locked(roots)
                if cascade
                else set(roots)
            )
            self._drop_reservations_for_executions_locked(execution_ids)
            return self._signal_and_cancel_locked(execution_ids)

    def _ensure_open_locked(self) -> None:
        if self._closing:
            raise AgentExecutionSupervisorClosedError(
                "AgentExecutionSupervisor is shutting down."
            )

    def _ensure_execution_available_locked(self, execution_id: str) -> None:
        handle = self._handles.get(execution_id)
        if handle is not None and not handle.task.done():
            raise AgentExecutionOwnershipError(
                f"Execution '{execution_id}' is already running."
            )
        if execution_id in self._reserved_by_execution:
            raise AgentExecutionOwnershipError(
                f"Execution '{execution_id}' is already reserved."
            )

    def _ensure_unique_cancellation_event_locked(
        self,
        cancellation_event: asyncio.Event,
    ) -> None:
        for handle in self._handles.values():
            if (
                not handle.task.done()
                and handle.cancellation_event is cancellation_event
            ):
                raise AgentExecutionOwnershipError(
                    "Cancellation event is already owned by another live execution."
                )
        for reservation in self._reservations.values():
            if reservation.cancellation_event is cancellation_event:
                raise AgentExecutionOwnershipError(
                    "Cancellation event is already owned by another reserved execution."
                )

    def _drop_reservation_locked(
        self,
        reservation: _ExecutionReservation,
    ) -> None:
        self._reservations.pop(reservation.token.token, None)
        current = self._reserved_by_execution.get(
            reservation.token.execution_id
        )
        if current == reservation.token.token:
            self._reserved_by_execution.pop(
                reservation.token.execution_id,
                None,
            )

    def _drop_reservations_for_executions_locked(
        self,
        execution_ids: set[str],
    ) -> None:
        for execution_id in tuple(execution_ids):
            token_value = self._reserved_by_execution.get(execution_id)
            if token_value is None:
                continue
            reservation = self._reservations.get(token_value)
            if reservation is not None:
                reservation.cancellation_event.set()
                self._drop_reservation_locked(reservation)

    def _expand_descendants_locked(
        self,
        roots: set[str],
    ) -> set[str]:
        expanded = set(roots)
        pending = list(roots)
        while pending:
            parent = pending.pop()
            children = set(self._children.get(parent, ()))
            children.update(
                reservation.token.execution_id
                for reservation in self._reservations.values()
                if reservation.parent_execution_id == parent
            )
            for child in children:
                if child not in expanded:
                    expanded.add(child)
                    pending.append(child)
        return expanded

    def _signal_and_cancel_locked(
        self,
        execution_ids: set[str],
    ) -> list[asyncio.Task[AgentExecutionResult]]:
        current = asyncio.current_task()
        tasks: list[asyncio.Task[AgentExecutionResult]] = []
        for execution_id in execution_ids:
            handle = self._handles.get(execution_id)
            if handle is None:
                continue
            handle.cancellation_event.set()
            if handle.task is current:
                # A task cannot await/drain itself.  The cancellation signal
                # makes the current execution observe cancellation at its next
                # explicit cancellation boundary.
                continue
            if not handle.task.done():
                handle.task.cancel()
            tasks.append(handle.task)
        return tasks

    async def _drain(
        self,
        tasks: list[asyncio.Task[AgentExecutionResult]],
    ) -> None:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _forget_handle_locked(
        self,
        execution_id: str,
        task: asyncio.Task,
    ) -> None:
        handle = self._handles.get(execution_id)
        if handle is None or handle.task is not task:
            return

        self._handles.pop(execution_id, None)
        if handle.task_id is not None:
            indexed = self._task_index.get(handle.task_id)
            if indexed is not None:
                indexed.discard(execution_id)
                if not indexed:
                    self._task_index.pop(handle.task_id, None)

        parent_execution_id = handle.parent_execution_id
        if parent_execution_id is not None:
            siblings = self._children.get(parent_execution_id)
            if siblings is not None:
                siblings.discard(execution_id)
                if not siblings:
                    self._children.pop(parent_execution_id, None)

        # Preserve a non-empty descendants set after a parent finishes; a
        # later explicit cancellation by that execution_id can still cascade
        # to live descendants.
        own_children = self._children.get(execution_id)
        if own_children is not None and not own_children:
            self._children.pop(execution_id, None)
