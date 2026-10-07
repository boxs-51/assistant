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
    """Stable semantic resource intent, separate from physical routing."""

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
        if not value or value != value.strip():
            raise ValueError("semantic target strings must already be canonical")
        return value

    @model_validator(mode="after")
    def _validate_client_local(self) -> "CapabilityInvocationTarget":
        if (
            self.resource_scope is ResourceScope.CLIENT_LOCAL
            and not self.stable_client_id
        ):
            raise ValueError("CLIENT_LOCAL requires stable_client_id")
        return self

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "resource_scope": self.resource_scope.value,
            "resource_ref": self.resource_ref,
            "stable_client_id": self.stable_client_id,
            "fallback_policy": self.fallback_policy.value,
        }


def coerce_capability_target(
    value: CapabilityInvocationTarget | Mapping[str, Any] | None,
) -> CapabilityInvocationTarget | None:
    if value is None:
        return None
    if isinstance(value, CapabilityInvocationTarget):
        return value
    if isinstance(value, Mapping):
        return CapabilityInvocationTarget.model_validate(dict(value))
    raise TypeError("target must be CapabilityInvocationTarget, mapping, or None")


def canonical_target_payload(
    value: CapabilityInvocationTarget | Mapping[str, Any],
) -> dict[str, str | None]:
    target = coerce_capability_target(value)
    if target is None:
        raise ValueError("non-null target required")
    return target.canonical_payload()


__all__ = [
    "CapabilityInvocationTarget",
    "FallbackPolicy",
    "ResourceScope",
    "canonical_target_payload",
    "coerce_capability_target",
]
