import asyncio
from types import SimpleNamespace

import pytest

from se.src.transport.gateway.authentication import password as password_helper
from se.src.transport.gateway.authentication.exceptions import OTPInvalidError
from se.src.transport.gateway.authentication.services.password_reset_service import (
    PasswordResetService,
)


class _Users:
    def __init__(self, user=None):
        self.user = user

    async def get_by_email(self, email):
        if self.user and self.user.email == email:
            return self.user
        return None

    async def set_password(self, user_id, hashed_password):
        if not self.user or str(self.user.id) != str(user_id):
            return None
        self.user.password_hash = hashed_password
        return self.user


class _Uow:
    def __init__(self, users):
        self.users = users
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def commit(self):
        self.committed = True


class _OtpStorage:
    cooldown_ttl = 60

    def __init__(self, reset_data=None, cooldown=0):
        self.reset_data = reset_data
        self.cooldown = cooldown
        self.saved = []

    async def check_password_reset_cooldown(self, email):
        return self.cooldown

    async def save_pending_password_reset(self, email, data, otp):
        self.saved.append((email, data, otp))

    async def verify_password_reset(self, email, otp):
        return self.reset_data


def test_password_reset_initiation_has_uniform_response_for_unknown_account():
    uow = _Uow(_Users())
    otp = _OtpStorage()
    service = PasswordResetService(lambda: uow, otp)

    response = asyncio.run(service.initiate("missing@example.com"))

    assert response["status"] == "success"
    assert "If the account exists" in response["message"]
    assert otp.saved == []


def test_password_reset_stores_challenge_and_updates_password(monkeypatch):
    user = SimpleNamespace(id="user-1", email="user@example.com", password_hash="old")
    uow = _Uow(_Users(user))
    otp = _OtpStorage(reset_data={"user_id": user.id, "email": user.email})
    service = PasswordResetService(lambda: uow, otp)
    monkeypatch.setattr(password_helper, "get_password_hash", lambda value: f"hashed:{value}")

    asyncio.run(service.initiate(user.email))
    assert len(otp.saved) == 1
    assert len(otp.saved[0][2]) == 6

    response = asyncio.run(service.confirm(user.email, "123456", "new-secret"))
    assert response["status"] == "success"
    assert user.password_hash == "hashed:new-secret"
    assert uow.committed is True


def test_password_reset_rejects_invalid_or_expired_code():
    uow = _Uow(_Users())
    service = PasswordResetService(lambda: uow, _OtpStorage(reset_data=None))

    with pytest.raises(OTPInvalidError):
        asyncio.run(service.confirm("user@example.com", "000000", "new-secret"))
