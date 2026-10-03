from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import se.src.infrastructure.storage.services.promotion_reservation_recovery as recovery_module
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
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
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRecord,
    DurablePromotionReservationState,
    PromotionReservationProofReuseConflictError,
    PromotionReservationReconstructionCorruptionError,
    PromotionReservationRevokedError,
)
from se.src.infrastructure.storage.services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    ToolResponsePayloadSourceRejectedError,
    TrustedToolResponsePromotionMaterial,
)

OWNER = "user-ctx-f5-3i-b4"


def _intent(suffix: str = "one", *, digest: str | None = None) -> MemoryPromotionIntent:
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=f"session-{suffix}",
        owner_user_id=OWNER,
        session_id=f"session-{suffix}",
        source_state="active",
        metadata={},
    )
    proof = SourcePromotionProof(
        source_ref_snapshot=source,
        proof_receipt_id=f"receipt-{suffix}",
        authority_state_token=f"state-{suffix}",
        scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
    )
    return MemoryPromotionIntent(
        owner_user_id=OWNER,
        source_ref_snapshot=source,
        source_proof=proof,
        content_digest=digest or f"digest-{suffix}",
        metadata={},
        memory_schema_version=1,
    )


def _record(
    authority_id: str,
    intent: MemoryPromotionIntent,
    state: DurablePromotionReservationState,
) -> DurablePromotionReservationRecord:
    proof = intent.source_proof
    return DurablePromotionReservationRecord(
        promotion_authority_id=authority_id,
        intent=intent,
        intent_digest=f"intent-digest-{authority_id}",
        source_context_source_id=intent.source_ref_snapshot.context_source_id,
        proof_receipt_id=proof.proof_receipt_id,
        authority_state_token=proof.authority_state_token,
        proof_scope=proof.scope,
        state=state,
    )


class _Repository:
    def __init__(self, exact, proof) -> None:
        self.exact = exact
        self.proof = proof
        self.calls = []

    async def get_by_intent(self, intent):
        self.calls.append(("intent", intent))
        return self.exact

    async def get_by_proof_authority(self, intent):
        self.calls.append(("proof", intent))
        return self.proof


def _resolver(monkeypatch, repository: _Repository):
    sessions = []

    @asynccontextmanager
    async def _session_factory():
        session = object()
        sessions.append(session)
        yield session

    monkeypatch.setattr(
        recovery_module,
        "DurablePromotionReservationRepository",
        lambda session: repository,
    )
    return DurablePromotionReservationRecovery(_session_factory), sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        DurablePromotionReservationState.ISSUED,
        DurablePromotionReservationState.CONSUMED,
    ],
)
async def test_recovery_returns_exact_issued_or_consumed_envelope_without_minting(
    monkeypatch,
    state,
):
    intent = _intent(state.value.lower())
    winner = _record("authority-existing", intent, state)
    repository = _Repository(winner, winner)
    resolver, sessions = _resolver(monkeypatch, repository)

    recovered = await resolver.recover(intent=intent)

    assert recovered == PromotionReservation(
        promotion_authority_id="authority-existing",
        intent=intent,
    )
    assert repository.calls == [("intent", intent), ("proof", intent)]
    assert len(sessions) == 1
    assert "uuid" not in recovery_module.__dict__
    assert "uuid4" not in recovery_module.__dict__


@pytest.mark.asyncio
async def test_recovery_returns_none_only_when_both_lookups_have_no_winner(monkeypatch):
    intent = _intent()
    resolver, _ = _resolver(monkeypatch, _Repository(None, None))

    assert await resolver.recover(intent=intent) is None


@pytest.mark.asyncio
async def test_recovery_revoked_winner_fails_closed(monkeypatch):
    intent = _intent("revoked")
    winner = _record(
        "authority-revoked",
        intent,
        DurablePromotionReservationState.REVOKED,
    )
    resolver, _ = _resolver(monkeypatch, _Repository(winner, winner))

    with pytest.raises(PromotionReservationRevokedError):
        await resolver.recover(intent=intent)


