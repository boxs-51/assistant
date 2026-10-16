"""Auth T1 RED-first: a failed refresh-session write must not issue tokens.

Real SessionRepository and RedisDriver error path, with a minimal user-reader
fixture only for the unrelated SQL role/org lookup. NO production modifications.
"""
import asyncio

import pytest
from pydantic import SecretStr

from se.src.infrastructure.config.schemas import AuthenticationSettings, DriverConfig
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.infrastructure.storage.repositories.sessions import SessionRepository
from se.src.transport.gateway.authentication.services.token_service import TokenService


class _UserReader:
    async def get_user_roles(self, user_id):
        return []

    async def get_organization_for_user(self, user_id):
        return None


class _Uow:
    def __init__(self):
        self.users = _UserReader()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _disconnected_repository():
    # No Redis daemon, fake exception or monkeypatched SessionRepository.
    backend = RedisDriver(DriverConfig(options={"url": "redis://127.0.0.1:1/0"}))
    assert backend.is_connected is False
    return SessionRepository(backend)


def test_session_repository_save_must_propagate_disconnected_redis_failure():
    async def exercise():
        repo = _disconnected_repository()
        with pytest.raises(RuntimeError, match="not connected"):
            await repo.save_token("user", "hashed-refresh", expires_in_seconds=3600)

    asyncio.run(exercise())


def test_token_issuer_must_not_return_success_when_refresh_hash_not_stored():
    async def exercise():
        repo = _disconnected_repository()
        config = AuthenticationSettings(
            jwt_secret_key=SecretStr("ae359-t1-only-not-a-real-secret-123456789"),
            refresh_token_expire_days=30,
        )
        issuer = TokenService(lambda: _Uow(), repo, config)
        with pytest.raises(RuntimeError, match="not connected"):
            await issuer.create_user_tokens("user", "user@example.invalid")

    asyncio.run(exercise())


def test_positive_control_connected_inmemory_session_write_and_read():
    from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver

    async def exercise():
        backend = InMemoryDriver(DriverConfig())
        await backend.connect()
        repo = SessionRepository(backend)
        await repo.save_token("user", "token-hash", expires_in_seconds=3600)
        assert await repo.get_user_id_by_token("token-hash") == "user"
        await backend.disconnect()

    asyncio.run(exercise())
