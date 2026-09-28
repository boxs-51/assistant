from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationProofReuseConflictError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)
from se.src.infrastructure.storage.services.promotion_reservation_verifier import (
    DurablePromotionReservationVerifier,
)


NOW = datetime(2026, 9, 28, 4, 30, tzinfo=timezone.utc)


def _intent(
    *,
    suffix: str = "one",
    content_digest: str | None = None,
    metadata=None,
) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id="user-ctx-f5-3g-b",
        session_id=f"session-{suffix}",
        source_created_at=NOW,
        source_state="active",
        metadata={"source": "canonical"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id=f"receipt-{suffix}",
        authority_state_token=f"token-{suffix}",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=content_digest or f"content-{suffix}",
        metadata={"kind": "issuer"} if metadata is None else metadata,
        memory_schema_version=1,
    )


async def _database(tmp_path, name: str):
    database_path = tmp_path / f"{name}.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(PromotionReservationRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _count_rows(sessions) -> int:
    async with sessions() as session:
        result = await session.execute(
            select(func.count()).select_from(PromotionReservationRow)
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_fresh_issue_commits_before_return_and_verifies(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "fresh")
    try:
        intent = _intent()
        issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-fresh",
        )

        reservation = await issuer.reserve(intent=intent)

        assert reservation.promotion_authority_id == "authority-fresh"
        assert reservation.intent == intent
        assert await _count_rows(sessions) == 1

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            durable = await repository.get(reservation.promotion_authority_id)
            assert durable is not None
            assert durable.state is DurablePromotionReservationState.ISSUED
            verifier = DurablePromotionReservationVerifier(repository)
            await verifier.verify(reservation=reservation, intent=intent)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_sequential_retry_converges_to_same_durable_identity(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "sequential")
    try:
        intent = _intent()
        candidate_ids = iter(("authority-first", "authority-second"))
        issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: next(candidate_ids),
        )

        first = await issuer.reserve(intent=intent)
        second = await issuer.reserve(intent=intent)

        assert first.promotion_authority_id == "authority-first"
        assert second.promotion_authority_id == first.promotion_authority_id
        assert await _count_rows(sessions) == 1

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            assert await repository.get("authority-first") is not None
            assert await repository.get("authority-second") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_concurrent_retry_converges_to_one_durable_identity(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "concurrent")
    try:
        intent = _intent(suffix="concurrent")
        issuer_one = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-concurrent-one",
        )
        issuer_two = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-concurrent-two",
        )

        first, second = await asyncio.gather(
            issuer_one.reserve(intent=intent),
            issuer_two.reserve(intent=intent),
        )

        assert first.promotion_authority_id == second.promotion_authority_id
        assert first.intent == second.intent == intent
        assert await _count_rows(sessions) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_proof_reuse_with_different_intent_fails_closed(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "proof-conflict")
    try:
        original = _intent(suffix="shared-proof", content_digest="content-original")
        conflicting = _intent(
            suffix="shared-proof",
            content_digest="content-conflicting",
            metadata={"kind": "different"},
        )
        first_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-proof-original",
        )
        second_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-proof-conflict",
        )

        original_reservation = await first_issuer.reserve(intent=original)

        with pytest.raises(PromotionReservationProofReuseConflictError):
            await second_issuer.reserve(intent=conflicting)

        assert await _count_rows(sessions) == 1
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            durable = await repository.get(
                original_reservation.promotion_authority_id
            )
            assert durable is not None
            assert durable.intent == original
            assert await repository.get("authority-proof-conflict") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        (
            DurablePromotionReservationState.CONSUMED,
            PromotionReservationAlreadyConsumedError,
        ),
        (
            DurablePromotionReservationState.REVOKED,
            PromotionReservationRevokedError,
        ),
    ],
)
async def test_ctx_f5_3g_b_terminal_retry_fails_without_replacement_authority(
    tmp_path,
    state,
    error_type,
):
    engine, sessions = await _database(tmp_path, f"terminal-{state.value.lower()}")
    try:
        intent = _intent(suffix=state.value.lower())
        authority_id = f"authority-{state.value.lower()}"

        initial_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: authority_id,
        )
        await initial_issuer.reserve(intent=intent)

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            if state is DurablePromotionReservationState.CONSUMED:
                await repository.mark_consumed(authority_id)
            else:
                await repository.mark_revoked(authority_id)
            await session.commit()

        retry_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-replacement-forbidden",
        )
        with pytest.raises(error_type):
            await retry_issuer.reserve(intent=intent)

        assert await _count_rows(sessions) == 1
        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            durable = await repository.get(authority_id)
            assert durable is not None
            assert durable.state is state
            assert await repository.get("authority-replacement-forbidden") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ctx_f5_3g_b_response_loss_retry_recovers_committed_winner(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "response-loss")
    try:
        intent = _intent(suffix="response-loss")
        first_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-committed-unobserved",
        )

        # Simulate a response that was durably committed but not retained by caller.
        await first_issuer.reserve(intent=intent)

        retry_issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-retry-candidate",
        )
        recovered = await retry_issuer.reserve(intent=intent)

        assert recovered.promotion_authority_id == "authority-committed-unobserved"
        assert recovered.intent == intent
        assert await _count_rows(sessions) == 1
    finally:
        await engine.dispose()
