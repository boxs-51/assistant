"""R2 security contract: deterministic atomic refresh/logout and lifecycle witnesses.

Kept separate from the frozen PR535/545 evidence. This file is approved ADD
only; tests are never softened, skipped or marked xfail.
"""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from se.src.infrastructure.config.schemas import AuthenticationSettings, DriverConfig
from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver
from se.src.infrastructure.storage.repositories.sessions import SessionRepository
from se.src.infrastructure.storage.repositories.refresh_sessions_inmemory import InMemoryRefreshSessionStore
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.transport.gateway.authentication.services.token_service import TokenService


class _Users:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id, email="r2@example.invalid")

    async def get_user_roles(self, user_id):
        return []

    async def get_organization_for_user(self, user_id):
        return None


class _Uow:
    def __init__(self):
        self.users = _Users()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _auth_config():
    return AuthenticationSettings(
        jwt_secret_key=SecretStr("ae359-r2-approved-tests-only-secret-1234567"),
        refresh_token_expire_days=30,
    )


def test_v2_issue_begin_revoke_before_finalize_is_fenced():
    async def run():
        store = InMemoryRefreshSessionStore()
        assert await store.issue("token-hash", "u", "jti", 60) is True
        lease = await store.begin("token-hash", "u", "jti")
        assert lease is not None
        assert await store.revoke("token-hash", "u", "jti") is True
        assert await store.finalize(lease) is False
        assert await store.begin("token-hash", "u", "jti") is None
        assert await store.issue("token-hash", "u", "jti", 60) is False
        await store.close()
    asyncio.run(run())


def test_v2_finalize_wins_before_revoke_with_no_resurrection():
    async def run():
        store = InMemoryRefreshSessionStore()
        assert await store.issue("t", "u", "j", 60)
        lease = await store.begin("t", "u", "j")
        assert await store.finalize(lease) is True
        assert await store.revoke("t", "u", "j") is True
        assert await store.revoke("t", "u", "j") is False
        assert await store.begin("t", "u", "j") is None
        await store.close()
    asyncio.run(run())


def test_v2_bounded_lease_is_not_reusable_after_abort():
    async def run():
        store = InMemoryRefreshSessionStore()
        assert await store.issue("t", "u", "j", 60)
        first = await store.begin("t", "u", "j")
        assert first is not None
        assert await store.begin("t", "u", "j") is None
        await store.abort(first)
        second = await store.begin("t", "u", "j")
        assert second is not None and second.lease_id != first.lease_id
        assert await store.finalize(first) is False
        assert await store.finalize(second) is True
        await store.close()
    asyncio.run(run())


def test_v2_retained_store_fails_closed_after_close():
    async def run():
        store = InMemoryRefreshSessionStore()
        assert await store.issue("t", "u", "j", 60)
        lease = await store.begin("t", "u", "j")
        await store.close()
        with pytest.raises(RuntimeError):
            await store.finalize(lease)
        with pytest.raises(RuntimeError):
            await store.issue("t", "u", "j", 60)
        with pytest.raises(RuntimeError):
            await store.revoke("t", "u", "j")
    asyncio.run(run())


def test_v2_real_service_logout_fences_paused_inflight_refresh():
    async def run():
        backend = InMemoryDriver(DriverConfig())
        await backend.connect()
        store = InMemoryRefreshSessionStore()
        reached_lookup = asyncio.Event()
        release_lookup = asyncio.Event()

        class _PausedUsers(_Users):
            async def get_by_id(self, user_id):
                reached_lookup.set()
                await asyncio.wait_for(release_lookup.wait(), timeout=4)
                return await super().get_by_id(user_id)

        class _PausedUow(_Uow):
            def __init__(self):
                self.users = _PausedUsers()

        repo = SessionRepository(backend, refresh_store=store)
        svc = TokenService(lambda: _Uow(), repo, _auth_config())
        issued = await svc.create_user_tokens("u", "r2@example.invalid")
        claims = svc.jwt.decode_token(issued.refresh_token)
        assert claims["sv"] == 2 and claims["jti"]
        token_hash = hashlib.sha256(issued.refresh_token.encode()).hexdigest()
        assert await backend.get(repo.prefix + token_hash) is None
        svc.uow_factory = lambda: _PausedUow()
        task = asyncio.create_task(svc.refresh_access_token(issued.refresh_token))
        try:
            await asyncio.wait_for(reached_lookup.wait(), timeout=4)
            await svc.logout(issued.refresh_token)
            release_lookup.set()
            with pytest.raises(InvalidCredentialsError):
                await asyncio.wait_for(task, timeout=4)
            with pytest.raises(InvalidCredentialsError):
                await svc.refresh_access_token(issued.refresh_token)
        finally:
            release_lookup.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await store.close()
            await backend.disconnect()
    asyncio.run(run())


def test_legacy_refresh_never_issues_an_unreturned_refresh_credential():
    async def run():
        backend = InMemoryDriver(DriverConfig())
        await backend.connect()
        repo = SessionRepository(backend)
        svc = TokenService(lambda: _Uow(), repo, _auth_config())
        token = svc.jwt.create_refresh_token({"sub": "u"})
        digest = hashlib.sha256(token.encode()).hexdigest()
        await repo.save_token("u", digest, 3600)
        result = await svc.refresh_access_token(token)
        assert result.access_token
        assert await backend.get(repo.prefix + digest) == "u"
        # Legacy refresh must not write any different refresh-hash record.
        assert len(backend._data) == 1
        await backend.disconnect()
    asyncio.run(run())
