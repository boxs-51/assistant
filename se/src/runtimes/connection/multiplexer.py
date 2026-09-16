import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional


class RemoteConnectionLost(ConnectionError):
    """A pending remote invocation lost its immutable transport binding."""

    code = "REMOTE_CONNECTION_LOST"
    retryable = False

    def __init__(self, connection_id: str, invocation_id: str) -> None:
        self.connection_id = connection_id
        self.invocation_id = invocation_id
        super().__init__(
            f"Remote connection '{connection_id}' lost while invocation "
            f"'{invocation_id}' was pending."
        )


@dataclass
class _PendingInvocation:
    connection_id: Optional[str]
    future: asyncio.Future[Any]


class ConnectionMultiplexer:
    """Correlation boundary for remote capability invocations.

    This foundation object only manages pending invocation futures. Transport
    send/receive wiring is intentionally added in the realtime implementation
    slice so Phase 6.0 does not alter the existing ConnectionRuntime.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, _PendingInvocation] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        invocation_id: str,
        connection_id: Optional[str] = None,
    ) -> asyncio.Future[Any]:
        async with self._lock:
            if invocation_id in self._pending:
                raise ValueError(
                    f"Invocation '{invocation_id}' is already registered."
                )
            future = asyncio.get_running_loop().create_future()
            self._pending[invocation_id] = _PendingInvocation(
                connection_id=connection_id,
                future=future,
            )
            return future

    async def resolve(
        self,
        invocation_id: str,
        result: Any,
        connection_id: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            pending = self._pending.get(invocation_id)
            if pending is not None and (
                connection_id is not None
                and pending.connection_id != connection_id
            ):
                return False
            pending = self._pending.pop(invocation_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(result)
        return True

    async def reject(
        self,
        invocation_id: str,
        error: BaseException,
        connection_id: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            pending = self._pending.get(invocation_id)
            if pending is not None and (
                connection_id is not None
                and pending.connection_id != connection_id
            ):
                return False
            pending = self._pending.pop(invocation_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_exception(error)
        return True

    async def cancel(
        self,
        invocation_id: str,
        connection_id: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            pending = self._pending.get(invocation_id)
            if pending is not None and (
                connection_id is not None
                and pending.connection_id != connection_id
            ):
                return False
            pending = self._pending.pop(invocation_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.cancel()
        return True

    async def fail_connection(
        self,
        connection_id: str,
        error: BaseException | None = None,
    ) -> int:
        async with self._lock:
            matching_ids = [
                invocation_id
                for invocation_id, pending in self._pending.items()
                if pending.connection_id == connection_id
            ]
            pending = [
                self._pending.pop(invocation_id)
                for invocation_id in matching_ids
            ]

        count = 0
        for invocation_id, item in zip(matching_ids, pending):
            if not item.future.done():
                failure = error or RemoteConnectionLost(
                    connection_id,
                    invocation_id,
                )
                item.future.set_exception(failure)
                count += 1
        return count

    async def fail_all(self, error: BaseException) -> int:
        async with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()

        count = 0
        for item in pending:
            if not item.future.done():
                item.future.set_exception(error)
                count += 1
        return count

    async def pending_count(
        self,
        connection_id: Optional[str] = None,
    ) -> int:
        async with self._lock:
            if connection_id is None:
                return len(self._pending)
            return sum(
                1
                for pending in self._pending.values()
                if pending.connection_id == connection_id
            )

    async def connection_for_invocation(
        self,
        invocation_id: str,
    ) -> Optional[str]:
        async with self._lock:
            pending = self._pending.get(invocation_id)
            return None if pending is None else pending.connection_id
