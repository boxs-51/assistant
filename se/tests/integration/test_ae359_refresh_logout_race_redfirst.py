"""AE359 T1 evidence: refresh/logout race and refresh JWT uniqueness.

RED-first only. These tests document unresolved security requirements.
Do not xfail, mock out the storage behavior, or merge this evidence branch.
"""
import asyncio
import hashlib
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from se.src.infrastructure.config.schemas import AuthenticationSettings, DriverConfig
from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver
from se.src.infrastructure.storage.repositories.sessions import SessionRepository
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.transport.gateway.authentication.jwt import JwtHelper
from se.src.transport.gateway.authentication.services.token_service import TokenService


_USER_ID = "ae359-race-user"


def _config():
    return AuthenticationSettings(
        jwt_secret_key=SecretStr("ae359-t1-only-not-a-real-secret-123456789"),
        access_token_expire_minutes=30,
        refresh_token_expire_days=30,
    )


def _freeze_jwt_seconds(monkeypatch):
    jwt_module = importlib.import_module("se.src.transport.gateway.authentication.jwt")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc)
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(jwt_module, "datetime", FrozenDateTime)


class _UserReader:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id, email="ae359@example.invalid")

    async def get_user_roles(self, user_id):
        return []

    async def get_organization_for_user(self, user_id):
        return None


class _ReadOnlyUow:
    def __init__(self):
        self.users = _UserReader()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_refresh_jwts_for_same_subject_second_must_be_distinct(monkeypatch):
    """Refresh credentials must have cryptographic per-issue identity."""
    _freeze_jwt_seconds(monkeypatch)
    jwt = JwtHelper(_config())
    first = jwt.create_refresh_token({"sub": _USER_ID})
    second = jwt.create_refresh_token({"sub": _USER_ID})
    assert first != second, (
        "RED-FIRST: refresh JWT lacks unique jti/entropy; same-second "
        "issuance creates the same credential and cache key"
    )


def test_logout_ack_must_prevent_inflight_refresh_from_reinserting_token(monkeypatch):
    """Real cache+service, deterministic check-before-DEL interleaving.

    Only the lookup gate is test-controlled. No backend deletion or
    JWT-creation behavior is mocked.
    """
    _freeze_jwt_seconds(monkeypatch)

    async def exercise():
        cache = InMemoryDriver(DriverConfig())
        await cache.connect()
        lookup_done = asyncio.Event()
        allow_refresh = asyncio.Event()

        class PausedRepository(SessionRepository):
            async def get_user_id_by_token(self, token_hash):
                user_id = await super().get_user_id_by_token(token_hash)
                lookup_done.set()
                await allow_refresh.wait()
                return user_id

        repo = PausedRepository(cache)
        service = TokenService(lambda: _ReadOnlyUow(), repo, _config())
        refresh_token = service.jwt.create_refresh_token({"sub": _USER_ID})
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        key = repo.prefix + token_hash
        await cache.set(key, _USER_ID, expire=3600)

        refresh_task = asyncio.create_task(service.refresh_access_token(refresh_token))
        try:
            await asyncio.wait_for(lookup_done.wait(), timeout=4)
            await service.logout(refresh_token)
            assert await cache.get(key) is None, "Invalid witness: logout did not delete"
            allow_refresh.set()

            # After an acknowledged logout, the in-flight refresh must not
            # succeed based only on a stale user lookup.
            with pytest.raises(InvalidCredentialsError):
                await asyncio.wait_for(refresh_task, timeout=4)
            assert await cache.get(key) is None, (
                "RED-FIRST: refresh re-created an already revoked hash"
            )
        finally:
            allow_refresh.set()
            if not refresh_task.done():
                refresh_task.cancel()
                await asyncio.gather(refresh_task, return_exceptions=True)
            await cache.disconnect()

    asyncio.run(exercise())
