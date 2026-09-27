from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from se.src.context.memory import canonical_memory_bytes
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionProofScope,
    validate_memory_promotion_intent_integrity,
)

from ..models.sql.promotion_reservation import PromotionReservationRow


class DurablePromotionReservationState(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"


class PromotionReservationPersistenceError(RuntimeError):
    """Base error for trusted durable promotion-reservation persistence."""


class PromotionReservationNotFoundError(PromotionReservationPersistenceError):
    """No durable reservation exists for the requested authority identity."""


class PromotionReservationExactIntentMismatchError(
    PromotionReservationPersistenceError
):
    """One durable authority identity was presented with different exact intent."""


class PromotionReservationProofReuseConflictError(
    PromotionReservationPersistenceError
):
    """One proof authority tuple was reused for a different exact intent."""


class PromotionReservationAlreadyConsumedError(
    PromotionReservationPersistenceError
):
    """A terminal CONSUMED reservation cannot be reused or resurrected."""


class PromotionReservationRevokedError(PromotionReservationPersistenceError):
    """A terminal REVOKED reservation cannot be reused or resurrected."""


class PromotionReservationStateTransitionConflictError(
    PromotionReservationPersistenceError
):
    """The durable state no longer permits the requested conditional transition."""


class PromotionReservationPersistenceUnavailableError(
    PromotionReservationPersistenceError
):
    """The persistence layer failed before a durable result could be established."""


class PromotionReservationReconstructionCorruptionError(
    PromotionReservationPersistenceError
):
    """Persisted reservation material cannot reconstruct its exact canonical intent."""


class PromotionReservationCanonicalDigestCollisionError(
    PromotionReservationPersistenceError
):
    """Equal canonical digests resolved to different exact canonical intent bytes."""


@dataclass(frozen=True)
class DurablePromotionReservationRecord:
    """Server-internal trusted persistence record; not a caller authorization envelope."""

    promotion_authority_id: str
    intent: MemoryPromotionIntent
    intent_digest: str
    source_context_source_id: str
    proof_receipt_id: str
    authority_state_token: str
    proof_scope: MemoryPromotionProofScope
    state: DurablePromotionReservationState


def _require_normalized_non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    if value != normalized:
        raise ValueError(f"{name} must already be normalized")
    return value


def _canonical_intent_bytes(intent: MemoryPromotionIntent) -> bytes:
    validate_memory_promotion_intent_integrity(intent)
    return canonical_memory_bytes(intent.model_dump(mode="json"))


def _intent_digest(canonical_intent: bytes) -> str:
    return hashlib.sha256(canonical_intent).hexdigest()


def _proof_tuple_from_intent(
    intent: MemoryPromotionIntent,
) -> tuple[str, str, str, MemoryPromotionProofScope]:
    validate_memory_promotion_intent_integrity(intent)
    proof = intent.source_proof
    return (
        intent.source_ref_snapshot.context_source_id,
        proof.proof_receipt_id,
        proof.authority_state_token,
        proof.scope,
    )


def _row_values(
    *,
    promotion_authority_id: str,
    intent: MemoryPromotionIntent,
) -> dict[str, Any]:
    authority_id = _require_normalized_non_empty(
        "promotion_authority_id",
        promotion_authority_id,
    )
    canonical_intent = _canonical_intent_bytes(intent)
    source_context_source_id, proof_receipt_id, authority_state_token, scope = (
        _proof_tuple_from_intent(intent)
    )
    payload = intent.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise ValueError("MemoryPromotionIntent must serialize as a JSON object")

    return {
        "promotion_authority_id": authority_id,
        "intent_digest": _intent_digest(canonical_intent),
        "intent_json": payload,
        "intent_canonical_bytes": canonical_intent,
        "source_context_source_id": source_context_source_id,
        "proof_receipt_id": proof_receipt_id,
        "authority_state_token": authority_state_token,
        "proof_scope": scope.value,
        "state": DurablePromotionReservationState.ISSUED.value,
    }


def _row_to_record(
    row: PromotionReservationRow,
) -> DurablePromotionReservationRecord:
    try:
        authority_id = _require_normalized_non_empty(
            "promotion_authority_id",
            row.promotion_authority_id,
        )
        stored_digest = _require_normalized_non_empty(
            "intent_digest",
            row.intent_digest,
        )
        stored_source_id = _require_normalized_non_empty(
            "source_context_source_id",
            row.source_context_source_id,
        )
        stored_receipt = _require_normalized_non_empty(
            "proof_receipt_id",
            row.proof_receipt_id,
        )
        stored_token = _require_normalized_non_empty(
            "authority_state_token",
            row.authority_state_token,
        )
        if not isinstance(row.intent_json, dict):
            raise ValueError("intent_json must be an object")
        if not isinstance(row.intent_canonical_bytes, (bytes, bytearray, memoryview)):
            raise ValueError("intent_canonical_bytes must be binary")

        scope = MemoryPromotionProofScope(row.proof_scope)
        state = DurablePromotionReservationState(row.state)
        intent_payload = dict(row.intent_json)
        intent = MemoryPromotionIntent.model_validate(intent_payload)
        validate_memory_promotion_intent_integrity(intent)

        reconstructed = _canonical_intent_bytes(intent)
        stored_canonical = bytes(row.intent_canonical_bytes)
        if reconstructed != stored_canonical:
            raise ValueError(
                "intent_json does not reproduce stored exact canonical bytes"
            )

        reconstructed_digest = _intent_digest(reconstructed)
        if stored_digest != reconstructed_digest:
            raise ValueError(
                "intent_digest does not match stored exact canonical bytes"
            )

        source_id, receipt_id, state_token, intent_scope = _proof_tuple_from_intent(
            intent
        )
        if stored_source_id != source_id:
            raise ValueError(
                "source_context_source_id conflicts with reconstructed intent"
            )
        if stored_receipt != receipt_id:
            raise ValueError("proof_receipt_id conflicts with reconstructed intent")
        if stored_token != state_token:
            raise ValueError(
                "authority_state_token conflicts with reconstructed intent"
            )
        if scope is not intent_scope:
            raise ValueError("proof_scope conflicts with reconstructed intent")
        if scope is not MemoryPromotionProofScope.MEMORY_PROMOTION:
            raise ValueError("proof_scope must be MEMORY_PROMOTION")
    except (TypeError, ValueError) as exc:
        raise PromotionReservationReconstructionCorruptionError(
            "durable promotion reservation failed exact reconstruction"
        ) from exc

    return DurablePromotionReservationRecord(
        promotion_authority_id=authority_id,
        intent=intent,
        intent_digest=stored_digest,
        source_context_source_id=stored_source_id,
        proof_receipt_id=stored_receipt,
        authority_state_token=stored_token,
        proof_scope=scope,
        state=state,
    )


class DurablePromotionReservationRepository:
    """Caller-transaction-owned persistence primitives for CTX-F5-3F-B."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _one_or_none(self, statement) -> PromotionReservationRow | None:
        try:
            result = await self.session.execute(statement)
        except SQLAlchemyError as exc:
            raise PromotionReservationPersistenceUnavailableError(
                "durable promotion reservation persistence is unavailable"
            ) from exc
        return result.scalar_one_or_none()

    async def get(
        self,
        promotion_authority_id: str,
    ) -> DurablePromotionReservationRecord | None:
        authority_id = _require_normalized_non_empty(
            "promotion_authority_id",
            promotion_authority_id,
        )
        row = await self._one_or_none(
            select(PromotionReservationRow)
            .where(
                PromotionReservationRow.promotion_authority_id == authority_id
            )
            .limit(1)
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def _get_by_digest(
        self,
        intent_digest: str,
    ) -> DurablePromotionReservationRecord | None:
        digest = _require_normalized_non_empty("intent_digest", intent_digest)
        row = await self._one_or_none(
            select(PromotionReservationRow)
            .where(PromotionReservationRow.intent_digest == digest)
            .limit(1)
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def get_by_intent(
        self,
        intent: MemoryPromotionIntent,
    ) -> DurablePromotionReservationRecord | None:
        canonical_intent = _canonical_intent_bytes(intent)
        digest = _intent_digest(canonical_intent)
        winner = await self._get_by_digest(digest)
        if winner is None:
            return None
        winner_canonical = _canonical_intent_bytes(winner.intent)
        if winner_canonical != canonical_intent:
            raise PromotionReservationCanonicalDigestCollisionError(
                "intent digest collision does not match exact canonical bytes"
            )
        return winner

    async def _get_by_proof_tuple(
        self,
        *,
        source_context_source_id: str,
        proof_receipt_id: str,
        authority_state_token: str,
        proof_scope: MemoryPromotionProofScope,
    ) -> DurablePromotionReservationRecord | None:
        source_id = _require_normalized_non_empty(
            "source_context_source_id",
            source_context_source_id,
        )
        receipt_id = _require_normalized_non_empty(
            "proof_receipt_id",
            proof_receipt_id,
        )
        state_token = _require_normalized_non_empty(
            "authority_state_token",
            authority_state_token,
        )
        if not isinstance(proof_scope, MemoryPromotionProofScope):
            raise ValueError("proof_scope must be a MemoryPromotionProofScope")

        row = await self._one_or_none(
            select(PromotionReservationRow)
            .where(
                PromotionReservationRow.source_context_source_id == source_id,
                PromotionReservationRow.proof_receipt_id == receipt_id,
                PromotionReservationRow.authority_state_token == state_token,
                PromotionReservationRow.proof_scope == proof_scope.value,
            )
            .limit(1)
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def get_by_proof_authority(
        self,
        intent: MemoryPromotionIntent,
    ) -> DurablePromotionReservationRecord | None:
        source_id, receipt_id, state_token, scope = _proof_tuple_from_intent(intent)
        return await self._get_by_proof_tuple(
            source_context_source_id=source_id,
            proof_receipt_id=receipt_id,
            authority_state_token=state_token,
            proof_scope=scope,
        )

    async def _resolve_winner(
        self,
        *,
        promotion_authority_id: str,
        intent: MemoryPromotionIntent,
        canonical_intent: bytes,
        digest: str,
    ) -> DurablePromotionReservationRecord:
        authority_winner = await self.get(promotion_authority_id)
        if authority_winner is not None:
            if _canonical_intent_bytes(authority_winner.intent) != canonical_intent:
                raise PromotionReservationExactIntentMismatchError(
                    "promotion_authority_id already binds a different exact intent"
                )
            return authority_winner

        digest_winner = await self._get_by_digest(digest)
        if digest_winner is not None:
            if _canonical_intent_bytes(digest_winner.intent) != canonical_intent:
                raise PromotionReservationCanonicalDigestCollisionError(
                    "intent digest collision does not match exact canonical bytes"
                )
            return digest_winner

        proof_winner = await self.get_by_proof_authority(intent)
        if proof_winner is not None:
            if _canonical_intent_bytes(proof_winner.intent) != canonical_intent:
                raise PromotionReservationProofReuseConflictError(
                    "proof authority tuple already binds a different exact intent"
                )
            return proof_winner

        raise PromotionReservationStateTransitionConflictError(
            "reservation uniqueness conflict has no readable durable winner"
        )

    async def insert_or_converge_issued_candidate(
        self,
        *,
        promotion_authority_id: str,
        intent: MemoryPromotionIntent,
    ) -> DurablePromotionReservationRecord:
        """Persist/converge one ISSUED candidate without granting runtime authorization."""
        values = _row_values(
            promotion_authority_id=promotion_authority_id,
            intent=intent,
        )
        canonical_intent = bytes(values["intent_canonical_bytes"])
        digest = values["intent_digest"]

        if self.session.get_bind().dialect.name == "sqlite":
            statement = (
                sqlite_insert(PromotionReservationRow)
                .values(**values)
                .on_conflict_do_nothing()
            )
            try:
                await self.session.execute(statement)
            except SQLAlchemyError as exc:
                raise PromotionReservationPersistenceUnavailableError(
                    "failed to persist durable promotion reservation candidate"
                ) from exc
            return await self._resolve_winner(
                promotion_authority_id=values["promotion_authority_id"],
                intent=intent,
                canonical_intent=canonical_intent,
                digest=digest,
            )

        row = PromotionReservationRow(**values)
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            return await self._resolve_winner(
                promotion_authority_id=values["promotion_authority_id"],
                intent=intent,
                canonical_intent=canonical_intent,
                digest=digest,
            )
        except SQLAlchemyError as exc:
            raise PromotionReservationPersistenceUnavailableError(
                "failed to persist durable promotion reservation candidate"
            ) from exc

        return _row_to_record(row)

    async def _transition_from_issued(
        self,
        *,
        promotion_authority_id: str,
        target: DurablePromotionReservationState,
    ) -> DurablePromotionReservationRecord:
        if target not in (
            DurablePromotionReservationState.CONSUMED,
            DurablePromotionReservationState.REVOKED,
        ):
            raise ValueError("target must be CONSUMED or REVOKED")
        authority_id = _require_normalized_non_empty(
            "promotion_authority_id",
            promotion_authority_id,
        )

        try:
            result = await self.session.execute(
                update(PromotionReservationRow)
                .where(
                    PromotionReservationRow.promotion_authority_id == authority_id,
                    PromotionReservationRow.state
                    == DurablePromotionReservationState.ISSUED.value,
                )
                .values(state=target.value)
                .execution_options(synchronize_session=False)
            )
        except SQLAlchemyError as exc:
            raise PromotionReservationPersistenceUnavailableError(
                "failed to transition durable promotion reservation state"
            ) from exc

        current = await self.get(authority_id)
        if current is None:
            raise PromotionReservationNotFoundError(
                "durable promotion reservation was not found"
            )
        if result.rowcount == 1:
            if current.state is not target:
                raise PromotionReservationStateTransitionConflictError(
                    "reservation state transition was not durably observable"
                )
            return current

        if current.state is DurablePromotionReservationState.CONSUMED:
            raise PromotionReservationAlreadyConsumedError(
                "durable promotion reservation is already CONSUMED"
            )
        if current.state is DurablePromotionReservationState.REVOKED:
            raise PromotionReservationRevokedError(
                "durable promotion reservation is REVOKED"
            )
        raise PromotionReservationStateTransitionConflictError(
            "durable promotion reservation ISSUED transition lost a concurrent race"
        )

    async def mark_consumed(
        self,
        promotion_authority_id: str,
    ) -> DurablePromotionReservationRecord:
        return await self._transition_from_issued(
            promotion_authority_id=promotion_authority_id,
            target=DurablePromotionReservationState.CONSUMED,
        )

    async def mark_revoked(
        self,
        promotion_authority_id: str,
    ) -> DurablePromotionReservationRecord:
        return await self._transition_from_issued(
            promotion_authority_id=promotion_authority_id,
            target=DurablePromotionReservationState.REVOKED,
        )
