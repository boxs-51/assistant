from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class ResourceScope(str, Enum):
    SANDBOX = "SANDBOX"
    CLIENT_LOCAL = "CLIENT_LOCAL"
    ASSET = "ASSET"
    EXTERNAL = "EXTERNAL"
    INTERNAL_TRUSTED = "INTERNAL_TRUSTED"


class FallbackPolicy(str, Enum):
    NONE = "NONE"
    SEMANTICALLY_EQUIVALENT_ONLY = "SEMANTICALLY_EQUIVALENT_ONLY"


class CapabilityInvocationTarget(BaseModel):
    """Stable resource semantics for one logical capability invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_scope: ResourceScope
    resource_ref: str | None = None
    stable_client_id: str | None = None
    fallback_policy: FallbackPolicy = FallbackPolicy.NONE

    @field_validator("resource_ref", "stable_client_id")
    @classmethod
    def _canonical_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(
                "semantic target strings must be canonical and non-empty"
            )
        return value

    @model_validator(mode="after")
    def _validate_client_local(self) -> "CapabilityInvocationTarget":
        if (
            self.resource_scope is ResourceScope.CLIENT_LOCAL
            and not self.stable_client_id
        ):
            raise ValueError("CLIENT_LOCAL target requires stable_client_id")
        return self

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "resource_scope": self.resource_scope.value,
            "resource_ref": self.resource_ref,
            "stable_client_id": self.stable_client_id,
            "fallback_policy": self.fallback_policy.value,
        }


def normalize_capability_target(
    value: CapabilityInvocationTarget | Mapping[str, Any] | None,
) -> CapabilityInvocationTarget | None:
    if value is None:
        return None
    if isinstance(value, CapabilityInvocationTarget):
        return value
    return CapabilityInvocationTarget.model_validate(dict(value))


def canonical_capability_target_payload(
    value: CapabilityInvocationTarget | Mapping[str, Any] | None,
) -> dict[str, str | None] | None:
    target = normalize_capability_target(value)
    return target.canonical_payload() if target is not None else None


__all__ = [
    "CapabilityInvocationTarget",
    "FallbackPolicy",
    "ResourceScope",
    "canonical_capability_target_payload",
    "normalize_capability_target",
]
