"""S04 T08: pinned historical OTP consumer Redis GET/DEL -> SQL fallback.

Test-only, physical disposable PG17/Redis7.4. The historic class is imported
unaltered from verified Git blob using the existing T07 test-only loader.
The legacy production ORM/binary is NOT reproduced; database UOW is an adapter.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def _legacy_helper():
    path = Path(__file__).with_name("s04_t07_historical_source_dual_fence_physical.py")
    spec = importlib.util.spec_from_file_location("s04_t07_loader", path)
    assert spec and spec.loader, "S04_HARNESS_ERROR: pinned historical loader unavailable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._verify_history()
    return module


def _admit():
    dsn = os.environ.get("S04_T08_PG_DSN", "")
    u = urlparse(dsn)
    assert (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_T08_STRICT") == "1"
            and os.environ.get("S04_T08_DISPOSABLE") == "1"
            and u.scheme == "postgresql" and u.hostname == "127.0.0.1"
            and u.port == 5432 and u.username == "s04_t08_admin"
            and u.path == "/s04_t08_disposable" and not u.password
            and not u.query), "S04_HARNESS_ERROR: disposable PG17 identity required"
    return dsn


def _payload(purpose, via):
    return json.dumps({
        "otp": "123456", "purpose": purpose, "user_data": {"via": via},
        "created_at": "2026-10-22T00:00:00+00:00",
    })


async def _child(request):
    import asyncpg
    import redis.asyncio as redis

    old = _legacy_helper()._original_class()
    dsn = _admit()
    schema = request["schema"]
    role = request["role"]
    user = request["redis_user"]
    password = request["redis_password"]
    purpose = request["purpose"]
    email = request["email"]
    assert (schema.startswith("s04t08_") and role.startswith("s04t08old_")
            and user.startswith("s04t08redis_")
            and purpose in ("registration", "password_reset")
            and email.endswith("@example.invalid")), (
        "S04_HARNESS_ERROR: historical child scope mismatch")

    client = redis.Redis(host="127.0.0.1", port=6379,
                         username=user, password=password,
                         decode_responses=True)

    class PhysicalCache:
        async def get(self, key):
            return await client.get(key)
        async def delete(self, key):
            return await client.delete(key)

    class PendingRepository:
        def __init__(self, uow): self.uow = uow
        async def get_and_delete(self, email_arg):
            row = await self.uow.conn.fetchrow(
                f'DELETE FROM "{schema}".pending WHERE email=$1 RETURNING payload',
                email_arg)
            return SimpleNamespace(payload=row["payload"]) if row else None

    class PhysicalUOW:
        def __init__(self): self.done = False
        async def __aenter__(self):
            self.conn = await asyncpg.connect(dsn, user=role)
            self.tx = self.conn.transaction()
            await self.tx.start()
            self.pending_registrations = PendingRepository(self)
            return self
        async def commit(self):
            await self.tx.commit()
            self.done = True
        async def __aexit__(self, exc_type, exc, tb):
            try:
                if not self.done:
                    await self.tx.rollback()
            finally:
                await self.conn.close()

    service = old(PhysicalCache(), PhysicalUOW)
    try:
        try:
            if purpose == "registration":
                result = await service.verify_and_get_data(email, "123456")
            else:
                result = await service.verify_password_reset(email, "123456")
        except asyncpg.InsufficientPrivilegeError:
            return "SQL_DELETE_FENCED"
        if result is None:
            return "NOT_REDEEMED"
        assert result in ({"via": "redis"}, {"via": "sql"}), (
            "S04_HARNESS_ERROR: unexpected historical payload")
        return "REDEEMED_" + result["via"].upper()
    finally:
        await client.aclose()


def _invoke(request):
    result = subprocess.run(
        [sys.executable, __file__, "--consumer-child"],
        input=json.dumps(request), text=True, capture_output=True,
        timeout=25, env=dict(os.environ))
    assert result.returncode == 0, (
        "S04_HARNESS_ERROR: historical process failed; stderr intentionally redacted")
    receipt = result.stdout.strip()
    assert receipt in {"REDEEMED_REDIS", "REDEEMED_SQL",
                       "SQL_DELETE_FENCED", "NOT_REDEEMED"}, (
        "S04_HARNESS_ERROR: unknown child receipt")
    return receipt


async def _scenario(purpose, mode):
    import asyncpg
    import redis.asyncio as redis

    dsn = _admit()
    _legacy_helper()._verify_history()
    admin = await asyncpg.connect(dsn)
    redis_admin = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    suffix = uuid4().hex[:18]
    schema = "s04t08_" + suffix
    sql_role = "s04t08old_" + suffix
    redis_user = "s04t08redis_" + suffix
    password = secrets.token_hex(18)
    email = "t08-" + suffix + "@example.invalid"
    key = ("pending_reg:" if purpose=="registration"
           else "pending_password_reset:") + email
    role_created = schema_created = redis_created = False
    try:
        db = await admin.fetchrow(
            "SELECT current_database() AS db, current_user AS actor")
        version = int(await admin.fetchval("SHOW server_version_num"))
        assert (170000 <= version < 180000 and db["db"]=="s04_t08_disposable"
                and db["actor"]=="s04_t08_admin"), (
            "S04_HARNESS_ERROR: wrong physical PG17 server/role")
        assert await redis_admin.ping(), "S04_HARNESS_ERROR: Redis physical unavailable"

        await admin.execute(
            f"CREATE ROLE {sql_role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE")
        role_created = True
        await admin.execute(f"CREATE SCHEMA {schema}")
        schema_created = True
        await admin.execute(
            f'CREATE TABLE "{schema}".pending(email text PRIMARY KEY, payload text NOT NULL)')
        await admin.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {sql_role}")
        await admin.execute(f'GRANT SELECT, DELETE ON "{schema}".pending TO {sql_role}')
        await redis_admin.execute_command(
            "ACL", "SETUSER", redis_user, "reset", "on", ">" + password,
            "~pending_reg:*", "~pending_password_reset:*", "+get", "+del", "+ping")
        redis_created = True

        await redis_admin.set(key, _payload(purpose,"redis"), ex=300)
        if mode != "baseline":
            await admin.execute(
                f'INSERT INTO "{schema}".pending(email,payload) VALUES($1,$2)',
                email, _payload(purpose,"sql"))
        request = {
            "schema": schema, "role": sql_role, "redis_user": redis_user,
            "redis_password": password, "purpose": purpose, "email": email,
        }
        if mode != "baseline":
            # Deliberately distinguish GET-denied from DEL-denied execution.
            denied = "get" if mode.startswith("get_") else "del"
            assert await redis_admin.execute_command(
                "ACL", "SETUSER", redis_user, "-" + denied)
            if mode.endswith("_sql_fenced"):
                await admin.execute(
                    f'REVOKE DELETE ON "{schema}".pending FROM {sql_role}')
                privilege = await admin.fetchval(
                    "SELECT has_table_privilege($1::name,$2::text,'DELETE')",
                    sql_role, f'{schema}.pending')
                assert privilege is False, "S04_HARNESS_ERROR: SQL revocation ineffective"

        receipt = await asyncio.to_thread(_invoke, request)
        if mode == "baseline":
            assert receipt == "REDEEMED_REDIS", (
                "S04_HARNESS_ERROR: baseline Redis GET+DEL control unavailable")
            assert await redis_admin.get(key) is None
            assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".pending') == 0
            # Historical method must not redeem same challenge twice.
            assert await asyncio.to_thread(_invoke,request) == "NOT_REDEEMED", (
                "S04_SECURITY_RED: historical Redis OTP replayed")
        elif mode.endswith("_sql_open"):
            assert receipt == "REDEEMED_SQL", (
                "S04_HARNESS_ERROR: physical SQL fallback hazard not reproduced")
            assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".pending') == 0
            assert await redis_admin.get(key) == _payload(purpose,"redis"), (
                "S04_HARNESS_ERROR: Redis token unexpectedly consumed")
        else:
            assert receipt == "SQL_DELETE_FENCED", (
                "S04_SECURITY_RED: historical consumer redeemed outside v2 authority")
            assert await asyncio.to_thread(_invoke,request) == "SQL_DELETE_FENCED", (
                "S04_SECURITY_RED: historical restart regained consumption authority")
            assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".pending') == 1
            assert await redis_admin.get(key) == _payload(purpose,"redis"), (
                "S04_SECURITY_RED: failed redemption destroyed legacy Redis token")
    finally:
        if redis_created:
            await redis_admin.execute_command("ACL","DELUSER",redis_user)
        await redis_admin.delete(key)
        if schema_created:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if role_created:
            await admin.execute(f"DROP ROLE {sql_role}")
        await redis_admin.aclose()
        await admin.close()


@pytest.mark.parametrize("purpose", ["registration","password_reset"])
def test_t08_01_historical_redis_get_del_one_time_positive(purpose):
    asyncio.run(_scenario(purpose,"baseline"))


@pytest.mark.parametrize("purpose", ["registration","password_reset"])
def test_t08_02_redis_get_denied_sql_fallback_redeems(purpose):
    asyncio.run(_scenario(purpose,"get_sql_open"))


@pytest.mark.parametrize("purpose", ["registration","password_reset"])
def test_t08_03_redis_del_denied_sql_fallback_redeems(purpose):
    asyncio.run(_scenario(purpose,"del_sql_open"))


@pytest.mark.parametrize("purpose", ["registration","password_reset"])
def test_t08_04_get_denied_and_sql_delete_fenced(purpose):
    asyncio.run(_scenario(purpose,"get_sql_fenced"))


@pytest.mark.parametrize("purpose", ["registration","password_reset"])
def test_t08_05_del_denied_and_sql_delete_fenced(purpose):
    asyncio.run(_scenario(purpose,"del_sql_fenced"))


if __name__=="__main__" and sys.argv[1:]==["--consumer-child"]:
    try:
        print(asyncio.run(_child(json.loads(sys.stdin.read()))))
    except Exception:
        # All fixture credentials/payloads/stderr remain out of CI output.
        sys.exit(2)
