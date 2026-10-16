"""#359 logout's HTTP ACK must not mask failed revocation.

A failure test is scoped: it does NOT prove durable access-token revocation,
registry generation, UI dispatch fencing, or physical post-logout semantics.
"""
import asyncio

import pytest
from fastapi import HTTPException

from se.src.domain.schemas.auth import RefreshRequestSchema
from se.src.transport.gateway.authentication.exceptions import InvalidCredentialsError
from se.src.transport.gateway.api.v1.auth_router import logout


class _FakeAuth:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def logout(self, refresh_token):
        self.calls.append(refresh_token)
        if self.error:
            raise self.error


def _call(auth):
    return asyncio.run(logout(
        RefreshRequestSchema(refresh_token="submitted-refresh-token"),
        auth_facade=auth,
        event_bus=None,
    ))


def test_logout_revoke_backend_failure_must_never_ack_http_204():
    auth = _FakeAuth(RuntimeError("database unavailable"))
    with pytest.raises(HTTPException) as caught:
        _call(auth)
    assert caught.value.status_code == 503
    assert "database unavailable" not in str(caught.value.detail)
    assert auth.calls == ["submitted-refresh-token"]


def test_logout_rejects_invalid_refresh_without_ack():
    auth = _FakeAuth(InvalidCredentialsError("missing token"))
    with pytest.raises(HTTPException) as caught:
        _call(auth)
    assert caught.value.status_code == 401


def test_logout_success_keeps_204_but_does_not_assert_physical_fence():
    auth = _FakeAuth()
    response = _call(auth)
    assert response.status_code == 204
    assert auth.calls == ["submitted-refresh-token"]
