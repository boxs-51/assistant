from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from se.src.context.memory import MEMORY_SCHEMA_VERSION
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    PromotionReservation,
)
from se.src.context.source_identity import ContextSourceRef
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)
from se.src.infrastructure.storage.services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
    DurablePromotionReservationRecoveryHandoff,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
    TrustedToolResponsePromotionMaterial,
)


@dataclass(frozen=True, slots=True)
class TrustedToolResponsePromotionReservation:
    """One durable reservation plus same-attempt transient trusted source material."""

    reservation: PromotionReservation
    trusted_material: TrustedToolResponsePromotionMaterial

    @property
    def content_snapshot(self) -> Any:
        """Return a fresh mutable snapshot without exposing durable replay authority."""
        return self.trusted_material.content_snapshot

    @property
    def content_digest(self) -> str:
        """Return the exact B2 digest bound into the reservation intent."""
        return self.trusted_material.content_digest


class DurableToolResponsePayloadPromotionOrchestration:
    """Bounded handoff from fresh trusted material to recovery or issuance."""

    def __init__(
        self,
        source_authority: DurableToolResponsePayloadSourceAuthority,
        reservation_issuer: DurablePromotionReservationIssuer,
        reservation_recovery: DurablePromotionReservationRecovery | None = None,
    ) -> None:
        self._source_authority = source_authority
        self._reservation_handoff = DurablePromotionReservationRecoveryHandoff(
            reservation_issuer,
            reservation_recovery,
        )

    async def reserve(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> TrustedToolResponsePromotionReservation:
        """Re-prove one source, recover-or-issue its intent, and return transient content."""
        material = await self._source_authority.read_trusted_promotion_material(
            source_ref=source_ref,
            owner_user_id=owner_user_id,
        )
        intent = MemoryPromotionIntent(
            owner_user_id=material.source_proof.source_ref_snapshot.owner_user_id,
            source_ref_snapshot=material.source_proof.source_ref_snapshot,
            source_proof=material.source_proof,
            content_digest=material.content_digest,
            metadata={},
            memory_schema_version=MEMORY_SCHEMA_VERSION,
        )
        reservation = await self._reservation_handoff.reserve(intent=intent)
        return TrustedToolResponsePromotionReservation(
            reservation=reservation,
            trusted_material=material,
        )
