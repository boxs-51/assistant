import structlog
from typing import Optional
from redis.exceptions import RedisError

from ..interfaces.repository import BaseRepository
from ..interfaces.cache import CacheDriver
from ..interfaces.refresh_sessions import RefreshLease, RefreshSessionStore

logger = structlog.get_logger(__name__)

class SessionRepository(BaseRepository):
    """
    Repository để quản lý session và refresh tokens sử dụng Cache (Redis).
    """
    def __init__(self, cache_driver: CacheDriver, refresh_store: RefreshSessionStore | None = None):
        self.cache_driver = cache_driver
        self.refresh_store = refresh_store
        # Định nghĩa prefix để tránh xung đột key trong Redis
        self.prefix = "session:refresh_token:"

    async def save_token(self, user_id: str, token_hash: str, expires_in_seconds: int):
        """
        Lưu hash của refresh token vào cache với user_id làm value.
        Key sẽ là `session:refresh_token:<token_hash>`.
        """
        key = f"{self.prefix}{token_hash}"
        try:
            await self.cache_driver.set(key, user_id, expire=expires_in_seconds)
            logger.debug("Refresh token saved to cache", key=key, user_id=user_id)
        except (RedisError, RuntimeError) as e:
            # Fail closed: never return an issued refresh credential without a stored session.
            logger.warning("Refresh token storage failed; abort token issuance.", error=str(e))
            raise

    async def get_user_id_by_token(self, token_hash: str) -> Optional[str]:
        """
        Lấy user_id từ cache bằng hash của refresh token.
        """
        key = f"{self.prefix}{token_hash}"
        try:
            return await self.cache_driver.get(key)
        except (RedisError, RuntimeError) as e:
            logger.warning("Redis is not available. Cannot fetch token.", error=str(e))
            return None  # Trả về None tạm thời

    async def delete_token(self, token_hash: str) -> int:
        """Return the atomic cache deletion count; never invent a revoke ACK."""
        key = f"{self.prefix}{token_hash}"
        try:
            deleted = await self.cache_driver.delete(key)
            # Reject void, boolean and unexpected counts from nonconforming
            # drivers rather than treating a no-op as successful revocation.
            if type(deleted) is not int or deleted not in (0, 1):
                raise RuntimeError("Refresh token cache deletion was not confirmed")
            if deleted:
                logger.debug("Refresh token deleted from cache", key=key)
            return deleted
        except (RedisError, RuntimeError) as e:
            logger.warning("Refresh token cache deletion failed", error=str(e))
            raise  # P0A: backend failures must propagate, never HTTP 204.
    async def assert_legacy_active(self, user_id: str, token_hash: str) -> bool:
        """Final legacy refresh authorization: one live cache read after user lookup.

        This is the service-level linearization point for pre-V2 credentials;
        it cannot guarantee HTTP delivery order after a logout 204 response.
        Unlike the historical initial lookup, backend failures MUST propagate.
        """
        value = await self.cache_driver.get(f"{self.prefix}{token_hash}")
        return value is not None and value == user_id

    def _require_refresh_store(self) -> RefreshSessionStore:
        if self.refresh_store is None:
            raise RuntimeError("V2 refresh authority is not configured")
        return self.refresh_store

    async def issue_v2(self, token_hash: str, user_id: str, jti: str, ttl_seconds: int) -> bool:
        return await self._require_refresh_store().issue(token_hash, user_id, jti, ttl_seconds)

    async def begin_v2(self, token_hash: str, user_id: str, jti: str) -> RefreshLease | None:
        return await self._require_refresh_store().begin(token_hash, user_id, jti)

    async def finalize_v2(self, lease: RefreshLease) -> bool:
        return await self._require_refresh_store().finalize(lease)

    async def abort_v2(self, lease: RefreshLease) -> None:
        await self._require_refresh_store().abort(lease)

    async def revoke_v2(self, token_hash: str, user_id: str, jti: str) -> bool:
        return await self._require_refresh_store().revoke(token_hash, user_id, jti)
