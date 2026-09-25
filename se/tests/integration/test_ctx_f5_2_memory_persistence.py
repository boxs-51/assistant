from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import (
    MemoryRecord,
    MemoryRecordConflictError,
    create_memory_record,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.models.sql.memory import MemoryRecordRow
from se.src.infrastructure.storage.repositories.memory import (
    DurableMemoryRecordRepository,
    MemoryAdmissionTransactionError,
    sqlite_memory_admission_transaction,
)


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


def _source(
    *,
    created_at: datetime = NOW,
    owner: str = "user-memory",
):
    return create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-memory",
        owner_user_id=owner,
        session_id="session-memory",
        source_created_at=created_at,
        source_state="active",
        metadata={"source": "canonical"},
    )


def _task_source(*, authority_version: int = 1):
    return create_context_source_ref(
        source_kind=ContextSourceKind.TASK,
        authority_id="task-memory",
        authority_version=authority_version,
        owner_user_id="user-memory",
        task_id="task-memory",
        source_created_at=NOW,
        source_state="active",
        metadata={"source": "canonical"},
    )


def _record(
    *,
    source=None,
    metadata=None,
    content=None,
    promotion_authority_id: str = "promotion-memory",
):
    return create_memory_record(
        source_ref=source or _source(),
        promotion_authority_id=promotion_authority_id,
        content={"fact": "alpha"} if content is None else content,
        metadata={"kind": "durable"} if metadata is None else metadata,
    )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(MemoryRecordRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _file_database(database: Path, *, wal: bool = True):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 0.01},
    )
    async with engine.begin() as connection:
        if wal:
            await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(MemoryRecordRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _conflicting_metadata(
    original: MemoryRecord,
    metadata,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=original.memory_id,
        promotion_authority_id=original.promotion_authority_id,
        memory_schema_version=original.memory_schema_version,
        source_ref_snapshot=original.source_ref_snapshot,
        owner_user_id=original.owner_user_id,
        content_digest=original.content_digest,
        canonical_bytes=original.canonical_bytes,
        content={"fact": "alpha"},
        metadata=metadata,
        created_at=original.created_at + timedelta(seconds=1),
    )


def test_ctx_f5_2_sql_model_freezes_identity_and_replay_constraints():
    table = MemoryRecordRow.__table__

    assert table.name == "memory_records"
    assert table.c.memory_id.primary_key is True
    assert table.c.memory_id.nullable is False
    assert table.c.promotion_authority_id.nullable is False
    assert table.c.source_ref_json.nullable is False
    assert table.c.content_json.nullable is False
    assert table.c.metadata_json.nullable is False

    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques["uq_memory_records_promotion_authority"] == (
        "promotion_authority_id",
    )


@pytest.mark.asyncio
async def test_ctx_f5_2_round_trip_revalidates_and_preserves_first_winner_timestamp():
    engine, sessions = await _database()
    try:
        first = _record()
        first = first.model_copy(update={"created_at": NOW})

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            stored = await repository.put(first)
            assert stored.memory_id == first.memory_id
            assert stored.created_at == NOW
            await session.commit()

        replay = _record().model_copy(
            update={"created_at": NOW + timedelta(days=1)}
        )
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            winner = await repository.put(replay)
            assert winner.memory_id == first.memory_id
            assert winner.created_at == NOW
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            loaded = await repository.get(first.memory_id)
            assert loaded is not None
            assert loaded.created_at == NOW
            assert loaded.source_ref_snapshot == first.source_ref_snapshot
            assert loaded.content_digest == first.content_digest
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_metadata", "replay_metadata"),
    [
        ({"value": True}, {"value": 1}),
        ({"value": 1}, {"value": 1.0}),
    ],
)
async def test_ctx_f5_2_durable_replay_preserves_json_type_distinctions(
    first_metadata,
    replay_metadata,
):
    engine, sessions = await _database()
    try:
        first = _record(metadata=first_metadata)
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await session.commit()

        conflicting = _conflicting_metadata(first, replay_metadata)
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            with pytest.raises(MemoryRecordConflictError, match="conflicting"):
                await repository.put(conflicting)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_promotion_authority_uniqueness_and_content_separation():
    engine, sessions = await _database()
    try:
        first = _record(
            promotion_authority_id="promotion-one",
            content={"fact": "alpha"},
        )
        conflicting = _record(
            promotion_authority_id="promotion-one",
            content={"fact": "beta"},
        )
        distinct = _record(
            promotion_authority_id="promotion-two",
            content={"fact": "alpha"},
        )
        assert first.memory_id != conflicting.memory_id
        assert first.memory_id != distinct.memory_id

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await repository.put(distinct)
            with pytest.raises(
                MemoryRecordConflictError,
                match="promotion_authority_id",
            ):
                await repository.put(conflicting)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_durable_replay_normalizes_equivalent_source_instants():
    engine, sessions = await _database()
    try:
        utc_source = _source(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        offset_source = _source(
            created_at=datetime(
                2026,
                1,
                1,
                1,
                tzinfo=timezone(timedelta(hours=1)),
            )
        )
        first = _record(source=utc_source)
        replay = _record(source=offset_source)
        assert first.memory_id == replay.memory_id

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            winner = await repository.put(first)
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            returned = await repository.put(replay)
            assert returned.memory_id == winner.memory_id
            assert returned.source_ref_snapshot.source_created_at == (
                utc_source.source_created_at
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_different_source_instant_is_durable_conflict():
    engine, sessions = await _database()
    try:
        first = _record(source=_source(created_at=NOW))
        changed = _record(source=_source(created_at=NOW + timedelta(seconds=1)))
        assert first.memory_id == changed.memory_id

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            with pytest.raises(MemoryRecordConflictError, match="conflicting"):
                await repository.put(changed)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_reconstruction_rejects_normalized_column_provenance_drift():
    engine, sessions = await _database()
    try:
        first = _record()
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await session.commit()

        async with sessions() as session:
            await session.execute(
                update(MemoryRecordRow)
                .where(MemoryRecordRow.memory_id == first.memory_id)
                .values(owner_user_id="different-owner")
            )
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            with pytest.raises(ValueError, match="owner column conflicts"):
                await repository.get(first.memory_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt_version", [True, 1.0])
async def test_ctx_f5_2_reconstruction_rejects_type_coerced_source_payload(
    corrupt_version,
):
    engine, sessions = await _database()
    try:
        first = _record(source=_task_source(authority_version=1))
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await session.commit()

        corrupted_payload = first.source_ref_snapshot.model_dump(mode="json")
        corrupted_payload["authority_version"] = corrupt_version
        async with sessions() as session:
            await session.execute(
                update(MemoryRecordRow)
                .where(MemoryRecordRow.memory_id == first.memory_id)
                .values(source_ref_json=corrupted_payload)
            )
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            with pytest.raises(ValueError, match="type-coerced"):
                await repository.get(first.memory_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_reconstruction_recomputes_memory_integrity_from_durable_row():
    engine, sessions = await _database()
    try:
        first = _record()
        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            await repository.put(first)
            await session.commit()

        async with sessions() as session:
            await session.execute(
                update(MemoryRecordRow)
                .where(MemoryRecordRow.memory_id == first.memory_id)
                .values(content_digest="0" * 64)
            )
            await session.commit()

        async with sessions() as session:
            repository = DurableMemoryRecordRepository(session)
            with pytest.raises(ValueError, match="content_digest"):
                await repository.get(first.memory_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_second_session_cannot_rollback_first_owner():
    engine, sessions = await _database()
    try:
        first = _record()
        async with sessions() as first_session, sessions() as second_session:
            first_repository = DurableMemoryRecordRepository(first_session)
            second_repository = DurableMemoryRecordRepository(second_session)

            stored = await first_repository.put(first)

            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="already owned by another session",
            ):
                await second_repository.put(
                    _record(promotion_authority_id="promotion-second")
                )

            # The rejected second logical Session never touches the shared
            # physical transaction, so the first owner's uncommitted row
            # remains present and can be committed normally.
            assert first_session.in_transaction() is True
            await first_session.commit()

            loaded = await first_repository.get(stored.memory_id)
            assert loaded is not None
            assert loaded.memory_id == first.memory_id

            # Lazy owner cleanup after A's commit makes B reusable without any
            # undocumented rollback/repair step.
            replay = await second_repository.put(_record())
            assert replay.memory_id == first.memory_id
            await second_session.commit()

            assert (
                await second_session.execute(text("SELECT 1"))
            ).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_unrelated_physical_transaction_is_protected():
    engine, sessions = await _database()
    try:
        first = _record()
        async with sessions() as first_session, sessions() as second_session:
            first_repository = DurableMemoryRecordRepository(first_session)
            second_repository = DurableMemoryRecordRepository(second_session)

            await first_repository.put(first)
            await first_session.commit()

            await first_session.execute(
                text("CREATE TABLE unrelated_probe (value INTEGER NOT NULL)")
            )
            await first_session.commit()

            await first_session.execute(text("BEGIN"))
            await first_session.execute(
                text("INSERT INTO unrelated_probe(value) VALUES (7)")
            )

            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="shared physical connection",
            ):
                await second_repository.put(
                    _record(promotion_authority_id="promotion-second-physical")
                )

            # B was rejected before touching SQL/rollback on the shared
            # physical connection. Its logical Session remains clean while A's
            # unrelated write transaction is still intact.
            assert second_session.in_transaction() is False
            await first_session.commit()

            assert (
                await first_session.execute(
                    text("SELECT value FROM unrelated_probe")
                )
            ).scalar_one() == 7

            # Once A's physical transaction ends, B becomes reusable and may
            # replay the previously committed Memory winner.
            returned = await second_repository.put(_record())
            assert returned.memory_id == first.memory_id
            await second_session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_reader_cleanup_is_quarantined_from_active_owner():
    engine, sessions = await _database()
    second_session = None
    try:
        first = _record()
        async with sessions() as first_session:
            first_repository = DurableMemoryRecordRepository(first_session)
            await first_repository.put(first)

            second_session = sessions()
            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="shared physical connection",
            ):
                await second_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )

            # SQLAlchemy has already created B's logical transaction by the
            # engine-begin hook. That attachment is unavoidable for a direct
            # Session.execute(), so quarantine its cleanup instead: rollback
            # must be rejected before it reaches A's shared DBAPI transaction.
            assert second_session.in_transaction() is True
            # Cleanup must succeed logically while the quarantined DBAPI
            # rollback is suppressed; A's physical transaction remains owned
            # by the first Session.
            await second_session.rollback()
            assert second_session.in_transaction() is False

            await first_session.commit()

            async with sessions() as verification_session:
                verification_repository = DurableMemoryRecordRepository(
                    verification_session
                )
                loaded = await verification_repository.get(first.memory_id)
                assert loaded is not None
                assert loaded.memory_id == first.memory_id
    finally:
        if second_session is not None:
            if second_session.in_transaction():
                await second_session.rollback()
            await second_session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_cursor_race_quarantines_rejected_session():
    engine, sessions = await _database()
    try:
        async with sessions() as first_session, sessions() as second_session:
            first_repository = DurableMemoryRecordRepository(first_session)
            DurableMemoryRecordRepository(second_session)

            # Deterministically force the race the auditor identified:
            # B creates only a logical/deferred transaction while SQLite is
            # physically idle, so the engine begin guard accepts it.
            await second_session.connection()
            assert second_session.in_transaction() is True

            # A then acquires the shared physical write transaction.
            first = _record()
            await first_repository.put(first)

            # B's first actual cursor execution now reaches the
            # before_cursor_execute race branch. It must use the same DBAPI
            # quarantine authority as the begin guard, never a stale state key.
            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="already owned by another logical connection",
            ):
                await second_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )

            # B's logical cleanup succeeds, but the quarantined DBAPI rollback
            # is suppressed while A still owns the raw transaction.
            await second_session.rollback()
            assert second_session.in_transaction() is False

            await first_session.commit()

            async with sessions() as verification_session:
                verification_repository = DurableMemoryRecordRepository(
                    verification_session
                )
                loaded = await verification_repository.get(first.memory_id)
                assert loaded is not None
                assert loaded.memory_id == first.memory_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_preinstalled_reader_raw_tx_is_quarantined():
    engine, sessions = await _database()
    second_session = None
    try:
        async with sessions() as first_session:
            await first_session.execute(
                text(
                    "CREATE TABLE preinstalled_reader_probe "
                    "(value INTEGER NOT NULL)"
                )
            )
            await first_session.commit()

            # B acquires its logical Connection before the Memory monitor
            # exists. SQLite remains physically idle because B has executed no
            # cursor statement yet.
            second_session = sessions()
            await second_session.connection()
            assert second_session.in_transaction() is True

            # A then starts a real physical transaction before monitor
            # installation.
            await first_session.execute(text("BEGIN"))
            await first_session.execute(
                text("INSERT INTO preinstalled_reader_probe(value) VALUES (17)")
            )

            # Install the monitor only after both conditions above exist.
            DurableMemoryRecordRepository(second_session)

            # B already has a logical transaction, so no new engine begin event
            # occurs. The cursor guard must discover the raw StaticPool
            # transaction despite state.active starting false, quarantine B,
            # and reject the read.
            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="pre-existing transaction",
            ):
                await second_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )

            # B cleanup is logically successful while its quarantined DBAPI
            # rollback is suppressed; A remains the physical transaction owner.
            await second_session.rollback()
            assert second_session.in_transaction() is False

            await first_session.commit()

            assert (
                await first_session.execute(
                    text("SELECT value FROM preinstalled_reader_probe")
                )
            ).scalar_one() == 17
    finally:
        if second_session is not None:
            if second_session.in_transaction():
                await second_session.rollback()
            await second_session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_late_monitor_reader_close_is_quarantined():
    engine, sessions = await _database()
    second_session = None
    try:
        async with sessions() as first_session:
            await first_session.execute(
                text("CREATE TABLE late_reader_probe (value INTEGER NOT NULL)")
            )
            await first_session.commit()
            await first_session.execute(text("BEGIN"))
            await first_session.execute(
                text("INSERT INTO late_reader_probe(value) VALUES (13)")
            )

            second_session = sessions()
            # Install the monitor only after A already owns the shared physical
            # transaction, then exercise a direct reader rather than Memory put.
            DurableMemoryRecordRepository(second_session)
            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="pre-existing transaction",
            ):
                await second_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )

            assert second_session.in_transaction() is True
            # Session.close() may invoke both transaction rollback and pool
            # rollback-on-return. Both DBAPI paths are quarantined while A
            # still owns the physical transaction, so close itself is safe.
            await second_session.close()
            assert second_session.in_transaction() is False
            second_session = None

            await first_session.commit()

            assert (
                await first_session.execute(
                    text("SELECT value FROM late_reader_probe")
                )
            ).scalar_one() == 13
    finally:
        if second_session is not None:
            if second_session.in_transaction():
                await second_session.rollback()
            await second_session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_in_memory_late_monitor_rejects_preexisting_physical_owner():
    engine, sessions = await _database()
    try:
        async with sessions() as first_session, sessions() as second_session:
            # Establish a real physical transaction before any Memory
            # repository has installed the shared-connection monitor.
            await first_session.execute(
                text("CREATE TABLE unrelated_late_probe (value INTEGER NOT NULL)")
            )
            await first_session.commit()
            await first_session.execute(text("BEGIN"))
            await first_session.execute(
                text("INSERT INTO unrelated_late_probe(value) VALUES (11)")
            )

            second_repository = DurableMemoryRecordRepository(second_session)
            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="pre-existing transaction",
            ):
                await second_repository.put(
                    _record(
                        promotion_authority_id="promotion-late-monitor",
                    )
                )

            # The raw StaticPool connection is inspected before
            # Session.execute(), so this late-monitor case is rejected before
            # B attaches logical transaction state.
            assert second_session.in_transaction() is False
            await first_session.commit()

            assert (
                await first_session.execute(
                    text("SELECT value FROM unrelated_late_probe")
                )
            ).scalar_one() == 11
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_exhausted_write_intent_retries_leave_session_clean(
    tmp_path: Path,
):
    engine, sessions = await _file_database(
        tmp_path / "ctx_f5_2_write_intent_exhausted.sqlite"
    )
    try:
        async with sessions() as holder_session, sessions() as blocked_session:
            holder_repository = DurableMemoryRecordRepository(holder_session)
            await holder_repository.put(_record())

            blocked_repository = DurableMemoryRecordRepository(blocked_session)
            with pytest.raises(OperationalError) as exc_info:
                await blocked_repository.put(
                    _record(promotion_authority_id="promotion-blocked")
                )
            assert "locked" in str(exc_info.value).lower() or "busy" in str(
                exc_info.value
            ).lower()
            assert blocked_session.in_transaction() is False

            await holder_session.commit()

            async with sqlite_memory_admission_transaction(
                blocked_session
            ) as recovered_repository:
                stored = await recovered_repository.put(
                    _record(promotion_authority_id="promotion-recovered")
                )
                assert stored.promotion_authority_id == "promotion-recovered"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_admission_scope_rolls_back_when_commit_is_locked(
    tmp_path: Path,
):
    engine, sessions = await _file_database(
        tmp_path / "ctx_f5_2_commit_locked.sqlite",
        wal=False,
    )
    try:
        async with sessions() as reader_session, sessions() as writer_session:
            # Force a real SQLite read transaction/SHARED lock; a logical
            # SQLAlchemy Session transaction alone is not sufficient.
            await reader_session.execute(text("BEGIN"))
            assert (
                await reader_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )
            ).scalar_one() == 0

            with pytest.raises(OperationalError) as exc_info:
                async with sqlite_memory_admission_transaction(
                    writer_session
                ) as repository:
                    await repository.put(_record())
            assert "locked" in str(exc_info.value).lower() or "busy" in str(
                exc_info.value
            ).lower()
            assert writer_session.in_transaction() is False

            await reader_session.rollback()

            async with sqlite_memory_admission_transaction(
                writer_session
            ) as repository:
                stored = await repository.put(_record())
                assert stored.memory_id == _record().memory_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_sqlite_stale_snapshot_fails_before_write_without_rollback(
    tmp_path: Path,
):
    engine, sessions = await _file_database(
        tmp_path / "ctx_f5_2_stale_snapshot.sqlite"
    )
    try:
        winner = _record()
        async with sessions() as loser_session, sessions() as winner_session:
            loser_repository = DurableMemoryRecordRepository(loser_session)
            winner_repository = DurableMemoryRecordRepository(winner_session)

            # Establish an unsupported deferred read snapshot before Memory
            # admission has acquired SQLite write intent.
            assert (
                await loser_session.execute(
                    text("SELECT count(*) FROM memory_records")
                )
            ).scalar_one() == 0

            await winner_repository.put(winner)
            await winner_session.commit()

            with pytest.raises(
                MemoryAdmissionTransactionError,
                match="write intent before any outer transaction read",
            ):
                await loser_repository.put(_record())

            # put() must not silently roll back or poison unrelated caller
            # work when it detects an already-stale transaction boundary.
            assert (
                await loser_session.execute(text("SELECT 1"))
            ).scalar_one() == 1
            await loser_session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_sqlite_admission_scope_acquires_write_intent_before_reads(
    tmp_path: Path,
):
    engine, sessions = await _file_database(
        tmp_path / "ctx_f5_2_admission_scope.sqlite"
    )
    try:
        winner = _record()
        async with sessions() as winner_session, sessions() as replay_session:
            winner_repository = DurableMemoryRecordRepository(winner_session)
            await winner_repository.put(winner)

            async def commit_winner():
                await asyncio.sleep(0.04)
                await winner_session.commit()

            commit_task = asyncio.create_task(commit_winner())
            try:
                async with sqlite_memory_admission_transaction(
                    replay_session
                ) as replay_repository:
                    assert (
                        await replay_session.execute(
                            text("SELECT count(*) FROM memory_records")
                        )
                    ).scalar_one() == 1
                    returned = await replay_repository.put(_record())
                    assert returned.memory_id == winner.memory_id
            finally:
                await commit_task
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_2_sqlite_busy_race_retries_and_outer_transaction_survives(
    tmp_path: Path,
):
    engine, sessions = await _file_database(tmp_path / "ctx_f5_2_race.sqlite")
    try:
        winner = _record()
        async with sessions() as winner_session, sessions() as loser_session:
            winner_repository = DurableMemoryRecordRepository(winner_session)
            await winner_repository.put(winner)

            async def commit_winner():
                await asyncio.sleep(0.04)
                await winner_session.commit()

            commit_task = asyncio.create_task(commit_winner())
            try:
                loser_repository = DurableMemoryRecordRepository(loser_session)
                returned = await loser_repository.put(_record())
            finally:
                await commit_task

            assert returned.memory_id == winner.memory_id
            assert (
                await loser_session.execute(text("SELECT 1"))
            ).scalar_one() == 1
    finally:
        await engine.dispose()


