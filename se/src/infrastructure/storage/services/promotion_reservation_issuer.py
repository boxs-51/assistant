from __future__ import annotations

from typing import AsyncContextManager, Callable
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    PromotionReservation,
    PromotionReservationIssuer,
    validate_memory_promotion_intent_integrity,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
    PromotionReservationPersistenceUnavailableError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)


SessionContextFactory = Callable[[], AsyncContextManager[AsyncSession]]
AuthorityIdFactory = Callable[[], str]


def _default_authority_id() -> str:
    return str(uuid4())


class DurablePromotionReservationIssuer(PromotionReservationIssuer):
    """Trusted durable issuer for one canonical promotion reservation."""

    def __init__(
        self,
        session_factory: SessionContextFactory,
        *,
        authority_id_factory: AuthorityIdFactory = _default_authority_id,
    ) -> None:
        self._session_factory = session_factory
        self._authority_id_factory = authority_id_factory

    async def reserve(
        self,
        *,
        intent: MemoryPromotionIntent,
    ) -> PromotionReservation:
        """Durably issue or converge one exact canonical reservation."""
        validate_memory_promotion_intent_integrity(intent)
        candidate_authority_id = self._authority_id_factory()

        async with self._session_factory() as session:
            repository = DurablePromotionReservationRepository(session)
            winner = await repository.insert_or_converge_issued_candidate(
                promotion_authority_id=candidate_authority_id,
                intent=intent,
            )

            if winner.state is DurablePromotionReservationState.CONSUMED:
                raise PromotionReservationAlreadyConsumedError(
                    "durable promotion reservation is already CONSUMED"
                )
            if winner.state is DurablePromotionReservationState.REVOKED:
                raise PromotionReservationRevokedError(
                    "durable promotion reservation is REVOKED"
                )
            if winner.state is not DurablePromotionReservationState.ISSUED:
                raise PromotionReservationReconstructionCorruptionError(
                    "durable promotion reservation has an invalid state"
                )

            try:
                await session.commit()
            except SQLAlchemyError as exc:
                raise PromotionReservationPersistenceUnavailableError(
                    "failed to commit durable promotion reservation issuance"
                ) from exc

            return PromotionReservation(
                promotion_authority_id=winner.promotion_authority_id,
                intent=winner.intent,
            )
