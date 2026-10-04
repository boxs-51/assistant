from __future__ import annotations

from se.src.context.memory import MemoryRecord
from se.src.context.source_identity import ContextSourceRef
from se.src.infrastructure.storage.services.memory_promotion_admission import (
    DurableMemoryPromotionAdmission,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)


class DurableToolResponsePayloadMemoryPromotion:
    """Compose trusted reservation/recovery handoff with atomic Memory admission."""

    def __init__(
        self,
        orchestration: DurableToolResponsePayloadPromotionOrchestration,
        admission: DurableMemoryPromotionAdmission,
    ) -> None:
        self._orchestration = orchestration
        self._admission = admission

    async def promote(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> MemoryRecord:
        """Promote one freshly re-proved TOOL_RESPONSE_PAYLOAD into durable Memory."""
        handoff = await self._orchestration.reserve(
            source_ref=source_ref,
            owner_user_id=owner_user_id,
        )
        content_snapshot = handoff.content_snapshot
        return await self._admission.admit(
            reservation=handoff.reservation,
            content=content_snapshot,
        )
