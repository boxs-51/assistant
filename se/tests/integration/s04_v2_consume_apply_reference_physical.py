"""S04 PG17 consume-and-apply transaction REFERENCE ONLY.

Non-production, explicitly selected pytest module, disposable PG17 schema. This
is NOT OTPStorageService, an Alembic migration, production admission, or a
security fix for the six existing mixed-version semantic REDs.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest

TEST_KEY = b"S04-disposable-fixture-key-NOT-FOR-PRODUCTION"


def _admit():
    dsn = os.environ.get("S04_V2_PG_DSN", "")
    u = urlparse(dsn)
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_V2_DISPOSABLE") == "1"
            and os.environ.get("S04_V2_STRICT") == "1"
            and u.scheme == "postgresql" and u.hostname == "127.0.0.1"
            and u.port == 5432 and u.username == "s04_reference"
            and u.path == "/s04_v2_disposable"
            and not u.password and not u.query):
        pytest.fail("S04_HARNESS_ERROR: isolated CI PG17 authority admission required")
    return dsn


def _mac(challenge_id, code):
    return hmac.new(TEST_KEY, (challenge_id + "|" + code).encode(), hashlib.sha256).digest()


def _fp(challenge_id, kind, subject, mark, code):
    payload = "|".join((challenge_id, kind, subject, mark, code))
    return hmac.new(TEST_KEY, payload.encode(), hashlib.sha256).digest()


async def _consume(conn, schema, *, op, challenge, kind, subject, mark,
                   code="123456", epoch=1, fail_after_effect=False):
    """Reference transaction: authority > global op guard > slot > challenge > account.

    All effects/receipt writes happen on the SAME asyncpg connection/transaction.
    If COMMIT acknowledgement is lost, caller retries SAME op or reads receipt.
    """
    digest = _fp(challenge, kind, subject, mark, code)
    async with conn.transaction(isolation="read_committed"):
        authority = await conn.fetchrow(
            f'SELECT epoch, phase FROM "{schema}".authority WHERE id=1 FOR SHARE')
        if authority is None or authority["phase"] != "ACTIVE" or authority["epoch"] != epoch:
            return "FENCED"
        # Collision may serialize unrelated IDs; identity is ALWAYS the full op PK.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", op)
        old = await conn.fetchrow(
            f'SELECT fingerprint,outcome FROM "{schema}".operations WHERE op_id=$1', op)
        if old:
            return ("REPLAY" if hmac.compare_digest(old["fingerprint"], digest)
                    else "CONFLICT")
        slot = await conn.fetchrow(
            f'''SELECT slot_id,current_id,generation,subject,kind
                FROM "{schema}".slots WHERE slot_id=$1 FOR UPDATE''', subject + "|" + kind)
        if not slot or slot["subject"] != subject or slot["kind"] != kind:
            return "DENIED"
        ch = await conn.fetchrow(
            f'''SELECT challenge_id,state,issued_epoch,mac,attempts,expires_at,
                expected_version FROM "{schema}".challenges
                WHERE challenge_id=$1 AND slot_id=$2 FOR UPDATE''',
            challenge, slot["slot_id"])
        if ch is None or slot["current_id"] != challenge or ch["state"] != "ISSUED" or ch["issued_epoch"] != epoch:
            return "DENIED"
        now = await conn.fetchval("SELECT clock_timestamp()")
        if ch["expires_at"] <= now or ch["attempts"] < 1:
            await conn.execute(
                f'''UPDATE "{schema}".challenges SET state='EXPIRED'
                    WHERE challenge_id=$1''', challenge)
            await conn.execute(
                f'UPDATE "{schema}".slots SET current_id=NULL WHERE slot_id=$1',
                slot["slot_id"])
            await conn.execute(
                f'''INSERT INTO "{schema}".operations VALUES($1,$2,$3,'DENIED')''',
                op, challenge, digest)
            return "DENIED"
        if not hmac.compare_digest(ch["mac"], _mac(challenge, code)):
            await conn.execute(
                f'''UPDATE "{schema}".challenges SET
                    attempts=attempts-1,
                    state=CASE WHEN attempts=1 THEN 'REVOKED' ELSE state END
                    WHERE challenge_id=$1''', challenge)
            if ch["attempts"] == 1:
                await conn.execute(
                    f'UPDATE "{schema}".slots SET current_id=NULL WHERE slot_id=$1',
                    slot["slot_id"])
            await conn.execute(
                f'''INSERT INTO "{schema}".operations VALUES($1,$2,$3,'DENIED')''',
                op, challenge, digest)
            return "DENIED"
        account = await conn.fetchrow(
            f'''SELECT version FROM "{schema}".accounts
                WHERE subject=$1 FOR UPDATE''', subject)
        if kind == "registration" and account is None:
            await conn.execute(
                f'''INSERT INTO "{schema}".accounts(subject,credential,version)
                    VALUES($1,$2,1)''', subject, mark)
        elif kind == "password_reset" and account and account["version"] == ch["expected_version"]:
            await conn.execute(
                f'''UPDATE "{schema}".accounts
                    SET credential=$2,version=version+1 WHERE subject=$1''', subject, mark)
        else:
            await conn.execute(
                f'''INSERT INTO "{schema}".operations VALUES($1,$2,$3,'DENIED')''',
                op, challenge, digest)
            return "DENIED"
        if fail_after_effect:
            raise RuntimeError("S04_TEST_ONLY_FAULT_AFTER_ACCOUNT_WRITE")
        await conn.execute(
            f'''UPDATE "{schema}".challenges
                SET state='CONSUMED' WHERE challenge_id=$1''', challenge)
        await conn.execute(
            f'UPDATE "{schema}".slots SET current_id=NULL WHERE slot_id=$1',
            slot["slot_id"])
        await conn.execute(
            f'''INSERT INTO "{schema}".effects(challenge_id,op_id,subject,kind)
                VALUES($1,$2,$3,$4)''', challenge, op, subject, kind)
        await conn.execute(
            f'''INSERT INTO "{schema}".operations VALUES($1,$2,$3,'EFFECT_COMMITTED')''',
            op, challenge, digest)
    return "EFFECT_COMMITTED"


async def _case(purpose, check, *, already_account=None):
    dsn = _admit()
    import asyncpg
    conn = await asyncpg.connect(dsn)
    schema = "s04v2_" + uuid4().hex[:20]
    subject = "test-" + uuid4().hex
    challenge = str(uuid4())
    created = False
    try:
        num = int(await conn.fetchval("SHOW server_version_num"))
        identity = await conn.fetchrow("SELECT current_database() AS db, current_user AS u")
        if not (170000 <= num < 180000
                and identity["db"] == "s04_v2_disposable"
                and identity["u"] == "s04_reference"):
            pytest.fail("S04_HARNESS_ERROR: wrong physical server/version/role")
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await conn.execute(f'''CREATE TABLE "{schema}".authority(
            id smallint PRIMARY KEY CHECK(id=1),epoch bigint NOT NULL,
            phase text NOT NULL CHECK(phase IN ('ACTIVE','FROZEN')))''')
        await conn.execute(f'''CREATE TABLE "{schema}".slots(
            slot_id text PRIMARY KEY,subject text NOT NULL,
            kind text NOT NULL,current_id text,generation bigint NOT NULL)''')
        await conn.execute(f'''CREATE TABLE "{schema}".challenges(
            challenge_id text PRIMARY KEY,slot_id text NOT NULL
                REFERENCES "{schema}".slots(slot_id),
            state text NOT NULL,issued_epoch bigint NOT NULL,
            mac bytea NOT NULL,attempts int NOT NULL,
            expires_at timestamptz NOT NULL,expected_version int NOT NULL)''')
        await conn.execute(f'''CREATE TABLE "{schema}".accounts(
            subject text PRIMARY KEY,credential text NOT NULL,version int NOT NULL)''')
        await conn.execute(f'''CREATE TABLE "{schema}".operations(
            op_id text PRIMARY KEY,challenge_id text NOT NULL,
            fingerprint bytea NOT NULL,outcome text NOT NULL)''')
        await conn.execute(f'''CREATE TABLE "{schema}".effects(
            challenge_id text PRIMARY KEY REFERENCES "{schema}".challenges(challenge_id),
            op_id text NOT NULL UNIQUE REFERENCES "{schema}".operations(op_id)
                DEFERRABLE INITIALLY DEFERRED,
            subject text NOT NULL,kind text NOT NULL)''')
        await conn.execute(f'INSERT INTO "{schema}".authority VALUES(1,1,$1)', "ACTIVE")
        await conn.execute(
            f'INSERT INTO "{schema}".slots VALUES($1,$2,$3,$4,1)',
            subject + "|" + purpose, subject, purpose, challenge)
        await conn.execute(
            f'''INSERT INTO "{schema}".challenges
                VALUES($1,$2,'ISSUED',1,$3,3,clock_timestamp()+interval '5 minutes',$4)''',
            challenge, subject + "|" + purpose, _mac(challenge, "123456"),
            already_account if already_account is not None else 0)
        if already_account is not None:
            await conn.execute(
                f'INSERT INTO "{schema}".accounts VALUES($1,$2,$3)',
                subject, "original", already_account)
        await check(dsn, conn, schema, subject, challenge)
    finally:
        try:
            if created:
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await conn.close()


def _request(op, ch, purpose, subject, mark="new-credential", **kw):
    return dict(op=op,challenge=ch,kind=purpose,subject=subject,mark=mark,**kw)


def test_s04_v2_registration_atomic_single_transaction():
    async def check(dsn,c,s,subject,ch):
        r=await _consume(c,s,**_request("op1",ch,"registration",subject))
        assert r=="EFFECT_COMMITTED"
        assert await c.fetchval(f'SELECT version FROM "{s}".accounts')==1
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==1
        assert await c.fetchval(f'SELECT state FROM "{s}".challenges')=="CONSUMED"
    asyncio.run(_case("registration",check))


def test_s04_v2_reset_updates_account_and_credential_version_atomically():
    async def check(dsn,c,s,subject,ch):
        assert await _consume(c,s,**_request("op2",ch,"password_reset",subject))=="EFFECT_COMMITTED"
        assert await c.fetchval(f'SELECT version FROM "{s}".accounts')==5
        assert await c.fetchval(f'SELECT credential FROM "{s}".accounts')=="new-credential"
    asyncio.run(_case("password_reset",check,already_account=4))


def test_s04_v2_two_consumers_one_effect_positive_and_denial():
    async def check(dsn,c,s,subject,ch):
        import asyncpg
        a,b=await asyncio.gather(asyncpg.connect(dsn),asyncpg.connect(dsn))
        try:
            results=await asyncio.wait_for(asyncio.gather(
                _consume(a,s,**_request("raceA",ch,"registration",subject)),
                _consume(b,s,**_request("raceB",ch,"registration",subject))),20)
            assert sorted(results)==["DENIED","EFFECT_COMMITTED"]
            assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==1
            assert await c.fetchval(f'SELECT count(*) FROM "{s}".accounts')==1
        finally:
            await a.close();await b.close()
    asyncio.run(_case("registration",check))


def test_s04_v2_global_operation_conflict_and_exact_replay():
    async def check(dsn,c,s,subject,ch):
        req=_request("shared",ch,"registration",subject)
        assert await _consume(c,s,**req)=="EFFECT_COMMITTED"
        assert await _consume(c,s,**req)=="REPLAY"
        assert await _consume(c,s,**{**req,"mark":"changed"})=="CONFLICT"
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==1
    asyncio.run(_case("registration",check))


def test_s04_v2_wrong_otp_denial_committed_no_effect_then_success():
    async def check(dsn,c,s,subject,ch):
        assert await _consume(c,s,**_request("bad",ch,"registration",subject,code="000000"))=="DENIED"
        assert await c.fetchval(f'SELECT attempts FROM "{s}".challenges')==2
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".accounts')==0
        assert await _consume(c,s,**_request("good",ch,"registration",subject))=="EFFECT_COMMITTED"
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==1
    asyncio.run(_case("registration",check))


def test_s04_v2_fault_after_account_write_rolls_back_both():
    async def check(dsn,c,s,subject,ch):
        with pytest.raises(RuntimeError,match="S04_TEST_ONLY_FAULT_AFTER_ACCOUNT_WRITE"):
            await _consume(c,s,**_request("fault",ch,"registration",subject,fail_after_effect=True))
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".accounts')==0
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".operations')==0
        assert await c.fetchval(f'SELECT state FROM "{s}".challenges')=="ISSUED"
        assert await _consume(c,s,**_request("fault",ch,"registration",subject))=="EFFECT_COMMITTED"
    asyncio.run(_case("registration",check))


def test_s04_v2_simulated_ack_loss_reconciles_same_operation():
    async def check(dsn,c,s,subject,ch):
        request=_request("acklost",ch,"password_reset",subject)
        assert await _consume(c,s,**request)=="EFFECT_COMMITTED"
        # Simulates dropped *response* AFTER confirmed commit, not real wire ACK loss.
        try:
            raise ConnectionResetError("synthetic response lost")
        except ConnectionResetError:
            assert await _consume(c,s,**request)=="REPLAY"
        assert await c.fetchval(f'SELECT version FROM "{s}".accounts')==5
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==1
    asyncio.run(_case("password_reset",check,already_account=4))


def test_s04_v2_stale_epoch_and_frozen_authority_deny_without_effect():
    async def check(dsn,c,s,subject,ch):
        await c.execute(f'UPDATE "{s}".authority SET epoch=2')
        assert await _consume(c,s,**_request("stale",ch,"registration",subject))=="FENCED"
        await c.execute(f"UPDATE \"{s}\".authority SET phase='FROZEN'")
        assert await _consume(c,s,**_request("frozen",ch,"registration",subject,epoch=2))=="FENCED"
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".operations')==0
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".accounts')==0
    asyncio.run(_case("registration",check))


def test_s04_v2_expiry_determined_after_lock_by_pg_clock():
    async def check(dsn,c,s,subject,ch):
        await c.execute(f'''UPDATE "{s}".challenges
            SET expires_at=clock_timestamp()-interval '1 second' ''')
        assert await _consume(c,s,**_request("expired",ch,"registration",subject))=="DENIED"
        assert await c.fetchval(f'SELECT state FROM "{s}".challenges')=="EXPIRED"
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".accounts')==0
    asyncio.run(_case("registration",check))


def test_s04_v2_registration_existing_account_denies_without_consume():
    async def check(dsn,c,s,subject,ch):
        assert await _consume(c,s,**_request("duplicate",ch,"registration",subject))=="DENIED"
        assert await c.fetchval(f'SELECT state FROM "{s}".challenges')=="ISSUED"
        assert await c.fetchval(f'SELECT count(*) FROM "{s}".effects')==0
    asyncio.run(_case("registration",check,already_account=4))
