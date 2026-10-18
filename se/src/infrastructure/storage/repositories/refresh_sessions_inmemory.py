"""Single-process V2 refresh authority. No distributed security guarantee.

State and lifecycle are owned by this typed store, never by CacheDriver's
private _data/_lock; StorageEngine closes the store on generation teardown.
"""
import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..interfaces.refresh_sessions import RefreshLease, RefreshSessionStore


@dataclass
class _Record:
    user_id: str
    jti: str
    expires_at: float
    epoch: int = 0
    revoked: bool = False
    lease_id: str | None = None
    lease_deadline: float = 0.0


class InMemoryRefreshSessionStore(RefreshSessionStore):
    def __init__(
        self,
        *,
        is_generation_active: Callable[[], bool] | None = None,
        lease_seconds: float = 10.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._lock = asyncio.Lock()
        self._records: dict[str, _Record] = {}
        self._closed = False
        self._is_generation_active = is_generation_active or (lambda: True)
        self._lease_seconds = lease_seconds

    def _check_active(self) -> None:
        if self._closed or not self._is_generation_active():
            raise RuntimeError("Refresh-session store generation is unavailable")

    def _record(self, token_hash: str) -> _Record | None:
        record = self._records.get(token_hash)
        if record is not None and time.monotonic() >= record.expires_at:
            del self._records[token_hash]
            return None
        return record

    async def issue(self, token_hash: str, user_id: str, jti: str, ttl_seconds: int) -> bool:
        if not token_hash or not user_id or not jti or ttl_seconds <= 0:
            raise ValueError("Invalid refresh-session issuance parameters")
        async with self._lock:
            self._check_active()
            if self._record(token_hash) is not None:
                return False
            self._records[token_hash] = _Record(
                user_id=user_id,
                jti=jti,
                expires_at=time.monotonic() + ttl_seconds,
            )
            return True

    async def begin(self, token_hash: str, user_id: str, jti: str) -> RefreshLease | None:
        async with self._lock:
            self._check_active()
            record = self._record(token_hash)
            if record is None or record.revoked or (record.user_id, record.jti) != (user_id, jti):
                return None
            now = time.monotonic()
            if record.lease_id is not None and record.lease_deadline > now:
                return None
            lease_id = secrets.token_urlsafe(24)
            record.lease_id = lease_id
            record.lease_deadline = min(record.expires_at, now + self._lease_seconds)
            return RefreshLease(token_hash, user_id, jti, lease_id, record.epoch)

    async def finalize(self, lease: RefreshLease) -> bool:
        async with self._lock:
            self._check_active()
            record = self._record(lease.token_hash)
            if record is None or record.revoked:
                return False
            if (record.user_id, record.jti, record.epoch, record.lease_id) != (
                lease.user_id, lease.jti, lease.epoch, lease.lease_id,
            ) or time.monotonic() >= record.lease_deadline:
                return False
            record.lease_id = None
            record.lease_deadline = 0.0
            return True

    async def abort(self, lease: RefreshLease) -> None:
        async with self._lock:
            self._check_active()
            record = self._record(lease.token_hash)
            if record is not None and record.epoch == lease.epoch and record.lease_id == lease.lease_id:
                record.lease_id = None
                record.lease_deadline = 0.0

    async def revoke(self, token_hash: str, user_id: str, jti: str) -> bool:
        async with self._lock:
            self._check_active()
            record = self._record(token_hash)
            if record is None or record.revoked or (record.user_id, record.jti) != (user_id, jti):
                return False
            record.revoked = True
            record.epoch += 1
            record.lease_id = None
            record.lease_deadline = 0.0
            return True

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._records.clear()
