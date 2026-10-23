"""S04 T05-01..04: disposable PG17 harness controls, NOT production OTP evidence.

Explicit-only test file (no test_ prefix). Never treat its PASS as S04 GREEN.
All DDL/DML is restricted to a new test-owned schema in an isolated PG17 CI service.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def _admit() -> str:
    dsn = os.environ.get("S04_T05_PG_DSN", "")
    url = urlparse(dsn)
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_T05_STRICT") == "1"
            and os.environ.get("S04_T05_DISPOSABLE") == "1"
            and url.scheme == "postgresql"
            and url.hostname == "127.0.0.1"
            and url.port == 5432
            and url.username == "s04_test"
            and url.path == "/s04_t05_contract"
            and not url.password and not url.query):
        pytest.fail("S04_HARNESS_ERROR: exact ephemeral PG17 CI admission required")
    return dsn


async def _exercise(verify):
    dsn = _admit()
    try:
        import asyncpg
    except ImportError:
        pytest.fail("S04_HARNESS_ERROR: asyncpg dependency missing")
    admin = await asyncpg.connect(dsn)
    schema = "s04t05_" + uuid4().hex[:20]
    created = False
    try:
        version = int(await admin.fetchval("SHOW server_version_num"))
        db = await admin.fetchval("SELECT current_database()")
        user = await admin.fetchval("SELECT current_user")
        if not (170000 <= version < 180000
                and db == "s04_t05_contract" and user == "s04_test"):
            pytest.fail("S04_HARNESS_ERROR: disposable PG17 identity mismatch")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await admin.execute(f'''CREATE TABLE "{schema}".ops(
            op_id text PRIMARY KEY, subject text NOT NULL, fingerprint text NOT NULL)''')
        await admin.execute(f'''CREATE TABLE "{schema}".effects(
            op_id text PRIMARY KEY REFERENCES "{schema}".ops(op_id),
            subject text NOT NULL)''')
        await admin.execute(f'''CREATE TABLE "{schema}".accounts(
            account_id text PRIMARY KEY, version bigint NOT NULL DEFAULT 0)''')
        await admin.execute(f'''INSERT INTO "{schema}".accounts(account_id)
            VALUES ('A'),('B')''')
        await verify(dsn, schema)
    finally:
        try:
            if created:
                await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await admin.close()


async def _claim(conn, schema, op_id, subject, fingerprint):
    # REFERENCE HARNESS GUARD: NOT the application implementation.
    async with conn.transaction():
        new = await conn.fetchval(
            f'''INSERT INTO "{schema}".ops(op_id,subject,fingerprint)
                VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING op_id''',
            op_id, subject, fingerprint)
        if new:
            await conn.execute(f'''INSERT INTO "{schema}".effects(op_id,subject)
                VALUES($1,$2)''', op_id, subject)
            return "COMMITTED"
        old = await conn.fetchrow(
            f'SELECT subject,fingerprint FROM "{schema}".ops WHERE op_id=$1',
            op_id)
        if old is None:
            pytest.fail("S04_HARNESS_ERROR: operation claim not observable")
        return ("REPLAY" if (old["subject"], old["fingerprint"])
                == (subject, fingerprint) else "CONFLICT")


def test_t05_01_same_operation_two_subjects_physical_control():
    async def verify(dsn, schema):
        import asyncpg
        a, b = await asyncio.gather(asyncpg.connect(dsn), asyncpg.connect(dsn))
        try:
            go = asyncio.Event()
            async def run(conn, subject):
                await go.wait()
                return await _claim(conn, schema, "shared-operation", subject, "fp-" + subject)
            tasks = [asyncio.create_task(run(a, "A")), asyncio.create_task(run(b, "B"))]
            go.set()
            assert sorted(await asyncio.wait_for(asyncio.gather(*tasks), 15)) == [
                "COMMITTED", "CONFLICT"]
            assert await a.fetchval(f'SELECT count(*) FROM "{schema}".ops') == 1
            assert await a.fetchval(f'SELECT count(*) FROM "{schema}".effects') == 1
        finally:
            await a.close()
            await b.close()
    asyncio.run(_exercise(verify))


def test_t05_02_idempotency_fingerprint_mismatch_physical_control():
    async def verify(dsn, schema):
        import asyncpg
        conn = await asyncpg.connect(dsn)
        try:
            assert await _claim(conn, schema, "replay", "A", "v1") == "COMMITTED"
            assert await _claim(conn, schema, "replay", "A", "v2") == "CONFLICT"
            assert await _claim(conn, schema, "replay", "A", "v1") == "REPLAY"
            assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".effects') == 1
        finally:
            await conn.close()
    asyncio.run(_exercise(verify))


def test_t05_03_advisory_hash_collision_preserves_identity_physical_control():
    async def verify(dsn, schema):
        import asyncpg
        a, b = await asyncio.gather(asyncpg.connect(dsn), asyncpg.connect(dsn))
        try:
            gate = asyncio.Event()
            async def run(conn, op_id):
                await gate.wait()
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(40904,3)")
                    await conn.execute(f'''INSERT INTO "{schema}".ops(
                        op_id,subject,fingerprint) VALUES($1,'A',$2)''',
                        op_id, op_id)
                    await conn.execute(f'''INSERT INTO "{schema}".effects(
                        op_id,subject) VALUES($1,'A')''', op_id)
                    return op_id
            tasks = [asyncio.create_task(run(a, "left")),
                     asyncio.create_task(run(b, "right"))]
            gate.set()
            assert sorted(await asyncio.wait_for(asyncio.gather(*tasks), 15)) == [
                "left", "right"]
            assert await a.fetchval(f'SELECT count(*) FROM "{schema}".ops') == 2
            assert await a.fetchval(f'SELECT count(*) FROM "{schema}".effects') == 2
        finally:
            await a.close()
            await b.close()
    asyncio.run(_exercise(verify))


def test_t05_04_deadlock_negative_and_canonical_positive_physical_control():
    async def verify(dsn, schema):
        import asyncpg
        a, b = await asyncio.gather(asyncpg.connect(dsn), asyncpg.connect(dsn))
        try:
            gate = asyncio.Barrier(2)
            async def inverted(conn, first, second):
                try:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL statement_timeout='9s'")
                        await conn.fetchval(
                            f'''SELECT version FROM "{schema}".accounts
                                WHERE account_id=$1 FOR UPDATE''', first)
                        await gate.wait()
                        await conn.fetchval(
                            f'''SELECT version FROM "{schema}".accounts
                                WHERE account_id=$1 FOR UPDATE''', second)
                    return "COMMITTED"
                except asyncpg.PostgresError as err:
                    return err.sqlstate
            observed = await asyncio.wait_for(asyncio.gather(
                inverted(a, "A", "B"), inverted(b, "B", "A")), 18)
            assert "40P01" in observed, (
                "S04_HARNESS_ERROR: inverted-lock negative control did not deadlock")
            assert await a.fetchval(
                f'SELECT SUM(version) FROM "{schema}".accounts') == 0
            async def canonical(conn):
                async with conn.transaction():
                    return await conn.fetch(
                        f'''SELECT account_id FROM "{schema}".accounts
                            WHERE account_id IN ('A','B')
                            ORDER BY account_id FOR UPDATE''')
            await asyncio.wait_for(asyncio.gather(canonical(a), canonical(b)), 12)
        finally:
            await a.close()
            await b.close()
    asyncio.run(_exercise(verify))
