from __future__ import annotations

import json
from typing import Any, AsyncContextManager, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory import (
    MemoryRecord,
    MemoryRecordConflictError,
    canonical_memory_bytes,
    create_memory_record,
    memory_content_digest,
    memory_records_replay_equivalent,
)
from se.src.context.memory_promotion import (
    PromotionAdmissionConsumedMemoryMismatchError,
    PromotionAdmissionConsumedMemoryMissingError,
    PromotionAdmissionIntentConflictError,
    PromotionAdmissionMemoryReplayConflictError,
    PromotionAdmissionPersistenceFailureError,
    PromotionAdmissionReservationNotIssuedError,
    PromotionAdmissionReservationRevokedError,
    PromotionAdmissionTransactionUnavailableError,
    PromotionReservation,
    PromotionReservationIntentMismatchError,
    validate_promotion_reservation_integrity,
    validate_reservation_matches_intent,
)
from se.src.infrastructure.storage.repositories.memory import (
    MemoryAdmissionTransactionError,
    sqlite_memory_admission_transaction,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationPersistenceError,
)


MemoryAdmissionSessionContextFactory = Callable[
    [],
    AsyncContextManager[AsyncSession],
]


class DurableMemoryPromotionAdmission:
    """Authoritative SQLite-only durable Memory promotion admission service."""

    def __init__(
        self,
        session_factory: MemoryAdmissionSessionContextFactory,
    ) -> None:
        self._session_factory = session_factory

    async def admit(
        self,
        *,
        reservation: PromotionReservation,
        content: Any,
    ) -> MemoryRecord:
        """Atomically admit or replay one exact durable Memory promotion."""
        validate_promotion_reservation_integrity(reservation)

        try:
            canonical_payload_bytes = canonical_memory_bytes(content)
            content_snapshot = json.loads(canonical_payload_bytes.decode("utf-8"))
            payload_digest = memory_content_digest(content_snapshot)
        except ValueError as exc:
            raise PromotionAdmissionIntentConflictError(
                "promotion content is not valid canonical Memory JSON"
            ) from exc

        # The caller-owned object is no longer admission authority after the
        # detached canonical snapshot has been materialized.
        del content

        transaction_entered = False
        try:
            async with self._session_factory() as session:
                async with sqlite_memory_admission_transaction(
                    session
                ) as memory_repository:
                    transaction_entered = True
                    reservation_repository = DurablePromotionReservationRepository(
                        session
                    )

                    durable = await reservation_repository.get(
                        reservation.promotion_authority_id
                    )
                    if durable is None:
                        raise PromotionAdmissionReservationNotIssuedError(
                            "durable promotion reservation was not found"
                        )

                    try:
                        validate_reservation_matches_intent(
                            reservation,
                            durable.intent,
                        )
                    except PromotionReservationIntentMismatchError as exc:
                        raise PromotionAdmissionIntentConflictError(
                            "promotion reservation does not match durable intent"
                        ) from exc

                    if payload_digest != durable.intent.content_digest:
                        raise PromotionAdmissionIntentConflictError(
                            "promotion content digest does not match durable intent"
                        )

                    if durable.state is DurablePromotionReservationState.REVOKED:
                        raise PromotionAdmissionReservationRevokedError(
                            "durable promotion reservation is REVOKED"
                        )

                    expected = create_memory_record(
                        source_ref=durable.intent.source_ref_snapshot,
                        promotion_authority_id=durable.promotion_authority_id,
                        content=content_snapshot,
                        metadata=durable.intent.metadata,
                        memory_schema_version=durable.intent.memory_schema_version,
                    )

                    if durable.state is DurablePromotionReservationState.ISSUED:
                        existing = (
                            await memory_repository.get_by_promotion_authority(
                                durable.promotion_authority_id
                            )
                        )
                        if existing is not None:
                            raise PromotionAdmissionMemoryReplayConflictError(
                                "ISSUED reservation already has durable Memory"
                            )

                        try:
                            winner = await memory_repository.put(expected)
                        except MemoryRecordConflictError as exc:
                            raise PromotionAdmissionMemoryReplayConflictError(
                                "first Memory admission conflicted with durable replay"
                            ) from exc

                        await reservation_repository.mark_consumed(
                            durable.promotion_authority_id
                        )
                        return winner

                    if durable.state is DurablePromotionReservationState.CONSUMED:
                        existing = (
                            await memory_repository.get_by_promotion_authority(
                                durable.promotion_authority_id
                            )
                        )
                        if existing is None:
                            raise PromotionAdmissionConsumedMemoryMissingError(
                                "CONSUMED reservation has no durable Memory"
                            )
                        if not memory_records_replay_equivalent(
                            existing,
                            expected,
                        ):
                            raise PromotionAdmissionConsumedMemoryMismatchError(
                                "CONSUMED durable Memory is not replay-equivalent"
                            )
                        return existing

                    raise PromotionAdmissionPersistenceFailureError(
                        "durable promotion reservation has an invalid state"
                    )
        except MemoryAdmissionTransactionError as exc:
            raise PromotionAdmissionTransactionUnavailableError(
                "authoritative SQLite Memory admission transaction is unavailable"
            ) from exc
        except SQLAlchemyError as exc:
            if not transaction_entered:
                raise PromotionAdmissionTransactionUnavailableError(
                    "authoritative SQLite Memory admission transaction is unavailable"
                ) from exc
            raise PromotionAdmissionPersistenceFailureError(
                "durable Memory promotion persistence failed"
            ) from exc
        except PromotionReservationPersistenceError as exc:
            raise PromotionAdmissionPersistenceFailureError(
                "durable promotion reservation persistence failed"
            ) from exc
        except ValueError as exc:
            if transaction_entered:
                raise PromotionAdmissionPersistenceFailureError(
                    "durable Memory promotion reconstruction failed"
                ) from exc
            raise
