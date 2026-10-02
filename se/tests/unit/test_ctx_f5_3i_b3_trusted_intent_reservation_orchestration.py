from __future__ import annotations

import asyncio
import inspect

import pytest

from se.src.context.memory import MEMORY_SCHEMA_VERSION
from se.src.context.memory_promotion import (
    MemoryPromotionProofScope,
    PromotionReservation,
    SourcePromotionProof,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.context.tool_response_payload import (
    COMMITTED_RESULT_STATE,
    create_tool_response_payload,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
    TrustedToolResponsePromotionReservation,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    TrustedToolResponsePromotionMaterial,
)


OWNER = "user-ctx-f5-3i-b3"
CONTENT = {"kind": "tool-result", "nested": [1, {"ok": True}]}


def _material() -> TrustedToolResponsePromotionMaterial:
    payload = create_tool_response_payload(
        source_result_id="result-b3",
        invocation_id="invocation-b3",
        execution_id="execution-b3",
        tool_call_id="tool-call-b3",
        logical_capability_id="tool.b3",
        content=CONTENT,
        source_commit_state=COMMITTED_RESULT_STATE,
        owner_user_id=OWNER,
        session_id="session-b3",
    )
    source_ref = create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=payload.payload_id,
        owner_user_id=OWNER,
        session_id="session-b3",
        source_state=COMMITTED_RESULT_STATE,
        metadata={"source_result_id": "result-b3"},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source_ref,
        proof_receipt_id="receipt-b3",
        authority_state_token="state-b3",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return TrustedToolResponsePromotionMaterial(
        source_proof=proof,
        _payload=payload,
    )


class _SourceAuthority:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    async def read_trusted_promotion_material(
        self,
        *,
        source_ref,
        owner_user_id,
    ):
        self.calls.append((source_ref, owner_user_id))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _ReservationIssuer:
    def __init__(self, *failures: BaseException | None) -> None:
        self.failures = list(failures)
        self.calls = []

    async def reserve(self, *, intent):
        self.calls.append(intent)
        failure = self.failures.pop(0) if self.failures else None
        if failure is not None:
            raise failure
        return PromotionReservation(
            promotion_authority_id=f"authority-{len(self.calls)}",
            intent=intent,
        )


@pytest.mark.asyncio
async def test_b3_builds_exact_server_intent_and_calls_each_dependency_once():
    material = _material()
    source = _SourceAuthority(material)
    issuer = _ReservationIssuer()
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
    )

    signature = inspect.signature(service.reserve)
    assert list(signature.parameters) == ["source_ref", "owner_user_id"]
    assert all(
        name not in signature.parameters
        for name in (
            "content",
            "content_digest",
            "source_proof",
            "metadata",
            "memory_schema_version",
        )
    )

    result = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )

    assert isinstance(result, TrustedToolResponsePromotionReservation)
    assert source.calls == [
        (material.source_proof.source_ref_snapshot, OWNER),
    ]
    assert len(issuer.calls) == 1

    intent = issuer.calls[0]
    assert (
        intent.owner_user_id
        == material.source_proof.source_ref_snapshot.owner_user_id
    )
    assert (
        intent.source_ref_snapshot
        == material.source_proof.source_ref_snapshot
    )
    assert intent.source_proof == material.source_proof
    assert intent.content_digest == material.content_digest
    assert dict(intent.metadata) == {}
    assert intent.memory_schema_version == MEMORY_SCHEMA_VERSION

    assert result.reservation.intent == intent
    assert result.trusted_material is material
    assert result.content_digest == intent.content_digest
    assert result.content_snapshot == CONTENT


@pytest.mark.asyncio
async def test_b3_transient_snapshot_mutation_cannot_mutate_later_snapshot():
    material = _material()
    service = DurableToolResponsePayloadPromotionOrchestration(
        _SourceAuthority(material),  # type: ignore[arg-type]
        _ReservationIssuer(),  # type: ignore[arg-type]
    )

    result = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )

    first = result.content_snapshot
    first["kind"] = "mutated"
    first["nested"][1]["ok"] = False

    second = result.content_snapshot
    assert second is not first
    assert second["kind"] == "tool-result"
    assert second["nested"][1]["ok"] is True
    assert result.content_digest == result.reservation.intent.content_digest


@pytest.mark.asyncio
async def test_b3_known_pre_reservation_failure_has_no_synthetic_success_and_retry_reproofs():
    material = _material()
    failure = RuntimeError("known pre-reservation source failure")
    source = _SourceAuthority(failure, material)
    issuer = _ReservationIssuer()
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="known pre-reservation"):
        await service.reserve(
            source_ref=material.source_proof.source_ref_snapshot,
            owner_user_id=OWNER,
        )

    assert len(source.calls) == 1
    assert issuer.calls == []

    result = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )

    assert len(source.calls) == 2
    assert len(issuer.calls) == 1
    assert result.reservation.intent == issuer.calls[0]


@pytest.mark.asyncio
async def test_b3_issuer_failure_propagates_and_next_invocation_reproofs():
    material = _material()
    source = _SourceAuthority(material, material)
    issuer = _ReservationIssuer(
        RuntimeError("issuer pre-commit failure"),
        None,
    )
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="issuer pre-commit"):
        await service.reserve(
            source_ref=material.source_proof.source_ref_snapshot,
            owner_user_id=OWNER,
        )

    result = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )

    assert len(source.calls) == 2
    assert len(issuer.calls) == 2
    assert result.reservation.intent == issuer.calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["source", "issuer"])
async def test_b3_cancellation_propagates_without_synthetic_success(cancel_at):
    material = _material()
    source = _SourceAuthority(
        asyncio.CancelledError() if cancel_at == "source" else material
    )
    issuer = _ReservationIssuer(
        asyncio.CancelledError() if cancel_at == "issuer" else None
    )
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError):
        await service.reserve(
            source_ref=material.source_proof.source_ref_snapshot,
            owner_user_id=OWNER,
        )

    assert len(source.calls) == 1
    assert len(issuer.calls) == (0 if cancel_at == "source" else 1)
