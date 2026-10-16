"""Typed R2 refresh-session protocol. Security claims concern backend ordering.

FINALIZE is the authorization linearization point, not HTTP delivery or
immediate revocation of pre-existing stateless access JWTs.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RefreshLease:
    token_hash: str
    user_id: str
    jti: str
    lease_id: str
    epoch: int


class RefreshSessionStore(ABC):
    @abstractmethod
    async def issue(self, token_hash: str, user_id: str, jti: str, ttl_seconds: int) -> bool:
        """Atomically persist a new ACTIVE session. Never overwrite a tombstone."""

    @abstractmethod
    async def begin(self, token_hash: str, user_id: str, jti: str) -> RefreshLease | None:
        """Acquire bounded fenced lease or return None when not authorized."""

    @abstractmethod
    async def finalize(self, lease: RefreshLease) -> bool:
        """Atomic last authorization step; reject REVOKED/stale lease."""

    @abstractmethod
    async def abort(self, lease: RefreshLease) -> None:
        """Idempotently clear only the matching lease."""

    @abstractmethod
    async def revoke(self, token_hash: str, user_id: str, jti: str) -> bool:
        """Atomically tombstone an ACTIVE session; bool is strict revoke receipt."""

    @abstractmethod
    async def close(self) -> None:
        """Invalidate this instance and prevent retained-reference operations."""
