from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from se.src.context.memory import memory_content_digest
from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    MemoryPromotionIntentIntegrityError,
    MemoryPromotionProofScope,
    PromotionReservation,
    PromotionReservationIntentMismatchError,
    PromotionReservationIssuer,
    PromotionReservationVerifier,
    SourcePromotionAuthorityPort,
    SourcePromotionProof,
    SourcePromotionProofIntegrityError,
    validate_memory_promotion_intent_integrity,
    validate_promotion_reservation_integrity,
    validate_reservation_matches_intent,
    validate_source_promotion_proof_integrity,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _source(
    *,
    authority_id: str = "session-1",
    owner: str = "user-1",
    metadata=None,
):
    return create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id=authority_id,
        owner_user_id=owner,
        session_id=authority_id,
        source_created_at=NOW,
        source_state="active",
        metadata={"label": "canonical"} if metadata is None else metadata,
    )


def _proof(*, source=None, receipt: str = "receipt-1", token: str = "state-7"):
    return SourcePromotionProof(
        source_ref_snapshot=source or _source(),
        proof_receipt_id=receipt,
        authority_state_token=token,
    )


def _intent(
    *,
    source=None,
    proof=None,
    owner: str | None = None,
    metadata=None,
    content=None,
):
    source = source or _source()
    content = {"fact": "alpha"} if content is None else content
    return MemoryPromotionIntent(
        owner_user_id=source.owner_user_id if owner is None else owner,
        source_ref_snapshot=source,
        source_proof=proof or _proof(source=source),
        content_digest=memory_content_digest(content),
        metadata={"kind": ["test"]} if metadata is None else metadata,
        memory_schema_version=1,
    )


def test_ctx_f5_3c_proof_scope_is_exact_and_strings_are_normalized():
    proof = _proof(receipt="  receipt-1  ", token="  state-7  ")

    assert list(MemoryPromotionProofScope) == [
        MemoryPromotionProofScope.MEMORY_PROMOTION
    ]
    assert proof.scope is MemoryPromotionProofScope.MEMORY_PROMOTION
    assert proof.proof_receipt_id == "receipt-1"
    assert proof.authority_state_token == "state-7"
    assert validate_source_promotion_proof_integrity(proof) is proof


def test_ctx_f5_3c_values_are_frozen_and_extra_fields_fail_closed():
    source = _source()

    with pytest.raises(ValidationError):
        SourcePromotionProof(
            source_ref_snapshot=source,
            proof_receipt_id="receipt-1",
            authority_state_token="state-7",
            unexpected="nope",
        )

    proof = _proof(source=source)
    with pytest.raises(ValidationError):
        proof.proof_receipt_id = "replacement"


def test_ctx_f5_3c_intent_binds_exact_source_proof_owner_and_scope():
    source = _source()
    proof = _proof(source=source)
    intent = _intent(source=source, proof=proof)

    assert validate_memory_promotion_intent_integrity(intent) is intent
    assert intent.owner_user_id == source.owner_user_id
    assert intent.source_proof.source_ref_snapshot == source

    with pytest.raises(ValidationError):
        _intent(source=source, proof=proof, owner="different-owner")

    foreign = _source(authority_id="session-2")
    with pytest.raises(ValidationError):
        _intent(source=source, proof=_proof(source=foreign))


@pytest.mark.parametrize(("proof_value", "intent_value"), [(True, 1), (1, 1.0)])
def test_ctx_f5_3c_source_snapshot_exact_binding_is_type_sensitive(
    proof_value,
    intent_value,
):
    intent_source = _source(metadata={"flag": intent_value})
    proof_source = _source(metadata={"flag": proof_value})

    with pytest.raises(ValidationError, match="exact source_ref_snapshot"):
        _intent(
            source=intent_source,
            proof=_proof(source=proof_source),
        )


def test_ctx_f5_3c_intent_reuses_memory_json_canonicality_and_deep_freezes_metadata():
    source = _source()
    metadata = {"labels": [{"name": "alpha"}]}
    intent = _intent(source=source, metadata=metadata)

    metadata["labels"][0]["name"] = "mutated"

    assert isinstance(intent.metadata, MappingProxyType)
    assert isinstance(intent.metadata["labels"], tuple)
    assert intent.metadata["labels"][0]["name"] == "alpha"
    assert intent.model_dump(mode="json")["metadata"] == {
        "labels": [{"name": "alpha"}]
    }

    with pytest.raises(ValidationError):
        _intent(source=source, metadata={"bad": {1, 2}})


@pytest.mark.parametrize("version", [True, False, 0, -1, 1.0, "1"])
def test_ctx_f5_3c_schema_version_is_real_positive_integer(version):
    source = _source()

    with pytest.raises(ValidationError):
        MemoryPromotionIntent(
            owner_user_id=source.owner_user_id,
            source_ref_snapshot=source,
            source_proof=_proof(source=source),
            content_digest=memory_content_digest({"fact": "alpha"}),
            metadata={},
            memory_schema_version=version,
        )


def test_ctx_f5_3c_reservation_is_untrusted_envelope_and_exact_intent_match_only():
    intent = _intent()
    reservation = PromotionReservation(
        promotion_authority_id="  promotion-1  ",
        intent=intent,
    )

    assert reservation.promotion_authority_id == "promotion-1"
    assert validate_promotion_reservation_integrity(reservation) is reservation
    assert validate_reservation_matches_intent(reservation, intent) is None

    different_intent = _intent(
        proof=_proof(receipt="receipt-2"),
    )
    with pytest.raises(PromotionReservationIntentMismatchError):
        validate_reservation_matches_intent(reservation, different_intent)


@pytest.mark.parametrize(("reserved_value", "supplied_value"), [(True, 1), (1, 1.0)])
def test_ctx_f5_3c_reservation_exact_intent_binding_is_type_sensitive(
    reserved_value,
    supplied_value,
):
    source = _source()
    reserved_intent = _intent(source=source, metadata={"flag": reserved_value})
    supplied_intent = _intent(source=source, metadata={"flag": supplied_value})
    reservation = PromotionReservation(
        promotion_authority_id="promotion-1",
        intent=reserved_intent,
    )

    with pytest.raises(PromotionReservationIntentMismatchError):
        validate_reservation_matches_intent(reservation, supplied_intent)


def test_ctx_f5_3c_integrity_helpers_fail_closed_on_tampered_stored_values():
    proof = _proof()
    object.__setattr__(proof, "proof_receipt_id", " ")

    with pytest.raises(SourcePromotionProofIntegrityError, match="proof_receipt_id"):
        validate_source_promotion_proof_integrity(proof)

    intent = _intent()
    object.__setattr__(intent, "owner_user_id", "foreign-owner")

    with pytest.raises(MemoryPromotionIntentIntegrityError, match="owner_user_id"):
        validate_memory_promotion_intent_integrity(intent)


def test_ctx_f5_3c_protocol_surface_is_dormant_and_exact():
    assert list(inspect.signature(
        SourcePromotionAuthorityPort.reprove_for_memory_promotion
    ).parameters) == ["self", "source_ref", "owner_user_id"]
    assert list(inspect.signature(PromotionReservationIssuer.reserve).parameters) == [
        "self",
        "intent",
    ]
    assert list(inspect.signature(PromotionReservationVerifier.verify).parameters) == [
        "self",
        "reservation",
        "intent",
    ]
