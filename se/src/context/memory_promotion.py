from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from se.src.context.memory import canonical_memory_bytes
from se.src.context.source_identity import (
    ContextSourceRef,
    validate_context_source_ref_integrity,
)


class PromotionPrimitiveIntegrityError(ValueError):
    """Base structural-integrity failure for dormant Memory promotion primitives."""


class SourcePromotionProofIntegrityError(PromotionPrimitiveIntegrityError):
    """Source promotion proof is structurally invalid or internally inconsistent."""


class MemoryPromotionIntentIntegrityError(PromotionPrimitiveIntegrityError):
    """Memory promotion intent is structurally invalid or internally inconsistent."""


class PromotionReservationIntegrityError(PromotionPrimitiveIntegrityError):
    """Promotion reservation envelope is structurally invalid."""


class PromotionReservationIntentMismatchError(PromotionPrimitiveIntegrityError):
    """Promotion reservation does not bind the exact supplied intent."""


class MemoryPromotionProofScope(StrEnum):
    MEMORY_PROMOTION = "MEMORY_PROMOTION"


_ErrorT = TypeVar("_ErrorT", bound=PromotionPrimitiveIntegrityError)


def _normalize_non_empty(
    name: str,
    value: Any,
    *,
    error_type: type[_ErrorT] = PromotionPrimitiveIntegrityError,
) -> str:
    if not isinstance(value, str):
        raise error_type(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise error_type(f"{name} must be non-empty")
    return normalized


def _require_stored_normalized_string(
    name: str,
    value: Any,
    *,
    error_type: type[_ErrorT],
) -> str:
    normalized = _normalize_non_empty(name, value, error_type=error_type)
    if value != normalized:
        raise error_type(f"{name} must already be normalized")
    return normalized


def _declared_shape_error(
    model: BaseModel,
    *,
    label: str,
    error_type: type[_ErrorT],
) -> None:
    declared_fields = set(type(model).model_fields)
    stored_fields = set(model.__dict__)
    extra_fields = stored_fields - declared_fields
    if model.__pydantic_extra__:
        extra_fields.update(model.__pydantic_extra__)
    if extra_fields:
        joined = ", ".join(sorted(extra_fields))
        raise error_type(f"{label} contains undeclared stored fields: {joined}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_frozen_metadata(value: Any, *, path: str = "$.metadata") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise MemoryPromotionIntentIntegrityError(
                f"non-finite metadata number at {path}"
            )
        return
    if isinstance(value, MappingProxyType):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MemoryPromotionIntentIntegrityError(
                    f"metadata keys must be strings at {path}"
                )
            _validate_frozen_metadata(item, path=f"{path}.{key}")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_frozen_metadata(item, path=f"{path}[{index}]")
        return
    raise MemoryPromotionIntentIntegrityError(
        f"metadata contains a non-frozen JSON container at {path}"
    )


def _source_snapshot_material(ref: ContextSourceRef) -> dict[str, Any]:
    validate_context_source_ref_integrity(ref)
    return ref.model_dump(mode="json")


class SourcePromotionProof(BaseModel):
    """Untrusted source-owner proof envelope for one Memory-promotion decision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    source_ref_snapshot: ContextSourceRef
    proof_receipt_id: str
    authority_state_token: str
    scope: MemoryPromotionProofScope = MemoryPromotionProofScope.MEMORY_PROMOTION

    @field_validator("proof_receipt_id", "authority_state_token", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: Any, info) -> str:
        return _normalize_non_empty(
            info.field_name,
            value,
            error_type=SourcePromotionProofIntegrityError,
        )

    @model_validator(mode="after")
    def validate_integrity(self) -> "SourcePromotionProof":
        return validate_source_promotion_proof_integrity(self)


class MemoryPromotionIntent(BaseModel):
    """Exact promotion material; structural validity is not server authorization."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    owner_user_id: str
    source_ref_snapshot: ContextSourceRef
    source_proof: SourcePromotionProof
    content_digest: str
    metadata: dict[str, Any] = Field(default_factory=dict, validate_default=True)
    memory_schema_version: int = Field(ge=1)

    @field_validator("owner_user_id", "content_digest", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: Any, info) -> str:
        return _normalize_non_empty(
            info.field_name,
            value,
            error_type=MemoryPromotionIntentIntegrityError,
        )

    @field_validator("memory_schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MemoryPromotionIntentIntegrityError(
                "memory_schema_version must be an integer"
            )
        if value < 1:
            raise MemoryPromotionIntentIntegrityError(
                "memory_schema_version must be >= 1"
            )
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata_domain(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise MemoryPromotionIntentIntegrityError(
                "metadata must be a canonical JSON object"
            )
        try:
            canonical_memory_bytes(value)
        except ValueError as exc:
            raise MemoryPromotionIntentIntegrityError(
                "metadata must use canonical Memory JSON semantics"
            ) from exc
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: dict[str, Any]) -> MappingProxyType:
        return _freeze_json(value)

    @field_serializer("metadata")
    def serialize_metadata(self, value: Any) -> Any:
        return _thaw_json(value)

    @model_validator(mode="after")
    def validate_integrity(self) -> "MemoryPromotionIntent":
        return validate_memory_promotion_intent_integrity(self)


class PromotionReservation(BaseModel):
    """Untrusted reservation envelope until a trusted server verifier succeeds."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    promotion_authority_id: str
    intent: MemoryPromotionIntent

    @field_validator("promotion_authority_id", mode="before")
    @classmethod
    def normalize_authority_id(cls, value: Any) -> str:
        return _normalize_non_empty(
            "promotion_authority_id",
            value,
            error_type=PromotionReservationIntegrityError,
        )

    @model_validator(mode="after")
    def validate_integrity(self) -> "PromotionReservation":
        return validate_promotion_reservation_integrity(self)


def validate_source_promotion_proof_integrity(
    proof: SourcePromotionProof,
) -> SourcePromotionProof:
    if not isinstance(proof, SourcePromotionProof):
        raise SourcePromotionProofIntegrityError(
            "proof must be a SourcePromotionProof"
        )
    _declared_shape_error(
        proof,
        label="SourcePromotionProof",
        error_type=SourcePromotionProofIntegrityError,
    )
    if not isinstance(proof.source_ref_snapshot, ContextSourceRef):
        raise SourcePromotionProofIntegrityError(
            "source_ref_snapshot must be a ContextSourceRef"
        )
    try:
        validate_context_source_ref_integrity(proof.source_ref_snapshot)
    except ValueError as exc:
        raise SourcePromotionProofIntegrityError(
            "source_ref_snapshot failed ContextSourceRef integrity"
        ) from exc
    _require_stored_normalized_string(
        "proof_receipt_id",
        proof.proof_receipt_id,
        error_type=SourcePromotionProofIntegrityError,
    )
    _require_stored_normalized_string(
        "authority_state_token",
        proof.authority_state_token,
        error_type=SourcePromotionProofIntegrityError,
    )
    if proof.scope is not MemoryPromotionProofScope.MEMORY_PROMOTION:
        raise SourcePromotionProofIntegrityError(
            "scope must be MEMORY_PROMOTION"
        )
    return proof


def validate_memory_promotion_intent_integrity(
    intent: MemoryPromotionIntent,
) -> MemoryPromotionIntent:
    if not isinstance(intent, MemoryPromotionIntent):
        raise MemoryPromotionIntentIntegrityError(
            "intent must be a MemoryPromotionIntent"
        )
    _declared_shape_error(
        intent,
        label="MemoryPromotionIntent",
        error_type=MemoryPromotionIntentIntegrityError,
    )
    if not isinstance(intent.source_ref_snapshot, ContextSourceRef):
        raise MemoryPromotionIntentIntegrityError(
            "source_ref_snapshot must be a ContextSourceRef"
        )
    if not isinstance(intent.source_proof, SourcePromotionProof):
        raise MemoryPromotionIntentIntegrityError(
            "source_proof must be a SourcePromotionProof"
        )

    try:
        validate_context_source_ref_integrity(intent.source_ref_snapshot)
        validate_source_promotion_proof_integrity(intent.source_proof)
    except ValueError as exc:
        raise MemoryPromotionIntentIntegrityError(
            "source promotion material failed integrity"
        ) from exc

    owner = _require_stored_normalized_string(
        "owner_user_id",
        intent.owner_user_id,
        error_type=MemoryPromotionIntentIntegrityError,
    )
    if owner != intent.source_ref_snapshot.owner_user_id:
        raise MemoryPromotionIntentIntegrityError(
            "owner_user_id must match source_ref_snapshot.owner_user_id"
        )

    if _source_snapshot_material(
        intent.source_ref_snapshot
    ) != _source_snapshot_material(intent.source_proof.source_ref_snapshot):
        raise MemoryPromotionIntentIntegrityError(
            "source_proof must bind the exact source_ref_snapshot"
        )

    if intent.source_proof.scope is not MemoryPromotionProofScope.MEMORY_PROMOTION:
        raise MemoryPromotionIntentIntegrityError(
            "source_proof must be scoped to MEMORY_PROMOTION"
        )

    _require_stored_normalized_string(
        "content_digest",
        intent.content_digest,
        error_type=MemoryPromotionIntentIntegrityError,
    )
    if isinstance(intent.memory_schema_version, bool) or not isinstance(
        intent.memory_schema_version,
        int,
    ):
        raise MemoryPromotionIntentIntegrityError(
            "memory_schema_version must be an integer"
        )
    if intent.memory_schema_version < 1:
        raise MemoryPromotionIntentIntegrityError(
            "memory_schema_version must be >= 1"
        )

    if not isinstance(intent.metadata, MappingProxyType):
        raise MemoryPromotionIntentIntegrityError(
            "metadata root must be a frozen JSON object"
        )
    _validate_frozen_metadata(intent.metadata)
    try:
        canonical_memory_bytes(_thaw_json(intent.metadata))
    except ValueError as exc:
        raise MemoryPromotionIntentIntegrityError(
            "metadata must reuse canonical Memory JSON semantics"
        ) from exc

    return intent


def validate_promotion_reservation_integrity(
    reservation: PromotionReservation,
) -> PromotionReservation:
    if not isinstance(reservation, PromotionReservation):
        raise PromotionReservationIntegrityError(
            "reservation must be a PromotionReservation"
        )
    _declared_shape_error(
        reservation,
        label="PromotionReservation",
        error_type=PromotionReservationIntegrityError,
    )
    _require_stored_normalized_string(
        "promotion_authority_id",
        reservation.promotion_authority_id,
        error_type=PromotionReservationIntegrityError,
    )
    if not isinstance(reservation.intent, MemoryPromotionIntent):
        raise PromotionReservationIntegrityError(
            "intent must be a MemoryPromotionIntent"
        )
    try:
        validate_memory_promotion_intent_integrity(reservation.intent)
    except ValueError as exc:
        raise PromotionReservationIntegrityError(
            "reservation intent failed structural integrity"
        ) from exc
    return reservation


def validate_reservation_matches_intent(
    reservation: PromotionReservation,
    intent: MemoryPromotionIntent,
) -> None:
    """Validate exact envelope equality only; this does not authorize promotion."""
    validate_promotion_reservation_integrity(reservation)
    validate_memory_promotion_intent_integrity(intent)
    if reservation.intent.model_dump(mode="json") != intent.model_dump(mode="json"):
        raise PromotionReservationIntentMismatchError(
            "reservation does not bind the exact supplied intent"
        )


class SourcePromotionAuthorityPort(Protocol):
    async def reprove_for_memory_promotion(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> SourcePromotionProof: ...


class PromotionReservationIssuer(Protocol):
    async def reserve(
        self,
        *,
        intent: MemoryPromotionIntent,
    ) -> PromotionReservation: ...


class PromotionReservationVerifier(Protocol):
    async def verify(
        self,
        *,
        reservation: PromotionReservation,
        intent: MemoryPromotionIntent,
    ) -> None: ...
