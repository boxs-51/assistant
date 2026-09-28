from __future__ import annotations

from se.src.context.memory import canonical_memory_bytes
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    PromotionReservation,
    PromotionReservationVerifier,
    validate_reservation_matches_intent,
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


def _canonical_intent_bytes(intent: MemoryPromotionIntent) -> bytes:
    return canonical_memory_bytes(intent.model_dump(mode="json"))


class DurablePromotionReservationVerifier(PromotionReservationVerifier):
    """Trusted, non-consuming verifier for one exact durable reservation."""

    def __init__(
        self,
        repository: DurablePromotionReservationRepository,
    ) -> None:
        self._repository = repository

    async def verify(
        self,
        *,
        reservation: PromotionReservation,
        intent: MemoryPromotionIntent,
    ) -> None:
        """Authorize only an exact ISSUED durable reservation identity."""
        validate_reservation_matches_intent(reservation, intent)

        durable = await self._repository.get(
            reservation.promotion_authority_id
        )
        if durable is None:
            raise PromotionReservationNotFoundError(
                "durable promotion reservation was not found"
            )

        if _canonical_intent_bytes(durable.intent) != _canonical_intent_bytes(
            intent
        ):
            raise PromotionReservationExactIntentMismatchError(
                "durable promotion reservation binds a different exact intent"
            )

        if durable.state is DurablePromotionReservationState.ISSUED:
            return
        if durable.state is DurablePromotionReservationState.CONSUMED:
            raise PromotionReservationAlreadyConsumedError(
                "durable promotion reservation is already CONSUMED"
            )
        if durable.state is DurablePromotionReservationState.REVOKED:
            raise PromotionReservationRevokedError(
                "durable promotion reservation is REVOKED"
            )

        raise PromotionReservationReconstructionCorruptionError(
            "durable promotion reservation has an invalid state"
        )
