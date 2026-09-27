from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from se.src.infrastructure.storage.models.sql.promotion_reservation import (
    PromotionReservationRow,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationExactIntentMismatchError,
    PromotionReservationNotFoundError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_verifier import (
    DurablePromotionReservationVerifier,
)


NOW = datetime(2026, 9, 27, 9, 30, tzinfo=timezone.utc)


def _intent(
    *,
    suffix: str = "one",
    metadata=None,
    proof_receipt_id: str | None = None,
    source_state: str = "active",
) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id="user-ctx-f5-3g-a",
        session_id=f"session-{suffix}",
        source_created_at=NOW,
        source_state=source_state,
        metadata={"source": "canonical"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id=proof_receipt_id or f"receipt-{suffix}",
        authority_state_token=f"token-{suffix}",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=f"content-{suffix}",
        metadata={"kind": "verifier"} if metadata is None else metadata,
        memory_schema_version=1,
    )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(PromotionReservationRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_same_session_verify_is_non_consuming_and_caller_owned():
    engine, sessions = await _database()
    try:
        intent = _intent()
        authority_id = "authority-issued"
        reservation = PromotionReservation(
            promotion_authority_id=authority_id,
            intent=intent,
        )

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id=authority_id,
                intent=intent,
            )
            await session.commit()

            verifier = DurablePromotionReservationVerifier(repository)
            await verifier.verify(reservation=reservation, intent=intent)
            assert session.in_transaction()
            await verifier.verify(reservation=reservation, intent=intent)

            current = await repository.get(authority_id)
            assert current is not None
            assert current.state is DurablePromotionReservationState.ISSUED
            assert session.in_transaction()

            await session.rollback()

        async with sessions() as session:
            current = await DurablePromotionReservationRepository(session).get(
                authority_id
            )
            assert current is not None
            assert current.state is DurablePromotionReservationState.ISSUED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_forged_id_fails_even_when_exact_intent_winner_exists():
    engine, sessions = await _database()
    try:
        intent = _intent()
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id="authority-real",
                intent=intent,
            )
            await session.commit()

            verifier = DurablePromotionReservationVerifier(repository)
            forged = PromotionReservation(
                promotion_authority_id="authority-forged",
                intent=intent,
            )
            with pytest.raises(PromotionReservationNotFoundError):
                await verifier.verify(reservation=forged, intent=intent)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "metadata",
        "proof",
        "source",
    ],
)
async def test_ctx_f5_3g_a_exact_intent_scalars_are_authority_significant(variant):
    engine, sessions = await _database()
    try:
        durable_intent = _intent(suffix="base")
        if variant == "metadata":
            supplied_intent = _intent(
                suffix="base",
                metadata={"kind": "different"},
            )
        elif variant == "proof":
            supplied_intent = _intent(
                suffix="base",
                proof_receipt_id="receipt-different",
            )
        else:
            supplied_intent = _intent(
                suffix="base",
                source_state="archived",
            )

        authority_id = f"authority-{variant}"
        reservation = PromotionReservation(
            promotion_authority_id=authority_id,
            intent=supplied_intent,
        )

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id=authority_id,
                intent=durable_intent,
            )
            await session.commit()

            verifier = DurablePromotionReservationVerifier(repository)
            with pytest.raises(PromotionReservationExactIntentMismatchError):
                await verifier.verify(
                    reservation=reservation,
                    intent=supplied_intent,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_consumed_and_revoked_states_remain_terminal():
    engine, sessions = await _database()
    try:
        for suffix, transition, error_type in (
            (
                "consumed",
                "consume",
                PromotionReservationAlreadyConsumedError,
            ),
            (
                "revoked",
                "revoke",
                PromotionReservationRevokedError,
            ),
        ):
            intent = _intent(suffix=suffix)
            authority_id = f"authority-{suffix}"
            reservation = PromotionReservation(
                promotion_authority_id=authority_id,
                intent=intent,
            )
            async with sessions() as session:
                repository = DurablePromotionReservationRepository(session)
                await repository.insert_or_converge_issued_candidate(
                    promotion_authority_id=authority_id,
                    intent=intent,
                )
                await session.commit()
                if transition == "consume":
                    await repository.mark_consumed(authority_id)
                else:
                    await repository.mark_revoked(authority_id)
                await session.commit()

                verifier = DurablePromotionReservationVerifier(repository)
                with pytest.raises(error_type):
                    await verifier.verify(
                        reservation=reservation,
                        intent=intent,
                    )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_reconstruction_corruption_propagates_from_exact_id_read():
    engine, sessions = await _database()
    try:
        intent = _intent(suffix="corrupt")
        authority_id = "authority-corrupt"
        reservation = PromotionReservation(
            promotion_authority_id=authority_id,
            intent=intent,
        )

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            await repository.insert_or_converge_issued_candidate(
                promotion_authority_id=authority_id,
                intent=intent,
            )
            await session.commit()
            await session.execute(
                update(PromotionReservationRow)
                .where(
                    PromotionReservationRow.promotion_authority_id
                    == authority_id
                )
                .values(intent_digest="0" * 64)
            )
            await session.commit()

            verifier = DurablePromotionReservationVerifier(repository)
            with pytest.raises(
                PromotionReservationReconstructionCorruptionError
            ):
                await verifier.verify(
                    reservation=reservation,
                    intent=intent,
                )
    finally:
        await engine.dispose()
