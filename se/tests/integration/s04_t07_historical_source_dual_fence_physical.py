"""S04 T07: pinned historical OTP code in independent Python process.

This is 2026-09-17 SOURCE METHOD proof, *not* a packaged historical binary.
Redis and PostgreSQL are disposable GitHub Actions physical backends.
"""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import types
from urllib.parse import urlparse
from uuid import uuid4

import pytest

OLD_COMMIT = "40b502cbb1ae7d678e536e68b4359322bab467ca"
OLD_BLOB = "5a7e6bcdba3d5eec5fcf34a7e8760508a4401271"
HISTORIC = Path("s04-historical/se/src/transport/gateway/authentication/services/otp_service.py")


def _admit():
    raw = os.environ.get("S04_T07_PG_DSN", "")
    u = urlparse(raw)
    assert (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_T07_STRICT") == "1"
            and os.environ.get("S04_T07_DISPOSABLE") == "1"
            and u.scheme == "postgresql" and u.hostname == "127.0.0.1"
            and u.port == 5432 and u.username == "s04_t07_admin"
            and u.path == "/s04_t07_disposable" and not u.password
            and not u.query), "S04_HARNESS_ERROR: dedicated PG17 identity required"
    return raw


def _verify_history():
    raw = HISTORIC.read_bytes()
    git_blob = b"blob " + str(len(raw)).encode() + b"\0" + raw
    assert hashlib.sha1(git_blob).hexdigest() == OLD_BLOB, (
        "S04_HARNESS_ERROR: pinned historical OTP blob drift")
    assert b"async def save_pending_registration" in raw
    assert b"async def save_pending_password_reset" in raw
    assert b"create_or_update" in raw
    return raw


def _original_class():
    """Execute the whole verbatim historic source with only dependency imports stubbed.

    Redis I/O and PG UOW use real physical drivers, not method replacements.
    """
    source = _verify_history()
    original_import = builtins.__import__
    fake_log = types.SimpleNamespace(error=lambda *a, **k: None)
    mock_structlog = types.SimpleNamespace(get_logger=lambda *a, **k: fake_log)
    def historical_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "structlog" and level == 0:
            return mock_structlog
        if level == 5 and name == "infrastructure.storage.core.unit_of_work":
            return types.SimpleNamespace(SqlAlchemyUnitOfWork=object)
        if level == 5 and name == "infrastructure.storage.interfaces.cache":
            return types.SimpleNamespace(CacheDriver=object)
        return original_import(name, globals, locals, fromlist, level)
    env = {
        "__name__": "s04_pinned_historical_otp",
        "__package__": "se.src.transport.gateway.authentication.services",
        "__builtins__": {**vars(builtins), "__import__": historical_import},
    }
    exec(compile(source, str(HISTORIC), "exec"), env)
    return env["OTPStorageService"]


async def _child(request):
    import asyncpg
    import redis.asyncio as redis

    service_cls = _original_class()
    pg = request["pg"]
    schema = request["schema"]
    role = request["pg_role"]
    user = request["redis_user"]
    pw = request["redis_password"]
    purpose = request["purpose"]
    email = request["email"]
    if not (schema.startswith("s04_t07_") and role.startswith("s04_old_")
            and user.startswith("s04old_") and email.endswith("@example.invalid")):
        raise AssertionError("S04_HARNESS_ERROR: child identity mismatch")

    client = redis.Redis(host="127.0.0.1", port=6379,
                         username=user, password=pw, decode_responses=True)

    class PhysicalCache:
        async def set(self, key, value, expire):
            return await client.set(key, value, ex=expire)
        async def get_ttl(self, key):
            return await client.ttl(key)

    class PhysicalPending:
        def __init__(self, uow): self.uow = uow
        async def create_or_update(self, *, email, payload, expires_in_seconds):
            await self.uow.conn.execute(
                f'''INSERT INTO "{schema}".pending(email,payload)
                    VALUES($1,$2) ON CONFLICT(email) DO UPDATE SET payload=EXCLUDED.payload''',
                email, payload)

    class PhysicalUOW:
        def __init__(self): self.done = False
        async def __aenter__(self):
            self.conn = await asyncpg.connect(pg, user=role)
            self.tx = self.conn.transaction()
            await self.tx.start()
            self.pending_registrations = PhysicalPending(self)
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

    service = service_cls(PhysicalCache(), PhysicalUOW)
    try:
        try:
            if purpose == "registration":
                await service.save_pending_registration(
                    email, {"fixture":"registration"}, "123456")
            elif purpose == "password_reset":
                await service.save_pending_password_reset(
                    email, {"fixture":"password_reset"}, "123456")
            else:
                raise AssertionError("S04_HARNESS_ERROR: unexpected purpose")
        except asyncpg.InsufficientPrivilegeError:
            result = "FENCED_SQL"
        else:
            # Directly inspect physical PG and Redis to distinguish the paths.
            pg_read = await asyncpg.connect(pg, user="s04_t07_admin")
            try:
                count = await pg_read.fetchval(
                    f'SELECT count(*) FROM "{schema}".pending WHERE email=$1', email)
            finally:
                await pg_read.close()
            result = "SQL_FALLBACK_COMMITTED" if count else "REDIS_ISSUED"
        return result
    finally:
        await client.aclose()


