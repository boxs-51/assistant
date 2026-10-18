"""AE359 PG17 T1 RED-first physical OTP DB-consume oracles.

TEST ONLY. No production source/schema/CI changes. All records are synthetic.
PG08-P (physical TCP COMMIT-ACK loss), distributed Redis/DB generation and
production admission are explicitly outside this file.

Run in dedicated disposable PostgreSQL 17 with:
  AE359_PG17_STRICT=1
  AE359_PG17_TEST_DATABASE_URL=postgresql+asyncpg://...@127.0.0.1/ae359_otp_t1
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.models.sql.user_data.pending_registration import PendingRegistration
from se.src.infrastructure.storage.repositories.user_data.pending_registrations import PendingRegistrationRepository
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService

pytestmark = [pytest.mark.asyncio]


class HarnessError(AssertionError):
    pass


class SyntheticAckLoss(RuntimeError):
    pass


class ForcedRollback(RuntimeError):
    pass


@dataclass
class Trace:
    events: list[tuple[str, str]] = field(default_factory=list)

    def add(self, attempt: str, name: str):
        self.events.append((attempt, name))

    def saw(self, attempt: str, name: str) -> bool:
        return (attempt, name) in self.events


class Driver:
    def __init__(self, maker):
        self.maker = maker

    def get_session(self):
        return self.maker()


class ProbeUOW(SqlAlchemyUnitOfWork):
    """Test-only boundary; delegates physical commit to actual production UOW."""

    def __init__(self, driver, trace, attempt, *, reached=None, release=None,
                 rollback=False, synthetic_loss=False, invalid_fk=False):
        super().__init__(driver)
        self.trace, self.attempt = trace, attempt
        self.reached, self.release = reached, release
        self.rollback_instead = rollback
        self.synthetic_loss = synthetic_loss
        self.invalid_fk = invalid_fk
        self.pid = None
        self.ready = asyncio.Event()

    async def __aenter__(self):
        await super().__aenter__()
        self.pid = (await self.session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        self.trace.add(self.attempt, "SESSION_READY")
        self.ready.set()
        return self

    async def commit(self):
        self.trace.add(self.attempt, "COMMIT_ENTER")
        # Transaction-local synthetic receipt must be committed/rolled back WITH OTP DELETE.
        await self.session.execute(
            text("INSERT INTO ae359_t1_receipts(attempt_id) VALUES (:id)"),
            {"id": self.attempt},
        )
        if self.invalid_fk:
            await self.session.execute(
                text("INSERT INTO ae359_t1_fk_child(id, parent_id) VALUES (:id,:missing)"),
                {"id": self.attempt, "missing": "missing-parent-" + self.attempt},
            )
        if self.reached is not None:
            self.reached.set()
        if self.release is not None:
            await asyncio.wait_for(self.release.wait(), timeout=8)
        if self.rollback_instead:
            self.trace.add(self.attempt, "FORCED_ROLLBACK")
            raise ForcedRollback("Test-only rollback before COMMIT")
        try:
            await super().commit()  # includes production pre-commit event factory
        except BaseException:
            self.trace.add(self.attempt, "REAL_UOW_COMMIT_ERROR")
            raise
        self.trace.add(self.attempt, "REAL_UOW_COMMIT_RETURNED")
        if self.synthetic_loss:
            self.trace.add(self.attempt, "SYNTHETIC_POSTCOMMIT_FAULT")
            raise SyntheticAckLoss("Test-only failure AFTER PostgreSQL commit")
        self.trace.add(self.attempt, "UOW_RETURN_TO_SERVICE_BOUNDARY")


@dataclass
class Outcome:
    status: str
    exception: BaseException | None = None
    warning_names: tuple[str, ...] = ()


def sqlstate(exc: BaseException) -> str | None:
    visited = set()
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        v = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if v:
            return str(v)
        exc = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
    return None


def categorize(a: Outcome, b: Outcome) -> str:
    if a.status == "SUCCESS" and b.status == "SUCCESS":
        return "SEMANTIC_RED_DUPLICATE"
    if {a.status, b.status} == {"SUCCESS", "DENIED"}:
        return "GREEN"
    if "ERROR" in (a.status, b.status):
        return "FAIL_CLOSED_COMPAT_FAILURE"
    return "SEMANTIC_RED_WINNER_MISMATCH"


def commit_before_service_success(events, attempt):
    names = [name for aid, name in events if aid == attempt]
    if "SERVICE_AUTH_SUCCESS" not in names:
        return True
    return (
        "REAL_UOW_COMMIT_RETURNED" in names
        and names.index("REAL_UOW_COMMIT_RETURNED") < names.index("SERVICE_AUTH_SUCCESS")
    )


def preserves_backend_failure(outcome, expected_sqlstate):
    return outcome.status == "ERROR" and sqlstate(outcome.exception) == expected_sqlstate


def expiry_ack_allowed(expires_at, db_linearization_clock, outcome):
    return outcome.status != "SUCCESS" or expires_at > db_linearization_clock


def valid_pg_target_lock(activity, owner_pid):
    if not activity:
        return False
    q = (activity.get("query") or "").upper()
    return (
        activity.get("wait_event_type") == "Lock"
        and owner_pid in (activity.get("blockers") or [])
        and "DELETE" in q and "PENDING_REGISTRATIONS" in q
    )


def independent_session_gate(session_a, session_b, pid_a, pid_b):
    return session_a is not session_b and pid_a != pid_b


async def observed_verify(factory, email, otp, trace, attempt, *, purpose="registration"):
    """Observe completed service outcome without process-global warning hooks.

    SQLAlchemy warnings for concurrently running workers are captured in
    one enclosing scope by the coordinator, not one context per async task.
    """
    service = OTPStorageService(None, factory)
    try:
        if purpose == "registration":
            result = await service.verify_and_get_data(email, otp)
        else:
            result = await service.verify_password_reset(email, otp)
        status = "SUCCESS" if result is not None else "DENIED"
        trace.add(attempt, "SERVICE_AUTH_" + status)
        return Outcome(status)
    except Exception as exc:
        trace.add(attempt, "SERVICE_AUTH_ERROR")
        return Outcome("ERROR", exception=exc)

class PgHarness:
    def __init__(self, engine):
        self.engine = engine
        self.maker = async_sessionmaker(engine, expire_on_commit=False)
        self.driver = Driver(self.maker)
        self.trace = Trace()

    def email(self):
        return f"ae359-{uuid.uuid4().hex}@example.invalid"

    def attempt(self):
        return uuid.uuid4().hex

    async def seed(self, email, ttl=60, purpose="registration", expires=None):
        payload = json.dumps({"purpose": purpose, "otp": "123456", "user_data": {"id": "synthetic"}})
        async with self.maker() as session:
            db_now = (await session.execute(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'"))).scalar_one()
            exp = expires if expires is not None else db_now + timedelta(seconds=ttl)
            session.add(PendingRegistration(email=email, payload=payload, expires_at=exp, created_at=db_now))
            await session.commit()
        return exp

    async def row_count(self, email):
        async with self.maker() as session:
            return (await session.execute(text(
                "SELECT count(*) FROM pending_registrations WHERE email=:e"
            ), {"e": email})).scalar_one()

    async def receipt_count(self, attempt):
        async with self.maker() as session:
            return (await session.execute(text(
                "SELECT count(*) FROM ae359_t1_receipts WHERE attempt_id=:id"
            ), {"id": attempt})).scalar_one()

    async def now(self):
        async with self.engine.connect() as conn:
            return (await conn.execute(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'"))).scalar_one()

    async def lock_observer(self, blocked_pid, owner_pid, *, deadline=5):
        """Independent AUTOCOMMIT observer: target DELETE, Lock, blocker owner."""
        end = time.monotonic() + deadline
        async with self.engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            while time.monotonic() < end:
                await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
                rec = (await conn.execute(text("""
                    SELECT pid, wait_event_type, query, pg_blocking_pids(pid) AS blockers
                    FROM pg_stat_activity WHERE pid=:pid
                """), {"pid": blocked_pid})).mappings().one_or_none()
                if valid_pg_target_lock(rec, owner_pid):
                    return True
                await asyncio.sleep(0.04)  # polling only, not lock evidence
        raise HarnessError("No attested target DELETE lock wait")

    def uow_factory(self, attempt, **kwargs):
        return lambda: ProbeUOW(self.driver, self.trace, attempt, **kwargs)


@pytest_asyncio.fixture
async def pg17():
    dsn = os.environ.get("AE359_PG17_TEST_DATABASE_URL", "")
    strict = os.environ.get("AE359_PG17_STRICT") == "1"
    if not dsn:
        if strict:
            pytest.fail("AE359_PG17_TEST_DATABASE_URL is required in strict PG17 CI")
        pytest.skip("Real PG17 opt-in: use AE359_PG17_TEST_DATABASE_URL")
    u = make_url(dsn)
    if u.drivername != "postgresql+asyncpg" or u.database != "ae359_otp_t1" or u.host not in ("127.0.0.1", "localhost"):
        pytest.fail("PG17 security tests require disposable localhost database ae359_otp_t1")
    engine = create_async_engine(dsn, pool_size=6, max_overflow=2, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            ver = int((await conn.execute(text("SELECT current_setting('server_version_num')"))).scalar_one())
            isolation = (await conn.execute(text("SHOW transaction_isolation"))).scalar_one()
            assert 170000 <= ver < 180000, "PostgreSQL 17 required"
            assert isolation == "read committed", "PG17 oracle is READ COMMITTED only"
        async with engine.begin() as conn:
            await conn.run_sync(PendingRegistration.__table__.create, checkfirst=True)
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ae359_t1_receipts
                (attempt_id TEXT PRIMARY KEY, committed_at TIMESTAMPTZ DEFAULT clock_timestamp())
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ae359_t1_fk_parent (id TEXT PRIMARY KEY)
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ae359_t1_fk_child (
                    id TEXT PRIMARY KEY, parent_id TEXT NOT NULL,
                    CONSTRAINT ae359_t1_deferred_fk FOREIGN KEY (parent_id)
                    REFERENCES ae359_t1_fk_parent(id) DEFERRABLE INITIALLY DEFERRED
                )
            """))
        yield PgHarness(engine)
    finally:
        # This database name is dedicated to destructive ephemeral T1 tests.
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS ae359_t1_fk_child"))
                await conn.execute(text("DROP TABLE IF EXISTS ae359_t1_fk_parent"))
                await conn.execute(text("DROP TABLE IF EXISTS ae359_t1_receipts"))
                await conn.run_sync(PendingRegistration.__table__.drop, checkfirst=True)
        finally:
            await engine.dispose()


