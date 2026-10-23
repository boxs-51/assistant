"""S04 T06: PostgreSQL 17 role/epoch fence *reference-only* physical contract.

Standalone disposable schema + two disposable LOGIN roles. It does not fence
legacy Redis clients or implement a production trusted external controller.
Explicitly selected in a dedicated CI job; no portable pytest collection.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def _dsn():
    raw = os.environ.get("S04_T06_PG_DSN", "")
    url = urlparse(raw)
    assert (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_T06_STRICT") == "1"
            and os.environ.get("S04_T06_DISPOSABLE") == "1"
            and url.scheme == "postgresql"
            and url.hostname == "127.0.0.1" and url.port == 5432
            and url.username == "s04_fence_admin"
            and url.path == "/s04_t06_disposable"
            and not url.password and not url.query), (
                "S04_HARNESS_ERROR: exact disposable CI PG17 identity required")
    return raw


async def _fixture(check):
    import asyncpg
    dsn = _dsn()
    admin = await asyncpg.connect(dsn)
    suffix = uuid4().hex[:18]
    schema = "s04t06_" + suffix
    old_role = "s04_old_" + suffix
    new_role = "s04_new_" + suffix
    old = modern = None
    created_schema = False
    created_roles = []
    try:
        version = int(await admin.fetchval("SHOW server_version_num"))
        ident = await admin.fetchrow(
            "SELECT current_database() AS db, current_user AS actor")
        if not (170000 <= version < 180000 and
                ident["db"] == "s04_t06_disposable" and
                ident["actor"] == "s04_fence_admin"):
            pytest.fail("S04_HARNESS_ERROR: not isolated PostgreSQL 17")
        for role in (old_role, new_role):
            await admin.execute(
                f"CREATE ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE")
            created_roles.append(role)
        await admin.execute(f"CREATE SCHEMA {schema}")
        created_schema = True
        await admin.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {old_role},{new_role}")
        await admin.execute(f"""CREATE TABLE {schema}.authority(
            id integer PRIMARY KEY CHECK (id=1),
            epoch bigint NOT NULL CHECK (epoch>=1),
            phase text NOT NULL CHECK(phase IN ('SEALED','ACTIVE','FROZEN')))""")
        await admin.execute(f"INSERT INTO {schema}.authority VALUES(1,1,'SEALED')")
        await admin.execute(f"""CREATE TABLE {schema}.legacy(
            item_id text PRIMARY KEY NOT NULL, payload text NOT NULL)""")
        await admin.execute(f"""CREATE TABLE {schema}.v2_ledger(
            item_id text PRIMARY KEY NOT NULL, issued_epoch bigint NOT NULL,
            actor text NOT NULL)""")
        await admin.execute(f"GRANT INSERT,SELECT ON {schema}.legacy TO {old_role}")
        # Modern role gets ONLY EXECUTE on a single fixed-schema entrypoint.
        await admin.execute(f"""CREATE FUNCTION {schema}.issue_v2(
              req_epoch bigint, req_id text) RETURNS text
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
            AS $body$
            DECLARE a record;
            BEGIN
              SELECT epoch,phase INTO a FROM {schema}.authority
                  WHERE id=1 FOR SHARE;
              IF a IS NULL OR a.epoch <> req_epoch OR a.phase <> 'ACTIVE' THEN
                RAISE EXCEPTION 'S04_AUTHORITY_FENCED' USING ERRCODE='P0001';
              END IF;
              INSERT INTO {schema}.v2_ledger(item_id,issued_epoch,actor)
                  VALUES(req_id,req_epoch,session_user::text);
              RETURN 'ACKED';
            END;
            $body$""")
        await admin.execute(
            f"REVOKE ALL ON FUNCTION {schema}.issue_v2(bigint,text) FROM PUBLIC")
        await admin.execute(
            f"GRANT EXECUTE ON FUNCTION {schema}.issue_v2(bigint,text) TO {new_role}")
        # A durable counter-rollback reference guard. NOT a production migration.
        await admin.execute(f"""CREATE FUNCTION {schema}.guard_epoch()
            RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog
            AS $body$
            BEGIN
              IF NEW.epoch < OLD.epoch OR
                 (OLD.phase='ACTIVE' AND NEW.phase='SEALED') OR
                 (OLD.phase='FROZEN' AND NEW.phase IN ('ACTIVE','SEALED')) THEN
                   RAISE EXCEPTION 'S04_EPOCH_ROLLBACK_DENIED' USING ERRCODE='P0001';
              END IF;
              RETURN NEW;
            END;
            $body$""")
        await admin.execute(
            f"""CREATE TRIGGER epoch_guard BEFORE UPDATE ON {schema}.authority
                FOR EACH ROW EXECUTE FUNCTION {schema}.guard_epoch()""")
        old = await asyncpg.connect(dsn, user=old_role)
        modern = await asyncpg.connect(dsn, user=new_role)
        assert await old.fetchval("SELECT current_user") == old_role
        assert await modern.fetchval("SELECT current_user") == new_role
        await check(dsn, admin, old, modern, schema, old_role, new_role)
    finally:
        for conn in (old, modern):
            if conn is not None:
                await conn.close()
        if created_schema:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        for role in reversed(created_roles):
            await admin.execute(f"DROP ROLE {role}")
        await admin.close()


async def _deny(conn, query, *args, sqlstate="42501"):
    import asyncpg
    try:
        await conn.execute(query, *args)
    except asyncpg.PostgresError as err:
        assert err.sqlstate == sqlstate, (
            f"S04_HARNESS_ERROR: expected SQLSTATE {sqlstate}, got {err.sqlstate}")
    else:
        pytest.fail(f"S04_SECURITY_RED: forbidden statement accepted ({sqlstate})")


async def _seal(admin, schema, old_role):
    # Trusted-admin *simulation* only: no real old binary/Redis ACL proof.
    await admin.execute(f"REVOKE INSERT ON {schema}.legacy FROM {old_role}")
    await admin.execute(
        f"UPDATE {schema}.authority SET phase='ACTIVE',epoch=2 WHERE id=1")


def test_t06_01_pre_seal_old_sql_write_control_and_post_seal_deny():
    async def check(dsn,a,old,modern,s,old_role,_):
        await old.execute(f"INSERT INTO {s}.legacy VALUES('before','old')")
        await _seal(a,s,old_role)
        await _deny(old,f"INSERT INTO {s}.legacy VALUES('after','old')")
        assert await a.fetchval(f"SELECT count(*) FROM {s}.legacy") == 1
    asyncio.run(_fixture(check))


def test_t06_02_only_v2_epoch_active_can_ack_issue():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _deny(modern,f"SELECT {s}.issue_v2(1,'early')",sqlstate="P0001")
        await _seal(a,s,old_role)
        assert await modern.fetchval(f"SELECT {s}.issue_v2(2,'good')") == "ACKED"
        await _deny(modern,f"SELECT {s}.issue_v2(1,'stale')",sqlstate="P0001")
        await a.execute(f"UPDATE {s}.authority SET phase='FROZEN'")
        await _deny(modern,f"SELECT {s}.issue_v2(2,'frozen')",sqlstate="P0001")
        assert await a.fetchval(f"SELECT count(*) FROM {s}.v2_ledger") == 1
    asyncio.run(_fixture(check))


def test_t06_03_old_cannot_execute_v2_modern_cannot_write_tables_directly():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        await _deny(old,f"SELECT {s}.issue_v2(2,'forged')")
        await _deny(old,f"INSERT INTO {s}.v2_ledger VALUES('forged',2,'old')")
        await _deny(modern,f"INSERT INTO {s}.v2_ledger VALUES('bypass',2,'modern')")
        await _deny(modern,f"UPDATE {s}.authority SET epoch=3")
        assert await modern.fetchval(f"SELECT {s}.issue_v2(2,'allowed')") == "ACKED"
    asyncio.run(_fixture(check))


def test_t06_04_existing_and_reconnected_old_session_both_denied():
    async def check(dsn,a,old,modern,s,old_role,_):
        import asyncpg
        await old.execute(f"INSERT INTO {s}.legacy VALUES('pre','old')")
        await _seal(a,s,old_role)
        await _deny(old,f"INSERT INTO {s}.legacy VALUES('old-pool','old')")
        reconnect = await asyncpg.connect(dsn,user=old_role)
        try:
            await _deny(reconnect,f"INSERT INTO {s}.legacy VALUES('old-restart','old')")
            await _deny(reconnect,f"SELECT {s}.issue_v2(2,'old-restart')")
        finally:
            await reconnect.close()
        assert await a.fetchval(f"SELECT count(*) FROM {s}.legacy") == 1
    asyncio.run(_fixture(check))


def test_t06_05_epoch_counter_and_phase_cannot_roll_back():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        await _deny(a,f"UPDATE {s}.authority SET epoch=1",sqlstate="P0001")
        await _deny(a,f"UPDATE {s}.authority SET phase='SEALED'",sqlstate="P0001")
        await a.execute(f"UPDATE {s}.authority SET phase='FROZEN'")
        await _deny(a,f"UPDATE {s}.authority SET phase='ACTIVE'",sqlstate="P0001")
        row = await a.fetchrow(f"SELECT epoch,phase FROM {s}.authority")
        assert row["epoch"]==2 and row["phase"]=="FROZEN"
    asyncio.run(_fixture(check))


def test_t06_06_untrusted_search_path_cannot_redirect_security_definer():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        await modern.execute("CREATE TEMP TABLE v2_ledger(item_id text,issued_epoch int,actor text)")
        await modern.execute("SET search_path=pg_temp,public")
        assert await modern.fetchval(f"SELECT {s}.issue_v2(2,'path-fixed')") == "ACKED"
        assert await a.fetchval(f"SELECT count(*) FROM {s}.v2_ledger") == 1
        assert await modern.fetchval("SELECT count(*) FROM pg_temp.v2_ledger") == 0
    asyncio.run(_fixture(check))


def test_t06_07_unique_v2_issuance_no_silent_duplicate_ack():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        assert await modern.fetchval(f"SELECT {s}.issue_v2(2,'once')")=="ACKED"
        await _deny(modern,f"SELECT {s}.issue_v2(2,'once')",sqlstate="23505")
        assert await a.fetchval(f"SELECT count(*) FROM {s}.v2_ledger")==1
    asyncio.run(_fixture(check))


def test_t06_08_fence_transition_waits_for_active_issuer_transaction():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        async with modern.transaction():
            assert await modern.fetchval(f"SELECT {s}.issue_v2(2,'in-flight')")=="ACKED"
            async with a.transaction():
                await a.execute("SET LOCAL lock_timeout = '150ms'")
                await _deny(a,f"UPDATE {s}.authority SET phase='FROZEN'",sqlstate="55P03")
        await a.execute(f"UPDATE {s}.authority SET phase='FROZEN'")
        await _deny(modern,f"SELECT {s}.issue_v2(2,'too-late')",sqlstate="P0001")
        assert await a.fetchval(f"SELECT count(*) FROM {s}.v2_ledger")==1
    asyncio.run(_fixture(check))


def test_t06_09_old_role_cannot_grant_itself_back_authority():
    async def check(dsn,a,old,modern,s,old_role,_):
        await _seal(a,s,old_role)
        # PostgreSQL may WARN ("no privileges were granted") rather than
        # raise on an unauthorized GRANT. Check effective privileges, not
        # statement completion, to reject false PASS and false RED.
        assert await a.fetchval(
            "SELECT has_table_privilege($1::name,$2::text,'INSERT')",
            old_role, f"{s}.legacy") is False
        await old.execute(f"GRANT INSERT ON {s}.legacy TO {old_role}")
        assert await a.fetchval(
            "SELECT has_table_privilege($1::name,$2::text,'INSERT')",
            old_role, f"{s}.legacy") is False, (
                "S04_SECURITY_RED: old role restored effective INSERT privilege")
        await _deny(old,f"SET ROLE s04_fence_admin")
        await _deny(old,f"INSERT INTO {s}.legacy VALUES('resurrection','old')")
        await _deny(old,f"SELECT {s}.issue_v2(2,'resurrection')")
    asyncio.run(_fixture(check))