@pytest.mark.asyncio
async def test_recovery_proof_only_or_disagreeing_winners_fail_closed(monkeypatch):
    intent = _intent("requested")
    proof_only = _record(
        "authority-proof",
        _intent("requested", digest="different-content"),
        DurablePromotionReservationState.ISSUED,
    )
    resolver, _ = _resolver(monkeypatch, _Repository(None, proof_only))
    with pytest.raises(PromotionReservationProofReuseConflictError):
        await resolver.recover(intent=intent)

    exact = _record(
        "authority-exact",
        intent,
        DurablePromotionReservationState.ISSUED,
    )
    proof = _record(
        "authority-other",
        intent,
        DurablePromotionReservationState.ISSUED,
    )
    resolver, _ = _resolver(monkeypatch, _Repository(exact, proof))
    with pytest.raises(PromotionReservationProofReuseConflictError):
        await resolver.recover(intent=intent)

    resolver, _ = _resolver(monkeypatch, _Repository(exact, None))
    with pytest.raises(PromotionReservationReconstructionCorruptionError):
        await resolver.recover(intent=intent)


def _material() -> TrustedToolResponsePromotionMaterial:
    payload = create_tool_response_payload(
        source_result_id="result-b4",
        invocation_id="invocation-b4",
        execution_id="execution-b4",
        tool_call_id="tool-call-b4",
        logical_capability_id="tool.b4",
        content={"answer": [1, 2, 3]},
        source_commit_state=COMMITTED_RESULT_STATE,
        owner_user_id=OWNER,
        session_id="session-b4",
    )
    source = create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=payload.payload_id,
        owner_user_id=OWNER,
        session_id="session-b4",
        source_state=COMMITTED_RESULT_STATE,
        metadata={"source_result_id": "result-b4"},
    )
    return TrustedToolResponsePromotionMaterial(
        source_proof=SourcePromotionProof(
            source_ref_snapshot=source,
            proof_receipt_id="receipt-b4",
            authority_state_token="state-b4",
            scope=MemoryPromotionProofScope.MEMORY_PROMOTION,
        ),
        _payload=payload,
    )


class _Source:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def read_trusted_promotion_material(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Recovery:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    async def recover(self, *, intent):
        self.calls.append(intent)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Issuer:
    def __init__(self) -> None:
        self.calls = []

    async def reserve(self, *, intent):
        self.calls.append(intent)
        return PromotionReservation(
            promotion_authority_id="authority-issued",
            intent=intent,
        )


@pytest.mark.asyncio
async def test_orchestration_recovers_before_issuer_and_reproofs_each_invocation():
    material = _material()
    recovered = PromotionReservation(
        promotion_authority_id="authority-recovered",
        intent=MemoryPromotionIntent(
            owner_user_id=OWNER,
            source_ref_snapshot=material.source_proof.source_ref_snapshot,
            source_proof=material.source_proof,
            content_digest=material.content_digest,
            metadata={},
            memory_schema_version=1,
        ),
    )
    source = _Source(material, material)
    recovery = _Recovery(recovered, None)
    issuer = _Issuer()
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
        recovery,  # type: ignore[arg-type]
    )

    first = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )
    second = await service.reserve(
        source_ref=material.source_proof.source_ref_snapshot,
        owner_user_id=OWNER,
    )

    assert first.reservation == recovered
    assert second.reservation.promotion_authority_id == "authority-issued"
    assert source.calls == 2
    assert len(recovery.calls) == 2
    assert len(issuer.calls) == 1


@pytest.mark.asyncio
async def test_source_rejection_stops_before_recovery_or_issuer():
    material = _material()
    source = _Source(ToolResponsePayloadSourceRejectedError("source rejected"))
    recovery = _Recovery(None)
    issuer = _Issuer()
    service = DurableToolResponsePayloadPromotionOrchestration(
        source,  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
        recovery,  # type: ignore[arg-type]
    )

    with pytest.raises(ToolResponsePayloadSourceRejectedError):
        await service.reserve(
            source_ref=material.source_proof.source_ref_snapshot,
            owner_user_id=OWNER,
        )

    assert source.calls == 1
    assert recovery.calls == []
    assert issuer.calls == []
