"""S04 T09: original historical SQLAlchemy model/repository and OTP consumer.

Physical PG17/SQLite/Redis7.4, test-only, pinned Git blobs. Historical
session.get -> session.delete -> session.flush is unchanged. The independent
UOW, database bootstrap, Redis transport and process harness are NOT a
reproduction of the old shipped application binary or its full startup path.
"""
from __future__ import annotations

import asyncio
import builtins
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import types
from urllib.parse import urlparse
from uuid import uuid4

import pytest

HISTORIC_ROOT=Path("s04-historical/se/src")
REPO_REL="infrastructure/storage/repositories/user_data/pending_registrations.py"
MODEL_REL="infrastructure/storage/models/sql/user_data/pending_registration.py"
REPO_BLOB="59a7b24560782b3f1336cfea0bfb7f4f67010112"
MODEL_BLOB="c2a687774f0d82d15551ede43001895d321eef75"


def _pinned(relative, digest):
    data=(HISTORIC_ROOT/relative).read_bytes()
    gitobj=b"blob "+str(len(data)).encode()+b"\x00"+data
    assert hashlib.sha1(gitobj).hexdigest()==digest, (
        "S04_HARNESS_ERROR: original historical Git blob not preserved")
    return data


def _historic_classes():
    from sqlalchemy.orm import declarative_base
    original_import=builtins.__import__
    Base=declarative_base()
    quiet=types.SimpleNamespace(
        info=lambda *a,**k:None,warning=lambda *a,**k:None,
        error=lambda *a,**k:None)
    def shim(name,globals=None,locals=None,fromlist=(),level=0):
        if level==0 and name=="structlog":
            return types.SimpleNamespace(get_logger=lambda *a,**k:quiet)
        if level==2 and name=="base":
            return types.SimpleNamespace(Base=Base)
        if level==3 and name=="interfaces.repository":
            return types.SimpleNamespace(BaseRepository=object)
        if level==3 and name=="interfaces.database":
            return types.SimpleNamespace(DatabaseDriver=object)
        if level==3 and name=="models.sql.user_data.pending_registration":
            return types.SimpleNamespace(PendingRegistration=Model)
        return original_import(name,globals,locals,fromlist,level)
    envbase={"__builtins__":{**vars(builtins),"__import__":shim}}
    model_env={**envbase,"__name__":"s04_historical_model",
        "__package__":"se.src.infrastructure.storage.models.sql.user_data"}
    exec(compile(_pinned(MODEL_REL,MODEL_BLOB),str(HISTORIC_ROOT/MODEL_REL),"exec"),
         model_env)
    Model=model_env["PendingRegistration"]
    repo_env={**envbase,"__name__":"s04_historical_repository",
        "__package__":"se.src.infrastructure.storage.repositories.user_data"}
    exec(compile(_pinned(REPO_REL,REPO_BLOB),str(HISTORIC_ROOT/REPO_REL),"exec"),
         repo_env)
    Repo=repo_env["PendingRegistrationRepository"]
    assert {"get_and_delete","create_or_update"}.issubset(set(dir(Repo)))
    return Base,Model,Repo


def _old_otp():
    p=Path(__file__).with_name("s04_t07_historical_source_dual_fence_physical.py")
    spec=importlib.util.spec_from_file_location("s04_t07_for_t09",p)
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._verify_history()
    return mod._original_class()


def _admit():
    raw=os.environ.get("S04_T09_PG_DSN","")
    u=urlparse(raw)
    assert (os.environ.get("GITHUB_ACTIONS")=="true"
        and os.environ.get("S04_T09_STRICT")=="1"
        and os.environ.get("S04_T09_DISPOSABLE")=="1"
        and u.scheme=="postgresql" and u.hostname=="127.0.0.1"
        and u.port==5432 and u.username=="s04_t09_admin"
        and u.path=="/s04_t09_disposable"
        and not u.password and not u.query), (
            "S04_HARNESS_ERROR: isolated CI PG17 only")
    return raw


def _payload(purpose,via):
    return json.dumps({
        "otp":"123456","purpose":purpose,"user_data":{"via":via},
        "created_at":"2026-10-22T00:00:00+00:00"})