async def run_contended(pg17, *, rollback_a=False, ttl=60):
    email = pg17.email()
    expiry = await pg17.seed(email, ttl=ttl)
    a_id, b_id = pg17.attempt(), pg17.attempt()
    reached, release = asyncio.Event(), asyncio.Event()
    factory_a = pg17.uow_factory(a_id, reached=reached, release=release, rollback=rollback_a)
    factory_b = pg17.uow_factory(b_id)
    a_uow, b_uow = factory_a(), factory_b()
    # Reuse exact UOW once only via callables: not a shared AsyncSession.
    task_a = task_b = None
    try:
        task_a = asyncio.create_task(observed_verify(lambda: a_uow, email, "123456", pg17.trace, a_id))
        await asyncio.wait_for(reached.wait(), timeout=5)
        task_b = asyncio.create_task(observed_verify(lambda: b_uow, email, "123456", pg17.trace, b_id))
        await asyncio.wait_for(b_uow.ready.wait(), timeout=5)
        await pg17.lock_observer(b_uow.pid, a_uow.pid, deadline=5)
        release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=10)
        return email, expiry, (a_id, b_id), outcomes
    finally:
        release.set()
        pending = [task for task in (task_a, task_b) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)


async def test_pg01_two_real_sessions_one_committed_service_winner(pg17):
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always", SAWarning)
        email, _, (a, b), (left, right) = await run_contended(pg17)
    orm_warnings = tuple(type(w.message).__name__ for w in seen
                         if isinstance(w.message, SAWarning))
    classification = categorize(left, right)
    assert classification == "GREEN", (
        f"PG01 classification={classification} statuses={left.status}/{right.status} "
        f"ORM warnings={orm_warnings}"
    )
    assert await pg17.row_count(email) == 0
    assert await pg17.receipt_count(a) == 1 and await pg17.receipt_count(b) == 1


