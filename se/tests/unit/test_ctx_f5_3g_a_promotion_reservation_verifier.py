from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionIntentIntegrityError,
    MemoryPromotionProofScope,
    PromotionReservation,
    PromotionReservationIntegrityError,
    PromotionReservationIntentMismatchError,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRecord,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationExactIntentMismatchError,
    PromotionReservationNotFoundError,
    PromotionReservationPersistenceUnavailableError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_verifier import (
    DurablePromotionReservationVerifier,
)


NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)


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


def _record(
    *,
    authority_id: str,
    intent: MemoryPromotionIntent,
    state: DurablePromotionReservationState,
) -> DurablePromotionReservationRecord:
    return DurablePromotionReservationRecord(
        promotion_authority_id=authority_id,
        intent=intent,
        intent_digest="d" * 64,
        source_context_source_id=intent.source_ref_snapshot.context_source_id,
        proof_receipt_id=intent.source_proof.proof_receipt_id,
        authority_state_token=intent.source_proof.authority_state_token,
        proof_scope=intent.source_proof.scope,
        state=state,
    )


class _ExactIdRepository:
    def __init__(self, records=None, *, failure: BaseException | None = None):
        self.records = dict(records or {})
        self.failure = failure
        self.get_calls: list[str] = []
        self.fallback_calls: list[str] = []

    async def get(self, authority_id: str):
        self.get_calls.append(authority_id)
        if self.failure is not None:
            raise self.failure
        return self.records.get(authority_id)

    async def get_by_intent(self, intent):
        self.fallback_calls.append("intent")
        raise AssertionError("verifier must never fall back to intent winner lookup")

    async def get_by_proof_authority(self, intent):
        self.fallback_calls.append("proof")
        raise AssertionError("verifier must never fall back to proof winner lookup")


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_exact_issued_authority_is_idempotent_and_non_consuming():
    intent = _intent()
    authority_id = "authority-issued"
    record = _record(
        authority_id=authority_id,
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _ExactIdRepository({authority_id: record})
    verifier = DurablePromotionReservationVerifier(repository)
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )

    await verifier.verify(reservation=reservation, intent=intent)
    await verifier.verify(reservation=reservation, intent=intent)

    assert repository.get_calls == [authority_id, authority_id]
    assert repository.fallback_calls == []
    assert record.state is DurablePromotionReservationState.ISSUED


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_unknown_exact_id_does_not_substitute_exact_intent_winner():
    intent = _intent()
    real = _record(
        authority_id="authority-real",
        intent=intent,
        state=DurablePromotionReservationState.ISSUED,
    )
    repository = _ExactIdRepository({"authority-real": real})
    verifier = DurablePromotionReservationVerifier(repository)
    forged = PromotionReservation(
        promotion_authority_id="authority-forged",
        intent=intent,
    )

    with pytest.raises(PromotionReservationNotFoundError):
        await verifier.verify(reservation=forged, intent=intent)

    assert repository.get_calls == ["authority-forged"]
    assert repository.fallback_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_same_id_with_different_exact_intent_fails_closed():
    durable_intent = _intent(suffix="durable")
    supplied_intent = _intent(suffix="supplied")
    authority_id = "authority-mismatch"
    repository = _ExactIdRepository(
        {
            authority_id: _record(
                authority_id=authority_id,
                intent=durable_intent,
                state=DurablePromotionReservationState.ISSUED,
            )
        }
    )
    verifier = DurablePromotionReservationVerifier(repository)
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=supplied_intent,
    )

    with pytest.raises(PromotionReservationExactIntentMismatchError):
        await verifier.verify(reservation=reservation, intent=supplied_intent)

    assert repository.fallback_calls == []


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
async def test_ctx_f5_3g_a_terminal_states_fail_with_existing_semantics(
    state,
    error_type,
):
    intent = _intent()
    authority_id = f"authority-{state.value.lower()}"
    repository = _ExactIdRepository(
        {
            authority_id: _record(
                authority_id=authority_id,
                intent=intent,
                state=state,
            )
        }
    )
    verifier = DurablePromotionReservationVerifier(repository)
    reservation = PromotionReservation(
        promotion_authority_id=authority_id,
        intent=intent,
    )

    with pytest.raises(error_type):
        await verifier.verify(reservation=reservation, intent=intent)


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_reuses_existing_caller_envelope_validation_errors():
    intent = _intent()
    repository = _ExactIdRepository()
    verifier = DurablePromotionReservationVerifier(repository)

    with pytest.raises(PromotionReservationIntegrityError):
        await verifier.verify(reservation=object(), intent=intent)  # type: ignore[arg-type]

    reservation = PromotionReservation(
        promotion_authority_id="authority-valid",
        intent=intent,
    )
    with pytest.raises(MemoryPromotionIntentIntegrityError):
        await verifier.verify(reservation=reservation, intent=object())  # type: ignore[arg-type]

    different = _intent(suffix="different")
    with pytest.raises(PromotionReservationIntentMismatchError):
        await verifier.verify(reservation=reservation, intent=different)

    assert repository.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        PromotionReservationPersistenceUnavailableError("unavailable"),
        PromotionReservationReconstructionCorruptionError("corrupt"),
    ],
)
async def test_ctx_f5_3g_a_propagates_exact_id_read_failures(failure):
    intent = _intent()
    repository = _ExactIdRepository(failure=failure)
    verifier = DurablePromotionReservationVerifier(repository)
    reservation = PromotionReservation(
        promotion_authority_id="authority-read-failure",
        intent=intent,
    )

    with pytest.raises(type(failure)):
        await verifier.verify(reservation=reservation, intent=intent)

    assert repository.fallback_calls == []


@pytest.mark.asyncio
async def test_ctx_f5_3g_a_cancellation_is_non_mutating_and_not_retried():
    intent = _intent()
    repository = _ExactIdRepository(failure=asyncio.CancelledError())
    verifier = DurablePromotionReservationVerifier(repository)
    reservation = PromotionReservation(
        promotion_authority_id="authority-cancel",
        intent=intent,
    )

    with pytest.raises(asyncio.CancelledError):
        await verifier.verify(reservation=reservation, intent=intent)

    assert repository.get_calls == ["authority-cancel"]
    assert repository.fallback_calls == []