async def _consume_child(r):
    import redis.asyncio as redis
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlalchemy.exc import DBAPIError

    Base,Model,Repo=_historic_classes()
    Service=_old_otp()
    dbtype=r["dbtype"]
    assert r["purpose"] in ("registration","password_reset")
    assert r["email"].endswith("@example.invalid")
    if dbtype=="pg":
        schema=r["schema"]
        role=r["role"]
        assert schema.startswith("s04t09_") and role.startswith("s04t09old_")
        u=urlparse(_admit())
        url=f"postgresql+asyncpg://{role}@127.0.0.1:5432{u.path}"
        engine=create_async_engine(url,poolclass=NullPool,
                  connect_args={"server_settings":{"search_path":schema}})
    elif dbtype=="sqlite":
        database=Path(r["sqlite_file"]).resolve()
        assert database.is_file() and database.name=="s04_t09_fixture.sqlite3"
        assert "s04-t09-" in str(database.parent)
        engine=create_async_engine("sqlite+aiosqlite:///"+str(database),
                                   poolclass=NullPool)
    else:
        raise AssertionError("S04_HARNESS_ERROR: DB kind out of scope")
    factory=async_sessionmaker(engine,expire_on_commit=False)
    client=redis.Redis(host="127.0.0.1",port=6379,
                       username=r["redis_user"],password=r["redis_password"],
                       decode_responses=True)

    class Cache:
        async def get(self,key):
            return await client.get(key)
        async def delete(self,key):
            return await client.delete(key)
    class UOW:
        async def __aenter__(self):
            self.session=factory()
            await self.session.__aenter__()
            self.pending_registrations=Repo(self.session)
            return self
        async def commit(self):
            await self.session.commit()
        async def __aexit__(self,*args):
            await self.session.__aexit__(*args)

    svc=Service(Cache(),UOW)
    try:
        try:
            if r["purpose"]=="registration":
                result=await svc.verify_and_get_data(r["email"],"123456")
            else:
                result=await svc.verify_password_reset(r["email"],"123456")
        except DBAPIError as err:
            if dbtype=="pg":
                code=getattr(err.orig,"sqlstate",None)
                if code=="42501":
                    return "PG_DELETE_FENCED"
            if dbtype=="sqlite" and "S04_SQLITE_DELETE_FENCED" in str(err.orig):
                return "SQLITE_DELETE_FENCED"
            # Different DB errors are not equivalent to an authorization denial.
            raise
        if result is None:
            return "NOT_REDEEMED"
        assert result in ({"via":"redis"},{"via":"sql"})
        return "REDEEMED_"+result["via"].upper()
    finally:
        await client.aclose()
        await engine.dispose()


def _run_child(r):
    p=subprocess.run([sys.executable,__file__,"--consumer-child"],
         input=json.dumps(r),capture_output=True,text=True,timeout=30,
         env=dict(os.environ))
    assert p.returncode==0, (
        "S04_HARNESS_ERROR: original SQLAlchemy child failed; "
        "stderr/credentials intentionally withheld")
    receipt=p.stdout.strip()
    assert receipt in {"REDEEMED_REDIS","REDEEMED_SQL","PG_DELETE_FENCED",
                       "SQLITE_DELETE_FENCED","NOT_REDEEMED"}, (
        "S04_HARNESS_ERROR: unrecognized child receipt")
    return receipt