async def test_pg02_rollback_first_consumer_second_becomes_winner(pg17):
    email, _, (a, b), (left, right) = await run_contended(pg17, rollback_a=True)
    assert left.status == "ERROR" and isinstance(left.exception, ForcedRollback)
    assert right.status == "SUCCESS"
    assert await pg17.receipt_count(a) == 0
    assert await pg17.receipt_count(b) == 1
    assert await pg17.row_count(email) == 0


async def test_pg03a_lock_is_physically_attested(pg17):
    email, _, _, outcomes = await run_contended(pg17)
    assert len(outcomes) == 2
    assert await pg17.row_count(email) == 0


async def test_pg03b_no_service_success_before_commit(pg17):
    email = pg17.email()
    await pg17.seed(email)
    aid = pg17.attempt()
    entered, release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(observed_verify(
        pg17.uow_factory(aid, reached=entered, release=release),
        email, "123456", pg17.trace, aid,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done() and not pg17.trace.saw(aid, "SERVICE_AUTH_SUCCESS")
        assert await pg17.receipt_count(aid) == 0  # precommit invisible to independent conn
        release.set()
        outcome = await asyncio.wait_for(task, timeout=5)
        assert outcome.status == "SUCCESS"
        assert pg17.trace.saw(aid, "REAL_UOW_COMMIT_RETURNED")
        assert commit_before_service_success(pg17.trace.events, aid)
        assert await pg17.receipt_count(aid) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_pg04_lock_timeout_does_not_turn_into_missing(pg17):
    email = pg17.email()
    await pg17.seed(email)
    aid = pg17.attempt()
    async with pg17.maker() as locker:
        async with locker.begin():
            await locker.execute(select(PendingRegistration.email).where(PendingRegistration.email == email).with_for_update())

            class ShortTimeoutUOW(ProbeUOW):
                async def __aenter__(self):
                    await super().__aenter__()
                    await self.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
                    return self

            factory = lambda: ShortTimeoutUOW(pg17.driver, pg17.trace, aid)
            result = await asyncio.wait_for(observed_verify(factory, email, "123456", pg17.trace, aid), timeout=5)
            assert preserves_backend_failure(result, "55P03")
            assert not pg17.trace.saw(aid, "SERVICE_AUTH_SUCCESS")
        assert await pg17.row_count(email) == 1


async def test_pg04_deadlock_victim_uses_real_otp_repository(pg17):
    left, right = pg17.email(), pg17.email()
    await pg17.seed(left)
    await pg17.seed(right)
    ready_a, ready_b, fire = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def participant(own, other, ready):
        async with pg17.maker() as session:
            await session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                await session.execute(select(PendingRegistration.email).where(PendingRegistration.email == own).with_for_update())
                ready.set()
                await asyncio.wait_for(fire.wait(), timeout=5)
                row = await PendingRegistrationRepository(session).get_and_delete(other)
                await session.commit()
                return ("COMMITTED", bool(row))
            except Exception as e:
                await session.rollback()
                return ("ERROR", sqlstate(e))

    a = asyncio.create_task(participant(left, right, ready_a))
    b = asyncio.create_task(participant(right, left, ready_b))
    try:
        await asyncio.wait_for(asyncio.gather(ready_a.wait(), ready_b.wait()), timeout=5)
        fire.set()
        out = await asyncio.wait_for(asyncio.gather(a, b), timeout=14)
        assert sum(x[0] == "COMMITTED" for x in out) == 1
        assert ("ERROR", "40P01") in out, f"Expected physical PG deadlock victim, saw {out!r}"
    finally:
        fire.set()
        for t in (a, b):
            if not t.done():
                t.cancel()
        await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.parametrize("ttl,expected", [(-30, "DENIED"), (60, "SUCCESS")])
async def test_pg05_expired_and_live_positive_control(pg17, ttl, expected):
    email = pg17.email()
    await pg17.seed(email, ttl=ttl)
    attempt = pg17.attempt()
    out = await observed_verify(pg17.uow_factory(attempt), email, "123456", pg17.trace, attempt)
    assert out.status == expected
    assert await pg17.row_count(email) == 0


async def test_pg05_timezone_is_utc_naive_under_test_contract(pg17):
    email = pg17.email()
    await pg17.seed(email)
    async with pg17.maker() as s:
        await s.execute(text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
        row = await s.get(PendingRegistration, email)
        assert row is not None and row.expires_at.tzinfo is None
    aid = pg17.attempt()
    assert (await observed_verify(pg17.uow_factory(aid), email, "123456", pg17.trace, aid)).status == "SUCCESS"


async def test_pg06_expiry_during_physical_row_lock_wait(pg17):
    email = pg17.email()
    expiry = await pg17.seed(email, ttl=2)
    b_id = pg17.attempt()
    b_uow = pg17.uow_factory(b_id)()
    task_b = None
    try:
        async with pg17.maker() as locker:
            async with locker.begin():
                owner_pid = (await locker.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                await locker.execute(select(PendingRegistration.email).where(PendingRegistration.email == email).with_for_update())
                task_b = asyncio.create_task(observed_verify(lambda: b_uow, email, "123456", pg17.trace, b_id))
                await asyncio.wait_for(b_uow.ready.wait(), timeout=5)
                await pg17.lock_observer(b_uow.pid, owner_pid)
                while await pg17.now() <= expiry + timedelta(milliseconds=250):
                    await asyncio.sleep(0.04)
            # lock released after this transaction's COMMIT
        out = await asyncio.wait_for(task_b, timeout=6)
        assert out.status == "DENIED", f"PG06 stale TTL: {out.status}"
        assert expiry_ack_allowed(expiry, await pg17.now(), out)
        assert await pg17.row_count(email) == 0
    finally:
        if task_b is not None and not task_b.done():
            task_b.cancel()
            await asyncio.gather(task_b, return_exceptions=True)


async def test_pg07_real_deferred_fk_commit_failure_blocks_service_ack(pg17):
    email = pg17.email()
    await pg17.seed(email)
    aid = pg17.attempt()
    outcome = await observed_verify(pg17.uow_factory(aid, invalid_fk=True), email, "123456", pg17.trace, aid)
    assert outcome.status == "ERROR" and sqlstate(outcome.exception) == "23503"
    assert not pg17.trace.saw(aid, "REAL_UOW_COMMIT_RETURNED")
    assert not pg17.trace.saw(aid, "SERVICE_AUTH_SUCCESS")
    assert await pg17.row_count(email) == 1
    assert await pg17.receipt_count(aid) == 0


async def test_pg08s_real_commit_synthetic_postcommit_ack_loss(pg17):
    email = pg17.email()
    await pg17.seed(email)
    aid = pg17.attempt()
    outcome = await observed_verify(pg17.uow_factory(aid, synthetic_loss=True), email, "123456", pg17.trace, aid)
    assert outcome.status == "ERROR" and isinstance(outcome.exception, SyntheticAckLoss)
    assert pg17.trace.saw(aid, "REAL_UOW_COMMIT_RETURNED")
    assert pg17.trace.saw(aid, "SYNTHETIC_POSTCOMMIT_FAULT")
    assert not pg17.trace.saw(aid, "SERVICE_AUTH_SUCCESS")
    assert await pg17.receipt_count(aid) == 1  # actual committed PG transaction
    assert await pg17.row_count(email) == 0


async def test_pg09_two_pg_backends_and_lock_observer_gate(pg17):
    async with pg17.maker() as a, pg17.maker() as b:
        pid_a = (await a.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        pid_b = (await b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert independent_session_gate(a, b, pid_a, pid_b)
        isolation_a = (await a.execute(text("SHOW transaction_isolation"))).scalar_one()
        isolation_b = (await b.execute(text("SHOW transaction_isolation"))).scalar_one()
        assert isolation_a == isolation_b == "read committed"
    # Physical lock observer is also exercised by PG03-A; do not fake it here.


# These mutation checks use the same semantic classifiers as production oracles.
# They do not modify production implementations or manufacture successful CI results.
async def test_m02_early_ack_mutation_is_caught_by_shared_checker(pg17):
    """Physical provisional DELETE, premature ACK and real ROLLBACK."""
    email = pg17.email()
    await pg17.seed(email)
    aid = pg17.attempt()
    release = asyncio.Event()
    reached = asyncio.Event()

    async def mutant():
        async with pg17.maker() as session:
            try:
                row = await PendingRegistrationRepository(session).get_and_delete(email)
                assert row is not None, "Mutant never reached provisional DELETE"
                pg17.trace.add(aid, "SERVICE_AUTH_SUCCESS")
                reached.set()
                await asyncio.wait_for(release.wait(), timeout=5)
                await session.rollback()
            finally:
                release.set()

    task = asyncio.create_task(mutant())
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        assert not commit_before_service_success(pg17.trace.events, aid)
        assert await pg17.row_count(email) == 1
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)
    assert await pg17.row_count(email) == 1

async def test_m05_fake_lock_observer_cannot_satisfy_attestation(pg17):
    """Plausible forged Python lock packet must fail independent PG check."""
    async with pg17.maker() as a, pg17.maker() as b:
        pid_a = (await a.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        pid_b = (await b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert pid_a != pid_b
        forged = {"query": "DELETE FROM pending_registrations WHERE email=$1",
                  "wait_event_type": "Lock", "blockers": [pid_a]}
        assert valid_pg_target_lock(forged, pid_a)  # raw dictionary can lie
        async with pg17.engine.connect() as observer:
            actual = (await observer.execute(text("""
                SELECT wait_event_type, query, pg_blocking_pids(pid) AS blockers
                FROM pg_stat_activity WHERE pid=:pid
            """), {"pid": pid_b})).mappings().one()
        assert not valid_pg_target_lock(actual, pid_a), (
            "Independent physical observation accepted a fabricated row lock"
        )

async def test_m06_shared_asyncsession_is_invalid_harness(pg17):
    """Real AsyncSession identity gate rejects shared-session mutant."""
    async with pg17.maker() as a, pg17.maker() as b:
        pid_a = (await a.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        pid_b = (await b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert independent_session_gate(a, b, pid_a, pid_b)
        worker_a_session = a
        worker_b_session = a
        assert worker_a_session is worker_b_session
        assert not independent_session_gate(worker_a_session,
                                            worker_b_session, pid_a, pid_b)


async def test_m01_stale_select_delete_zero_row_mutant_is_detected(pg17):
    """Physical stale SELECT and zero-row DELETE, mutated stale ACK."""
    from sqlalchemy import delete
    email = pg17.email()
    await pg17.seed(email)
    async with pg17.maker() as a, pg17.maker() as b:
        stale_a = await a.get(PendingRegistration, email)
        stale_b = await b.get(PendingRegistration, email)
        assert stale_a is not None and stale_b is not None
        assert stale_a.payload == stale_b.payload
        first = await a.execute(delete(PendingRegistration).where(PendingRegistration.email == email))
        assert first.rowcount == 1
        await a.commit()
        second = await b.execute(delete(PendingRegistration).where(PendingRegistration.email == email))
        await b.commit()
        assert second.rowcount == 0
        mutant_a = Outcome("SUCCESS" if stale_a.payload else "DENIED")
        mutant_b = Outcome("SUCCESS" if stale_b.payload else "DENIED")
        assert categorize(mutant_a, mutant_b) == "SEMANTIC_RED_DUPLICATE"
        assert categorize(mutant_a, Outcome("DENIED")) == "GREEN"

async def test_m03_sqlstate_to_missing_mutant_is_detected(pg17):
    """Use a *real* PG lock timeout before test-only error suppression."""
    email = pg17.email()
    await pg17.seed(email)
    class TimeoutUOW(ProbeUOW):
        async def __aenter__(self):
            await super().__aenter__()
            await self.session.execute(text("SET LOCAL lock_timeout='150ms'"))
            return self

    aid = pg17.attempt()
    async with pg17.maker() as locker:
        async with locker.begin():
            await locker.execute(select(PendingRegistration.email).where(PendingRegistration.email == email).with_for_update())
            true_result = await asyncio.wait_for(observed_verify(
                lambda: TimeoutUOW(pg17.driver, pg17.trace, aid),
                email, "123456", pg17.trace, aid), timeout=5)
            assert preserves_backend_failure(true_result, "55P03")
            # Actual SQLSTATE reached a test-only error suppressor.
            def mutant_suppress_backend_error(reached_result):
                assert reached_result.exception is not None
                return Outcome("DENIED")
            false_missing = mutant_suppress_backend_error(true_result)
            assert not preserves_backend_failure(false_missing, "55P03")


async def test_m04_prelock_ttl_mutant_is_detected(pg17):
    """Reach clock crossing on PG17; compare with the same expiry predicate as PG06."""
    email = pg17.email()
    expiry = await pg17.seed(email, ttl=1)
    before = await pg17.now()
    assert before < expiry, "Mutation precondition: prelock observation must be live"
    while await pg17.now() <= expiry + timedelta(milliseconds=120):
        await asyncio.sleep(0.03)
    after = await pg17.now()
    assert after >= expiry
    # Test-only buggy source cached 'live' at the earlier SELECT:
    assert not expiry_ack_allowed(expiry, after, Outcome("SUCCESS"))
    assert expiry_ack_allowed(expiry, after, Outcome("DENIED"))


async def test_pg05_exact_now_boundary_is_expired(pg17):
    email = pg17.email()
    boundary = await pg17.now()
    await pg17.seed(email, expires=boundary)
    aid = pg17.attempt()
    outcome = await observed_verify(pg17.uow_factory(aid), email, "123456", pg17.trace, aid)
    assert outcome.status == "DENIED"
    assert await pg17.row_count(email) == 0