def test_ctx_f5_2_migration_is_linear_and_upgrades_downgrades_real_sqlite(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "ctx_f5_2.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["21a_ctx_f5_memory_foundation"]
    assert (
        script.get_revision("21a_ctx_f5_memory_foundation").down_revision
        == "20a_cas_f5_binding_foundation"
    )

    command.upgrade(config, "20a_cas_f5_binding_foundation")
    command.upgrade(config, "21a_ctx_f5_memory_foundation")

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "memory_records" in tables

        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(memory_records)")
        }
        assert set(columns) == {
            "memory_id",
            "promotion_authority_id",
            "memory_schema_version",
            "owner_user_id",
            "source_context_source_id",
            "source_ref_json",
            "content_digest",
            "canonical_bytes",
            "content_json",
            "metadata_json",
            "created_at",
        }
        assert columns["memory_id"][5] == 1

        unique_indexes = [
            row
            for row in connection.execute("PRAGMA index_list(memory_records)")
            if row[2] == 1
        ]
        assert any(
            tuple(
                item[2]
                for item in connection.execute(
                    f"PRAGMA index_info({index_row[1]})"
                )
            )
            == ("promotion_authority_id",)
            for index_row in unique_indexes
        )
    finally:
        connection.close()

    command.downgrade(config, "20a_cas_f5_binding_foundation")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "memory_records" not in tables
    finally:
        connection.close()
