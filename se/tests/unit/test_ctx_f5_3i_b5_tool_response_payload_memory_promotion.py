from __future__ import annotations

import asyncio

import pytest

from se.src.infrastructure.storage.repositories.promotion_reservation import (
    PromotionReservationProofReuseConflictError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.tool_response_payload_memory_promotion import (
    DurableToolResponsePayloadMemoryPromotion,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    ToolResponsePayloadSourceRejectedError,
)


class _Handoff:
    def __init__(self, reservation, content) -> None:
        self.reservation = reservation
        self._content = content
        self.snapshot_reads = 0

    @property
    def content_snapshot(self):
        self.snapshot_reads += 1
        if isinstance(self._content, dict):
            return dict(self._content)
        return self._content


class _Orchestration:
    def __init__(self, handoff=None, *, failure=None, events=None) -> None:
        self.handoff = handoff
        self.failure = failure
        self.events = events if events is not None else []
        self.calls = []

    async def reserve(self, **kwargs):
        self.events.append("reserve")
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.handoff


class _Admission:
    def __init__(self, result=None, *, failure=None, events=None) -> None:
        self.result = result
        self.failure = failure
        self.events = events if events is not None else []
        self.calls = []

    async def admit(self, **kwargs):
        self.events.append("admit")
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.result


@pytest.mark.asyncio
async def test_b5_reserves_before_single_snapshot_and_exact_admission():
    events = []
    reservation = object()
    handoff = _Handoff(reservation, {"fact": "alpha"})
    expected_memory = object()
    orchestration = _Orchestration(handoff, events=events)
    admission = _Admission(expected_memory, events=events)
    service = DurableToolResponsePayloadMemoryPromotion(
        orchestration,  # type: ignore[arg-type]
        admission,  # type: ignore[arg-type]
    )
    source_ref = object()

    returned = await service.promote(
        source_ref=source_ref,  # type: ignore[arg-type]
        owner_user_id="user-b5",
    )

    assert returned is expected_memory
    assert events == ["reserve", "admit"]
    assert orchestration.calls == [
        {
            "source_ref": source_ref,
            "owner_user_id": "user-b5",
        }
    ]
    assert handoff.snapshot_reads == 1
    assert admission.calls[0]["reservation"] is reservation
    assert admission.calls[0]["content"] == {"fact": "alpha"}
    assert admission.calls[0]["content"] is not handoff._content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ToolResponsePayloadSourceRejectedError("source rejected"),
        PromotionReservationRevokedError("revoked"),
        PromotionReservationProofReuseConflictError("proof conflict"),
        PromotionReservationReconstructionCorruptionError("corrupt"),
    ],
)
async def test_b5_handoff_failures_stop_before_admission(failure):
    orchestration = _Orchestration(failure=failure)
    admission = _Admission()
    service = DurableToolResponsePayloadMemoryPromotion(
        orchestration,  # type: ignore[arg-type]
        admission,  # type: ignore[arg-type]
    )

    with pytest.raises(type(failure), match=str(failure)):
        await service.promote(
            source_ref=object(),  # type: ignore[arg-type]
            owner_user_id="user-b5",
        )

    assert len(orchestration.calls) == 1
    assert admission.calls == []


@pytest.mark.asyncio
async def test_b5_admission_failure_propagates_without_retry():
    failure = RuntimeError("admission failed")
    reservation = object()
    handoff = _Handoff(reservation, {"fact": "beta"})
    orchestration = _Orchestration(handoff)
    admission = _Admission(failure=failure)
    service = DurableToolResponsePayloadMemoryPromotion(
        orchestration,  # type: ignore[arg-type]
        admission,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="admission failed"):
        await service.promote(
            source_ref=object(),  # type: ignore[arg-type]
            owner_user_id="user-b5",
        )

    assert len(orchestration.calls) == 1
    assert len(admission.calls) == 1
    assert handoff.snapshot_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["reserve", "admit"])
async def test_b5_cancellation_propagates_unchanged(cancel_at):
    handoff = _Handoff(object(), {"fact": "cancel"})
    orchestration = _Orchestration(
        handoff,
        failure=asyncio.CancelledError() if cancel_at == "reserve" else None,
    )
    admission = _Admission(
        failure=asyncio.CancelledError() if cancel_at == "admit" else None,
    )
    service = DurableToolResponsePayloadMemoryPromotion(
        orchestration,  # type: ignore[arg-type]
        admission,  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError):
        await service.promote(
            source_ref=object(),  # type: ignore[arg-type]
            owner_user_id="user-b5",
        )

    if cancel_at == "reserve":
        assert admission.calls == []
        assert handoff.snapshot_reads == 0
    else:
        assert len(admission.calls) == 1
        assert handoff.snapshot_reads == 1
