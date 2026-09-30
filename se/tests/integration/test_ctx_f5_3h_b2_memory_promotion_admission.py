from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import memory_content_digest
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionProofScope,
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
