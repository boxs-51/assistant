import asyncio
from typing import Any, Dict


class ConnectionMultiplexer:
    """Correlation boundary for remote capability invocations.

    This foundation object only manages pending invocation futures. Transport
    send/receive wiring is intentionally added in the realtime implementation
    slice so Phase 6.0 does not alter the existing ConnectionRuntime.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def register(self, invocation_id: str) -> asyncio.Future[Any]:
        async with self._lock:
            if invocation_id in self._pending:
                raise ValueError(
                    f"Invocation '{invocation_id}' is already registered."
                )
            future = asyncio.get_running_loop().create_future()
            self._pending[invocation_id] = future
            return future

    async def resolve(self, invocation_id: str, result: Any) -> bool:
        async with self._lock:
            future = self._pending.pop(invocation_id, None)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True

    async def reject(self, invocation_id: str, error: BaseException) -> bool:
        async with self._lock:
            future = self._pending.pop(invocation_id, None)
        if future is None or future.done():
            return False
        future.set_exception(error)
        return True

    async def cancel(self, invocation_id: str) -> bool:
        async with self._lock:
            future = self._pending.pop(invocation_id, None)
        if future is None or future.done():
            return False
        future.cancel()
        return True

    async def fail_all(self, error: BaseException) -> int:
        async with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()

        count = 0
        for future in pending:
            if not future.done():
                future.set_exception(error)
                count += 1
        return count

    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._pending)
