from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory import canonical_memory_bytes
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    PromotionReservation,
    validate_memory_promotion_intent_integrity,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRecord,
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationProofReuseConflictError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)

SessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _canonical_intent_bytes(intent: MemoryPromotionIntent) -> bytes:
    validate_memory_promotion_intent_integrity(intent)
    return canonical_memory_bytes(intent.model_dump(mode="json"))


def _same_durable_authority(
    left: DurablePromotionReservationRecord,
    right: DurablePromotionReservationRecord,
) -> bool:
    return (
        left.promotion_authority_id == right.promotion_authority_id
        and left.intent_digest == right.intent_digest
        and _canonical_intent_bytes(left.intent)
        == _canonical_intent_bytes(right.intent)
    )


class DurablePromotionReservationRecovery:
    """Read-only resolver for one exact durable promotion reservation."""

    def __init__(self, session_factory: SessionContextFactory) -> None:
        self._session_factory = session_factory

    async def recover(
        self,
        *,
        intent: MemoryPromotionIntent,
    ) -> PromotionReservation | None:
        """Recover an exact durable winner, or return None only for true no-winner."""
        requested_canonical = _canonical_intent_bytes(intent)

        async with self._session_factory() as session:
            repository = DurablePromotionReservationRepository(session)
            exact_winner = await repository.get_by_intent(intent)
            proof_winner = await repository.get_by_proof_authority(intent)

            # Under READ COMMITTED (and SQLite legacy transaction mode), a
            # concurrent issuer can commit between the two reads.  Recheck the
            # exact key before treating a proof-only observation as reuse.
            if exact_winner is None and proof_winner is not None:
                exact_winner = await repository.get_by_intent(intent)

        if exact_winner is None and proof_winner is None:
            return None

        if exact_winner is None:
            raise PromotionReservationProofReuseConflictError(
                "proof authority already binds a different exact intent"
            )
        if proof_winner is None:
            raise PromotionReservationReconstructionCorruptionError(
                "exact-intent winner has no matching proof-authority winner"
            )
        if not _same_durable_authority(exact_winner, proof_winner):
            raise PromotionReservationProofReuseConflictError(
                "exact-intent and proof-authority winners disagree"
            )
        if _canonical_intent_bytes(exact_winner.intent) != requested_canonical:
            raise PromotionReservationReconstructionCorruptionError(
                "durable winner does not match the requested exact intent"
            )

        if exact_winner.state is DurablePromotionReservationState.REVOKED:
            raise PromotionReservationRevokedError(
                "durable promotion reservation is REVOKED"
            )
        if exact_winner.state not in (
            DurablePromotionReservationState.ISSUED,
            DurablePromotionReservationState.CONSUMED,
        ):
            raise PromotionReservationReconstructionCorruptionError(
                "durable promotion reservation has an invalid state"
            )

        return PromotionReservation(
            promotion_authority_id=exact_winner.promotion_authority_id,
            intent=exact_winner.intent,
        )


class DurablePromotionReservationRecoveryHandoff:
    """Resolve without minting first; issue only after a proven no-winner result."""

    def __init__(
        self,
        reservation_issuer: DurablePromotionReservationIssuer,
        reservation_recovery: DurablePromotionReservationRecovery | None = None,
    ) -> None:
        self._reservation_issuer = reservation_issuer
        self._reservation_recovery = reservation_recovery

    async def reserve(
        self,
        *,
        intent: MemoryPromotionIntent,
    ) -> PromotionReservation:
        if self._reservation_recovery is not None:
            recovered = await self._reservation_recovery.recover(intent=intent)
            if recovered is not None:
                return recovered
        return await self._reservation_issuer.reserve(intent=intent)