async def _test_case(purpose,mode):
    import asyncpg
    import redis.asyncio as redis
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import insert,text as sqltext

    dsn=_admit()
    Base,Model,Repo=_historic_classes()
    _old_otp()
    admin=await asyncpg.connect(dsn)
    cache=redis.Redis(host="127.0.0.1",port=6379,decode_responses=True)
    suffix=uuid4().hex[:18]
    schema="s04t09_"+suffix
    role="s04t09old_"+suffix
    redis_user="s04t09redis_"+suffix
    pw=secrets.token_hex(18)
    email="s04-t09-"+suffix+"@example.invalid"
    key=("pending_reg:" if purpose=="registration"
         else "pending_password_reset:")+email
    created_role=created_schema=created_redis=False
    sqlite_temp=None
    sqlite_engine=None
    try:
        who=await admin.fetchrow("SELECT current_database() AS db,current_user AS usr")
        version=int(await admin.fetchval("SHOW server_version_num"))
        assert (170000<=version<180000 and who["db"]=="s04_t09_disposable"
                and who["usr"]=="s04_t09_admin")
        assert await cache.ping()
        await cache.execute_command(
           "ACL","SETUSER",redis_user,"reset","on",">"+pw,
           "~pending_reg:*","~pending_password_reset:*","+get","+del","+ping")
        created_redis=True
        req={"dbtype":"pg" if mode.startswith("pg_") else "sqlite",
             "schema":schema,"role":role,"redis_user":redis_user,
             "redis_password":pw,"purpose":purpose,"email":email}
        if mode.startswith("pg_"):
            await admin.execute(
                f"CREATE ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE")
            created_role=True
            await admin.execute(f"CREATE SCHEMA {schema}")
            created_schema=True
            await admin.execute(f"""CREATE TABLE "{schema}".pending_registrations(
                email varchar(255) PRIMARY KEY,payload text NOT NULL,
                expires_at timestamp NOT NULL,created_at timestamp NOT NULL)""")
            await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}')
            await admin.execute(
                f'GRANT SELECT,DELETE ON "{schema}".pending_registrations TO {role}')
            await admin.execute(
                f'''INSERT INTO "{schema}".pending_registrations
                   VALUES($1,$2,clock_timestamp()+interval '5 minutes',clock_timestamp())''',
                email,_payload(purpose,"sql"))
        else:
            sqlite_temp=tempfile.TemporaryDirectory(prefix="s04-t09-")
            file=Path(sqlite_temp.name)/"s04_t09_fixture.sqlite3"
            sqlite_engine=create_async_engine("sqlite+aiosqlite:///"+str(file),
                                              poolclass=NullPool)
            async with sqlite_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(insert(Model).values(
                    email=email,payload=_payload(purpose,"sql"),
                    expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
                    created_at=datetime.now(timezone.utc)))
            req["sqlite_file"]=str(file)
            if mode=="sqlite_guard":
                async with sqlite_engine.begin() as conn:
                    await conn.execute(sqltext(
                      """CREATE TRIGGER s04_t09_delete_fence
                         BEFORE DELETE ON pending_registrations
                         BEGIN SELECT RAISE(ABORT,'S04_SQLITE_DELETE_FENCED'); END"""))
        await cache.set(key,_payload(purpose,"redis"),ex=300)

        if mode!="pg_redis_positive" and mode!="pg_sql_revoked_redis_open":
            denied="del" if "del" in mode else "get"
            await cache.execute_command("ACL","SETUSER",redis_user,"-"+denied)
        if mode in ("pg_get_fenced","pg_del_fenced","pg_sql_revoked_redis_open"):
            await admin.execute(
                f'REVOKE DELETE ON "{schema}".pending_registrations FROM {role}')
            assert await admin.fetchval(
                "SELECT has_table_privilege($1::name,$2::text,'DELETE')",
                role,f"{schema}.pending_registrations") is False

        first=await asyncio.to_thread(_run_child,req)
        if mode in ("pg_get_open","pg_del_open","sqlite_open"):
            assert first=="REDEEMED_SQL", (
                "S04_HARNESS_ERROR: original SQLAlchemy fallback missing")
            assert await cache.get(key)==_payload(purpose,"redis")
            if mode.startswith("pg_"):
                assert await admin.fetchval(
                    f'SELECT count(*) FROM "{schema}".pending_registrations')==0
            else:
                async with sqlite_engine.connect() as c:
                    n=(await c.execute(sqltext(
                        "SELECT count(*) FROM pending_registrations"))).scalar()
                    assert n==0
        elif mode in ("pg_get_fenced","pg_del_fenced","sqlite_guard"):
            expected=("PG_DELETE_FENCED" if mode.startswith("pg_")
                      else "SQLITE_DELETE_FENCED")
            assert first==expected, (
                "S04_SECURITY_RED: historical SQLAlchemy delete bypassed write fence")
            assert await asyncio.to_thread(_run_child,req)==expected, (
                "S04_SECURITY_RED: old process restart bypassed revoked authority")
            assert await cache.get(key)==_payload(purpose,"redis")
            if mode.startswith("pg_"):
                assert await admin.fetchval(
                    f'SELECT count(*) FROM "{schema}".pending_registrations')==1
            else:
                async with sqlite_engine.connect() as c:
                    assert (await c.execute(sqltext(
                        "SELECT count(*) FROM pending_registrations"))).scalar()==1
        else:
            assert first=="REDEEMED_REDIS", (
                "S04_HARNESS_ERROR: old Redis-only consumption positive absent")
            assert await cache.get(key) is None
            assert await admin.fetchval(
                f'SELECT count(*) FROM "{schema}".pending_registrations')==1
        # Isolated fixtures leave no real accounts, passwords or token side effects.
    finally:
        if created_redis:
            await cache.execute_command("ACL","DELUSER",redis_user)
        await cache.delete(key)
        if sqlite_engine:
            await sqlite_engine.dispose()
        if sqlite_temp:
            sqlite_temp.cleanup()
        if created_schema:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if created_role:
            await admin.execute(f"DROP ROLE {role}")
        await cache.aclose()
        await admin.close()


@pytest.mark.parametrize("purpose",["registration","password_reset"])
@pytest.mark.parametrize("mode",[
    "pg_redis_positive",
    "pg_get_open",
    "pg_del_open",
    "pg_get_fenced",
    "pg_del_fenced",
    "pg_sql_revoked_redis_open",
    "sqlite_open",
    "sqlite_guard",
])
def test_t09_historical_sqlalchemy_otp_redeem_fence(purpose,mode):
    asyncio.run(_test_case(purpose,mode))


if __name__=="__main__" and sys.argv[1:]==["--consumer-child"]:
    try:
        print(asyncio.run(_consume_child(json.loads(sys.stdin.read()))))
    except Exception:
        # No fixture credentials, SQL/Redis payload or traceback escape.
        sys.exit(2)
