import asyncio
import json
from types import SimpleNamespace

from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService


class _Cache:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)


class _PendingRegistrations:
    def __init__(self, record=None):
        self.record = record

    async def get_and_delete(self, _email):
        record, self.record = self.record, None
        return record


class _Uow:
    def __init__(self, pending):
        self.pending_registrations = pending
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        self.commits += 1


def test_password_reset_cache_challenge_is_consumed_even_on_wrong_code():
    key = "pending_password_reset:user@example.com"
    cache = _Cache({key: json.dumps({
        "purpose": "password_reset",
        "otp": "123456",
        "user_data": {"user_id": "user-1"},
    })})
    service = OTPStorageService(cache, lambda: _Uow(_PendingRegistrations()))

    assert asyncio.run(service.verify_password_reset("user@example.com", "000000")) is None
    assert asyncio.run(service.verify_password_reset("user@example.com", "123456")) is None


def test_password_reset_database_challenge_deletion_is_committed():
    record = SimpleNamespace(payload=json.dumps({
        "purpose": "password_reset",
        "otp": "123456",
        "user_data": {"user_id": "user-1"},
    }))
    uow = _Uow(_PendingRegistrations(record))
    service = OTPStorageService(None, lambda: uow)

    result = asyncio.run(service.verify_password_reset("user@example.com", "123456"))

    assert result == {"user_id": "user-1"}
    assert uow.commits == 1
    assert uow.pending_registrations.record is None