def _invoke(request):
    env = dict(os.environ)
    child = subprocess.run([sys.executable, __file__, "--historical-child"],
                           input=json.dumps(request), text=True, capture_output=True,
                           timeout=25, env=env)
    assert child.returncode == 0, (
        "S04_HARNESS_ERROR: historical child failed; stderr suppressed to "
        "avoid leaking fixture credentials")
    result = child.stdout.strip()
    assert result in {"REDIS_ISSUED", "SQL_FALLBACK_COMMITTED", "FENCED_SQL"}, (
        "S04_HARNESS_ERROR: unrecognized historical child receipt")
    return result


async def _scenario(purpose, phase):
    import asyncpg
    import redis.asyncio as redis
    dsn = _admit()
    _verify_history()
    admin = await asyncpg.connect(dsn)
    master = redis.Redis(host="127.0.0.1",port=6379,decode_responses=True)
    suffix=uuid4().hex[:18]
    schema="s04_t07_"+suffix
    pg_role="s04_old_"+suffix
    redis_user="s04old_"+suffix
    password=secrets.token_hex(18)
    email=f"s04t07-{suffix}@example.invalid"
    created_pg = False
    created_role = False
    created_redis = False
    try:
        version=int(await admin.fetchval("SHOW server_version_num"))
        who=await admin.fetchrow("SELECT current_database() AS db,current_user AS u")
        assert 170000<=version<180000 and who["db"]=="s04_t07_disposable" and who["u"]=="s04_t07_admin",(
            "S04_HARNESS_ERROR: wrong physical PostgreSQL instance")
        assert await master.ping(), "S04_HARNESS_ERROR: physical Redis unavailable"
        await admin.execute(f"CREATE ROLE {pg_role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE")
        created_role=True
        await admin.execute(f"CREATE SCHEMA {schema}")
        created_pg=True
        await admin.execute(f"CREATE TABLE {schema}.pending(email text PRIMARY KEY,payload text NOT NULL)")
        await admin.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {pg_role}")
        await admin.execute(f"GRANT SELECT,INSERT,UPDATE ON {schema}.pending TO {pg_role}")
        await master.execute_command("ACL","SETUSER",redis_user,"reset","on",">"+password,
                                     "~pending_reg:*","~pending_password_reset:*",
                                     "~otp_cooldown:*","~password_reset_cooldown:*",
                                     "+set","+get","+ping")
        created_redis=True
        request={"pg":dsn,"schema":schema,"pg_role":pg_role,"redis_user":redis_user,
                 "redis_password":password,"purpose":purpose,"email":email}
        if phase=="pre_fence":
            receipt=await asyncio.to_thread(_invoke,request)
            assert receipt=="REDIS_ISSUED", "S04_HARNESS_ERROR: historical Redis positive control failed"
            key=("pending_reg:" if purpose=="registration" else "pending_password_reset:")+email
            assert await master.get(key) is not None
            assert await admin.fetchval(f"SELECT count(*) FROM {schema}.pending")==0
        else:
            assert await master.execute_command("ACL","SETUSER",redis_user,"-set")
            if phase=="dual_fence":
                await admin.execute(f"REVOKE INSERT,UPDATE ON {schema}.pending FROM {pg_role}")
                assert await admin.fetchval(
                    "SELECT has_table_privilege($1::name,$2::text,'INSERT')",
                    pg_role, f"{schema}.pending") is False
            receipt=await asyncio.to_thread(_invoke,request)
            if phase=="redis_only":
                assert receipt=="SQL_FALLBACK_COMMITTED", (
                    "S04_HARNESS_ERROR: historical DB fallback did not reproduce")
                assert await admin.fetchval(f"SELECT count(*) FROM {schema}.pending")==1
            else:
                assert receipt=="FENCED_SQL", (
                    "S04_SECURITY_RED: historical source found an alternate writer authority")
                # Restart/reconnect the historical process under identical old credentials.
                receipt2=await asyncio.to_thread(_invoke,request)
                assert receipt2=="FENCED_SQL", (
                    "S04_SECURITY_RED: historical restart regained write authority")
                assert await admin.fetchval(f"SELECT count(*) FROM {schema}.pending")==0
                key=("pending_reg:" if purpose=="registration" else "pending_password_reset:")+email
                assert await master.get(key) is None
    finally:
        if created_redis:
            await master.execute_command("ACL","DELUSER",redis_user)
        if created_pg:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        if created_role:
            await admin.execute(f"DROP ROLE {pg_role}")
        await admin.close()
        await master.aclose()


@pytest.mark.parametrize("purpose",["registration","password_reset"])
def test_t07_pre_fence_historical_source_redis_positive_control(purpose):
    asyncio.run(_scenario(purpose,"pre_fence"))


@pytest.mark.parametrize("purpose",["registration","password_reset"])
def test_t07_redis_only_revoke_leaves_sql_fallback_semantic_hazard(purpose):
    asyncio.run(_scenario(purpose,"redis_only"))


@pytest.mark.parametrize("purpose",["registration","password_reset"])
def test_t07_dual_redis_sql_fence_historical_source_restart_denied(purpose):
    asyncio.run(_scenario(purpose,"dual_fence"))


if __name__=="__main__" and sys.argv[1:]==["--historical-child"]:
    try:
        print(asyncio.run(_child(json.loads(sys.stdin.read()))))
    except Exception:
        # No application secrets, DB connection strings, OTP or payload in logs.
        sys.exit(2)
