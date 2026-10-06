from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import canonical_memory_bytes
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionProofScope,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.models.sql.promotion_reservation import (
    PromotionReservationRow,
)
from se.src.infrastructure.storage.repositories.memory import (
    DurableMemoryRecordRepository,
)
import se.src.infrastructure.storage.repositories.promotion_reservation as reservation_repository_module
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationCanonicalDigestCollisionError,
    PromotionReservationProofReuseConflictError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


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


def _intent(
    *,
    suffix: str = "one",
    metadata=None,
    content_digest: str | None = None,
    proof_receipt_id: str | None = None,
    authority_state_token: str | None = None,
) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id="user-ctx-f5-3f-b",
        session_id=f"session-{suffix}",
        source_created_at=NOW,
        source_state="active",
        metadata={"source": "canonical"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id=proof_receipt_id or f"receipt-{suffix}",
        authority_state_token=authority_state_token or f"token-{suffix}",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=content_digest or f"content-{suffix}",
        metadata={"kind": "durable"} if metadata is None else metadata,
        memory_schema_version=1,
    )


async def _memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(PromotionReservationRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _file_database(database: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5.0},
    )
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(PromotionReservationRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def test_ctx_f5_3f_b_sql_model_freezes_trusted_identity_and_state_constraints():
    table = PromotionReservationRow.__table__

    assert table.name == "promotion_reservations"
    assert table.c.promotion_authority_id.primary_key is True
    assert table.c.intent_json.nullable is False
    assert table.c.intent_canonical_bytes.nullable is False
    assert table.c.intent_digest.nullable is False
    assert table.c.source_context_source_id.nullable is False
    assert table.c.proof_receipt_id.nullable is False
    assert table.c.authority_state_token.nullable is False
    assert table.c.proof_scope.nullable is False
    assert table.c.state.nullable is False

    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques["uq_promotion_reservations_intent_digest"] == (
        "intent_digest",
    )
    assert uniques["uq_promotion_reservations_proof_authority"] == (
        "source_context_source_id",
        "proof_receipt_id",
        "authority_state_token",
        "proof_scope",
    )

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ISSUED" in checks["ck_promotion_reservations_state"]
    assert "CONSUMED" in checks["ck_promotion_reservations_state"]
    assert "REVOKED" in checks["ck_promotion_reservations_state"]
    assert "MEMORY_PROMOTION" in checks["ck_promotion_reservations_proof_scope"]


def test_ctx_f5_3f_b_migration_is_linear_and_downgrade_is_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ctx_f5_3f_b.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["28a_tbo1_task_policy_representation"]
    assert (
        script.get_revision("23a_ctx_f5_promotion_reservation").down_revision
        == "22a_r12_execution_lease_fence"
    )

    command.upgrade(config, "22a_r12_execution_lease_fence")
    connection = sqlite3.connect(database)
    try:
        before_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()

    command.upgrade(config, "23a_ctx_f5_promotion_reservation")
    connection = sqlite3.connect(database)
    try:
        after_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert after_tables - before_tables == {"promotion_reservations"}

        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='promotion_reservations'"
        ).fetchone()[0]
        assert "ck_promotion_reservations_state" in table_sql
        assert "ck_promotion_reservations_proof_scope" in table_sql
        assert "uq_promotion_reservations_intent_digest" in table_sql
        assert "uq_promotion_reservations_proof_authority" in table_sql

        connection.execute(
            """
            INSERT INTO promotion_reservations (
                promotion_authority_id,
                intent_digest,
                intent_json,
                intent_canonical_bytes,
                source_context_source_id,
                proof_receipt_id,
                authority_state_token,
                proof_scope,
                state
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "authority-downgrade",
                "d" * 64,
                json.dumps({"synthetic": True}),
                b'{"synthetic":true}',
                "s" * 64,
                "receipt-downgrade",
                "token-downgrade",
                "MEMORY_PROMOTION",
                "ISSUED",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError,
        match="durable promotion reservation authority rows exist",
    ):
        command.downgrade(config, "22a_r12_execution_lease_fence")

    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM promotion_reservations")
        connection.commit()
    finally:
        connection.close()

    command.downgrade(config, "22a_r12_execution_lease_fence")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "promotion_reservations" not in tables
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_same_intent_retry_recovers_one_durable_winner():
    engine, sessions = await _memory_database()
    intent = _intent()
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            first = await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-first",
                intent=intent,
            )
            assert first.state is DurablePromotionReservationState.ISSUED
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            replay = await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-second",
                intent=intent,
            )
            assert replay.promotion_authority_id == "authority-first"
            assert replay.intent == intent
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            loaded = await repository.get("authority-first")
            assert loaded is not None
            assert loaded.intent == intent
            assert loaded.intent_digest == first.intent_digest
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_exact_intent_round_trip_preserves_json_type_identity():
    base = _intent(suffix="typed")
    same_authority_variants = [
        MemoryPromotionIntent(
            owner_user_id=base.owner_user_id,
            source_ref_snapshot=base.source_ref_snapshot,
            source_proof=base.source_proof,
            content_digest=base.content_digest,
            metadata={"value": value},
            memory_schema_version=base.memory_schema_version,
        )
        for value in (True, 1, 1.0)
    ]
    canonical_variants = {
        canonical_memory_bytes(intent.model_dump(mode="json"))
        for intent in same_authority_variants
    }
    assert len(canonical_variants) == 3

    engine, sessions = await _memory_database()
    try:
        variants = [
            _intent(suffix="bool", metadata={"value": True}),
            _intent(suffix="int", metadata={"value": 1}),
            _intent(suffix="float", metadata={"value": 1.0}),
        ]
        winners = []
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            for index, intent in enumerate(variants):
                winners.append(
                    await repository.insert_or_converge_issued_candidate(
                        promotion_authority_id=f"authority-type-{index}",
                        intent=intent,
                    )
                )
            await session.commit()

        assert len({winner.intent_digest for winner in winners}) == 3

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            for index, original in enumerate(variants):
                loaded = await repository.get(f"authority-type-{index}")
                assert loaded is not None
                assert loaded.intent.model_dump(mode="json") == original.model_dump(
                    mode="json"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_synthetic_digest_collision_fails_closed(monkeypatch):
    monkeypatch.setattr(
        reservation_repository_module,
        "_intent_digest",
        lambda canonical: "0" * 64,
    )
    engine, sessions = await _memory_database()
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-collision-a",
                intent=_intent(suffix="collision-a"),
            )
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            with pytest.raises(
                PromotionReservationCanonicalDigestCollisionError,
                match="collision",
            ):
                await repository.insert_or_converge_issued_candidate(
                    promotion_authority_id="authority-collision-b",
                    intent=_intent(suffix="collision-b"),
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_proof_tuple_reuse_for_different_intent_fails_closed():
    engine, sessions = await _memory_database()
    first = _intent(
        suffix="proof",
        metadata={"version": 1},
        proof_receipt_id="shared-receipt",
        authority_state_token="shared-token",
    )
    second = MemoryPromotionIntent(
        owner_user_id=first.owner_user_id,
        source_ref_snapshot=first.source_ref_snapshot,
        source_proof=first.source_proof,
        content_digest=first.content_digest,
        metadata={"version": 2},
        memory_schema_version=first.memory_schema_version,
    )
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-proof-a",
                intent=first,
            )
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            with pytest.raises(
                PromotionReservationProofReuseConflictError,
                match="proof authority tuple",
            ):
                await repository.insert_or_converge_issued_candidate(
                    promotion_authority_id="authority-proof-b",
                    intent=second,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_only_allows_issued_to_terminal_transitions():
    engine, sessions = await _memory_database()
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-consume",
                intent=_intent(suffix="consume"),
            )
            resident_consume = await session.get(
                PromotionReservationRow,
                "authority-consume",
            )
            assert resident_consume is not None
            assert resident_consume.state == DurablePromotionReservationState.ISSUED.value
            loaded_consume = await repository.get("authority-consume")
            assert loaded_consume is not None
            assert loaded_consume.state is DurablePromotionReservationState.ISSUED

            consumed = await repository.mark_consumed("authority-consume")
            assert consumed.state is DurablePromotionReservationState.CONSUMED
            assert resident_consume.state == DurablePromotionReservationState.CONSUMED.value
            with pytest.raises(PromotionReservationAlreadyConsumedError):
                await repository.mark_revoked("authority-consume")
            assert session.in_transaction()
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-revoke",
                intent=_intent(suffix="revoke"),
            )
            resident_revoke = await session.get(
                PromotionReservationRow,
                "authority-revoke",
            )
            assert resident_revoke is not None
            assert resident_revoke.state == DurablePromotionReservationState.ISSUED.value
            loaded_revoke = await repository.get("authority-revoke")
            assert loaded_revoke is not None
            assert loaded_revoke.state is DurablePromotionReservationState.ISSUED

            revoked = await repository.mark_revoked("authority-revoke")
            assert revoked.state is DurablePromotionReservationState.REVOKED
            assert resident_revoke.state == DurablePromotionReservationState.REVOKED.value
            with pytest.raises(PromotionReservationRevokedError):
                await repository.mark_consumed("authority-revoke")
            assert session.in_transaction()
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_terminal_transition_never_commits_caller_transaction():
    engine, sessions = await _memory_database()
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-transition-rollback",
                intent=_intent(suffix="transition-rollback"),
            )
            await session.commit()

            resident = await session.get(
                PromotionReservationRow,
                "authority-transition-rollback",
            )
            assert resident is not None
            assert resident.state == DurablePromotionReservationState.ISSUED.value
            loaded = await repository.get("authority-transition-rollback")
            assert loaded is not None
            assert loaded.state is DurablePromotionReservationState.ISSUED

            consumed = await repository.mark_consumed(
                "authority-transition-rollback"
            )
            assert consumed.state is DurablePromotionReservationState.CONSUMED
            assert session.in_transaction()

            await session.rollback()
            restored = await repository.get("authority-transition-rollback")
            assert restored is not None
            assert restored.state is DurablePromotionReservationState.ISSUED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_repository_never_commits_and_can_share_memory_session(
    tmp_path: Path,
):
    engine, sessions = await _file_database(tmp_path / "no_hidden_commit.sqlite")
    try:
        async with sessions() as writer:
            reservation_repository = DurablePromotionReservationRepository(writer)
            memory_repository = DurableMemoryRecordRepository(writer)
            assert reservation_repository.session is writer
            assert memory_repository.session is writer

            await reservation_repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-uncommitted",
                intent=_intent(suffix="uncommitted"),
            )
            assert writer.in_transaction()

            async with sessions() as observer:
                observer_repository = DurablePromotionReservationRepository(observer)
                assert await observer_repository.get("authority-uncommitted") is None

            await writer.rollback()

        async with sessions() as observer:
            observer_repository = DurablePromotionReservationRepository(observer)
            assert await observer_repository.get("authority-uncommitted") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_file_sqlite_competing_insertions_converge_one_winner(
    tmp_path: Path,
):
    engine, sessions = await _file_database(tmp_path / "convergence.sqlite")
    intent = _intent(suffix="race")

    async def contender(authority_id: str) -> str:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            winner = await repository.insert_or_converge_issued_candidate(
                promotion_authority_id=authority_id,
                intent=intent,
            )
            await session.commit()
            return winner.promotion_authority_id

    try:
        winners = await asyncio.gather(
            contender("authority-race-a"),
            contender("authority-race-b"),
        )
        assert len(set(winners)) == 1

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            loaded = await repository.get_by_intent(intent)
            assert loaded is not None
            assert loaded.promotion_authority_id == winners[0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3f_b_reload_revalidates_canonical_material_and_corruption():
    engine, sessions = await _memory_database()
    intent = _intent(suffix="reload")
    try:
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-reload",
                intent=intent,
            )
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            loaded = await repository.get("authority-reload")
            assert loaded is not None
            assert loaded.intent == intent

            row = await session.get(PromotionReservationRow, "authority-reload")
            assert row is not None
            row.intent_canonical_bytes = b"{}"
            await session.commit()

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            with pytest.raises(
                PromotionReservationReconstructionCorruptionError,
                match="exact reconstruction",
            ):
                await repository.get("authority-reload")
    finally:
        await engine.dispose()
