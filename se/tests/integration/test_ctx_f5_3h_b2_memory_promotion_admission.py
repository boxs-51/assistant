from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import memory_content_digest
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionProofScope,
    PromotionAdmissionPersistenceFailureError,
    PromotionReservation,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.models.sql.memory import MemoryRecordRow
from se.src.infrastructure.storage.models.sql.promotion_reservation import (
    PromotionReservationRow,
)
from se.src.infrastructure.storage.repositories.memory import (
    DurableMemoryRecordRepository,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
)
from se.src.infrastructure.storage.services.memory_promotion_admission import (
    DurableMemoryPromotionAdmission,
)


NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)


def _intent(content) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-h-b2",
        owner_user_id="user-h-b2",
        session_id="session-h-b2",
        source_created_at=NOW,
        source_state="active",
        metadata={"source": "canonical"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id="receipt-h-b2",
        authority_state_token="token-h-b2",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=memory_content_digest(content),
        metadata={"kind": "h-b2"},
        memory_schema_version=1,
    )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(MemoryRecordRow.__table__.create)
        await connection.run_sync(PromotionReservationRow.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _file_database(database: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 0.01},
    )
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(MemoryRecordRow.__table__.create)
        await connection.run_sync(PromotionReservationRow.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_reservation(sessions, authority_id: str, intent: MemoryPromotionIntent):
    async with sessions() as session:
        repository = DurablePromotionReservationRepository(session)
        durable = await repository.insert_or_converge_issued_candidate(
            promotion_authority_id=authority_id,
            intent=intent,
        )
        await session.commit()
        return durable


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_sqlite_first_admission_and_consumed_replay_are_atomic():
    engine, sessions = await _database()
    content = {"fact": ["alpha"]}
    intent = _intent(content)
    authority_id = "authority-h-b2"
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )

    try:
        async with sessions() as session:
            reservation_repository = DurablePromotionReservationRepository(session)
            durable = await reservation_repository.insert_or_converge_issued_candidate(
                promotion_authority_id=authority_id,
                intent=intent,
            )
            assert durable.state is DurablePromotionReservationState.ISSUED
            await session.commit()

        service = DurableMemoryPromotionAdmission(sessions)
        first = await service.admit(
            reservation=reservation,
            content=content,
        )

        async with sessions() as session:
            reservation_repository = DurablePromotionReservationRepository(session)
            memory_repository = DurableMemoryRecordRepository(session)
            durable = await reservation_repository.get(authority_id)
            persisted = await memory_repository.get_by_promotion_authority(authority_id)

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.CONSUMED
            assert persisted is not None
            assert persisted.memory_id == first.memory_id
            assert persisted.content["fact"] == ("alpha",)

        replay = await service.admit(
            reservation=reservation,
            content={"fact": ["alpha"]},
        )

        assert replay.memory_id == first.memory_id
        assert replay.created_at == first.created_at

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                authority_id
            )
            persisted = await DurableMemoryRecordRepository(
                session
            ).get_by_promotion_authority(authority_id)

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.CONSUMED
            assert persisted is not None
            assert persisted.memory_id == first.memory_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_cancellation_after_memory_put_rolls_back_both_effects(
    monkeypatch,
):
    engine, sessions = await _database()
    content = {"fact": "cancel"}
    intent = _intent(content)
    authority_id = "authority-h-b2-cancel"
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )
    await _seed_reservation(sessions, authority_id, intent)

    async def cancel_consume(self, promotion_authority_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        DurablePromotionReservationRepository,
        "mark_consumed",
        cancel_consume,
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            await DurableMemoryPromotionAdmission(sessions).admit(
                reservation=reservation,
                content=content,
            )

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                authority_id
            )
            memory = await DurableMemoryRecordRepository(
                session
            ).get_by_promotion_authority(authority_id)

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.ISSUED
            assert memory is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_post_entry_persistence_failure_rolls_back_both_effects(
    monkeypatch,
):
    engine, sessions = await _database()
    content = {"fact": "failure"}
    intent = _intent(content)
    authority_id = "authority-h-b2-failure"
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )
    await _seed_reservation(sessions, authority_id, intent)

    async def fail_consume(self, promotion_authority_id):
        raise SQLAlchemyError("forced transition failure")

    monkeypatch.setattr(
        DurablePromotionReservationRepository,
        "mark_consumed",
        fail_consume,
    )

    try:
        with pytest.raises(PromotionAdmissionPersistenceFailureError):
            await DurableMemoryPromotionAdmission(sessions).admit(
                reservation=reservation,
                content=content,
            )

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                authority_id
            )
            memory = await DurableMemoryRecordRepository(
                session
            ).get_by_promotion_authority(authority_id)

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.ISSUED
            assert memory is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_concurrent_same_reservation_converges_without_split_state(
    tmp_path,
):
    engine, sessions = await _file_database(tmp_path / "ctx-h-b2-race.db")
    content = {"fact": "race"}
    intent = _intent(content)
    authority_id = "authority-h-b2-race"
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )
    await _seed_reservation(sessions, authority_id, intent)
    service = DurableMemoryPromotionAdmission(sessions)

    try:
        first, second = await asyncio.gather(
            service.admit(reservation=reservation, content=content),
            service.admit(reservation=reservation, content={"fact": "race"}),
        )

        assert first.memory_id == second.memory_id

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                authority_id
            )
            memory = await DurableMemoryRecordRepository(
                session
            ).get_by_promotion_authority(authority_id)

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.CONSUMED
            assert memory is not None
            assert memory.memory_id == first.memory_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3h_b2_durable_row_corruption_maps_to_persistence_failure():
    engine, sessions = await _database()
    content = {"fact": "corrupt"}
    intent = _intent(content)
    authority_id = "authority-h-b2-corrupt"
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )
    await _seed_reservation(sessions, authority_id, intent)

    try:
        async with sessions() as session:
            await session.execute(
                update(PromotionReservationRow)
                .where(
                    PromotionReservationRow.promotion_authority_id
                    == authority_id
                )
                .values(intent_digest="0" * 64)
            )
            await session.commit()

        with pytest.raises(PromotionAdmissionPersistenceFailureError):
            await DurableMemoryPromotionAdmission(sessions).admit(
                reservation=reservation,
                content=content,
            )

        async with sessions() as session:
            memory = await DurableMemoryRecordRepository(
                session
            ).get_by_promotion_authority(authority_id)
            assert memory is None
    finally:
        await engine.dispose()
