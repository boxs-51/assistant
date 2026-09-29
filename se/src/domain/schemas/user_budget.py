from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Any, Mapping

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import GatewayBaseModel


USER_BUDGET_DECIMAL_QUANTUM = Decimal("0.00000001")
USER_BUDGET_ATOMIC_SCALE = 100_000_000
USER_BUDGET_INT64_MAX = 9_223_372_036_854_775_807


class UserBudgetWindowState(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class UserBudgetResourceKind(str, Enum):
    INFERENCE_CALL = "INFERENCE_CALL"
    INPUT_TOKEN = "INPUT_TOKEN"
    OUTPUT_TOKEN = "OUTPUT_TOKEN"
    TOTAL_TOKEN = "TOTAL_TOKEN"
    TOOL_CALL = "TOOL_CALL"
    COMPUTE_UNIT = "COMPUTE_UNIT"
    COST_USD = "COST_USD"


class UserBudgetReservationState(str, Enum):
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


_COUNT_RESOURCE_KINDS = {
    UserBudgetResourceKind.INFERENCE_CALL,
    UserBudgetResourceKind.INPUT_TOKEN,
    UserBudgetResourceKind.OUTPUT_TOKEN,
    UserBudgetResourceKind.TOTAL_TOKEN,
    UserBudgetResourceKind.TOOL_CALL,
}
_DECIMAL_RESOURCE_KINDS = {
    UserBudgetResourceKind.COMPUTE_UNIT,
    UserBudgetResourceKind.COST_USD,
}


def _require_atomic_int(value: int, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("user budget atomic values must be integers")
    minimum = 1 if positive else 0
    if value < minimum or value > USER_BUDGET_INT64_MAX:
        relation = "positive" if positive else "non-negative"
        raise ValueError(
            f"user budget atomic value must be {relation} and <= signed BIGINT max"
        )
    return value


def normalize_decimal_value(value: Any, *, positive: bool = False) -> Decimal:
    normalized = Decimal(str(value))
    if not normalized.is_finite():
        raise ValueError("user budget decimal values must be finite")
    normalized = normalized.quantize(
        USER_BUDGET_DECIMAL_QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
    if positive and normalized <= 0:
        raise ValueError("user budget policy decimal limits must be positive")
    if not positive and normalized < 0:
        raise ValueError("user budget decimal counters must be non-negative")
    atomic = int(normalized * USER_BUDGET_ATOMIC_SCALE)
    _require_atomic_int(atomic, positive=positive)
    return normalized


def decimal_to_atomic(value: Any, *, positive: bool = False) -> int:
    normalized = normalize_decimal_value(value, positive=positive)
    return _require_atomic_int(
        int(normalized * USER_BUDGET_ATOMIC_SCALE),
        positive=positive,
    )


def decimal_from_atomic(value: int) -> Decimal:
    atomic = _require_atomic_int(value)
    return (Decimal(atomic) / USER_BUDGET_ATOMIC_SCALE).quantize(
        USER_BUDGET_DECIMAL_QUANTUM
    )


def normalize_resource_amount_atomic(
    resource_kind: UserBudgetResourceKind | str,
    value: Any,
    *,
    positive: bool = False,
) -> int:
    kind = UserBudgetResourceKind(resource_kind)
    if kind in _COUNT_RESOURCE_KINDS:
        if isinstance(value, bool):
            raise TypeError("count resource amounts must be integers")
        if isinstance(value, int):
            return _require_atomic_int(value, positive=positive)
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
            raise ValueError("count resource amounts must be exact integers")
        return _require_atomic_int(int(decimal_value), positive=positive)
    if kind in _DECIMAL_RESOURCE_KINDS:
        return decimal_to_atomic(value, positive=positive)
    raise ValueError(f"unsupported user budget resource kind: {kind}")


def canonical_decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_decimal_value(value)
    return format(normalized, ".8f")


def normalize_authoritative_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authoritative user budget time must be timezone-aware")
    return value.astimezone(timezone.utc)


class UserBudgetPolicy(GatewayBaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=255)
    owner_user_id: str = Field(min_length=1, max_length=255)
    policy_version: str = Field(min_length=1, max_length=64)
    window_duration_seconds: int = Field(gt=0, le=USER_BUDGET_INT64_MAX)

    max_compute_units: Decimal | None
    max_inference_calls: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    max_input_tokens: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    max_output_tokens: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    max_total_tokens: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    max_tool_calls_total: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    default_per_tool_limit: int | None = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    tool_limits: dict[str, int]
    max_cost_usd: Decimal | None

    @field_validator("max_compute_units", "max_cost_usd", mode="before")
    @classmethod
    def _normalize_optional_decimal_limit(cls, value: Any) -> Any:
        if value is None:
            return None
        return normalize_decimal_value(value, positive=True)

    @field_validator("tool_limits")
    @classmethod
    def _validate_tool_limits(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for capability_id, limit in value.items():
            if not isinstance(capability_id, str) or not capability_id:
                raise ValueError("tool limit capability_id must be non-empty")
            if len(capability_id) > 255:
                raise ValueError("tool limit capability_id exceeds 255 characters")
            normalized[capability_id] = _require_atomic_int(limit, positive=True)
        return dict(sorted(normalized.items()))

    def canonical_semantics(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "owner_user_id": self.owner_user_id,
            "policy_version": self.policy_version,
            "window_duration_seconds": self.window_duration_seconds,
            "max_compute_units": canonical_decimal_string(self.max_compute_units),
            "max_inference_calls": self.max_inference_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_total_tokens": self.max_total_tokens,
            "max_tool_calls_total": self.max_tool_calls_total,
            "default_per_tool_limit": self.default_per_tool_limit,
            "tool_limits": {
                key: self.tool_limits[key] for key in sorted(self.tool_limits)
            },
            "max_cost_usd": canonical_decimal_string(self.max_cost_usd),
        }

    @property
    def policy_fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical_semantics(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def durable_values(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "owner_user_id": self.owner_user_id,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "window_duration_seconds": self.window_duration_seconds,
            "max_compute_atomic": (
                None
                if self.max_compute_units is None
                else decimal_to_atomic(self.max_compute_units, positive=True)
            ),
            "max_inference_calls": self.max_inference_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_total_tokens": self.max_total_tokens,
            "max_tool_calls_total": self.max_tool_calls_total,
            "default_per_tool_limit": self.default_per_tool_limit,
            "tool_limits_json": dict(self.tool_limits),
            "max_cost_usd_atomic": (
                None
                if self.max_cost_usd is None
                else decimal_to_atomic(self.max_cost_usd, positive=True)
            ),
        }


class UserBudgetReservationIntent(GatewayBaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str = Field(min_length=1, max_length=255)
    owner_user_id: str = Field(min_length=1, max_length=255)
    window_epoch: int = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    idempotency_key: str = Field(min_length=1, max_length=255)
    resource_kind: UserBudgetResourceKind
    capability_id: str | None = Field(default=None, max_length=255)
    reserved_amount_atomic: int = Field(gt=0, le=USER_BUDGET_INT64_MAX)
    attribution: dict[str, Any]

    @model_validator(mode="after")
    def _validate_capability_scope(self) -> "UserBudgetReservationIntent":
        if self.resource_kind is UserBudgetResourceKind.TOOL_CALL:
            if not self.capability_id:
                raise ValueError("TOOL_CALL reservation requires canonical capability_id")
        elif self.capability_id is not None:
            raise ValueError("non-tool reservation cannot carry capability_id")
        return self

    def canonical_attribution(self) -> dict[str, Any]:
        canonical = _canonicalize_json(self.attribution)
        if not isinstance(canonical, dict):
            raise TypeError("UBQ reservation attribution must canonicalize to an object")
        return canonical

    @property
    def payload_fingerprint(self) -> str:
        payload = {
            "resource_kind": self.resource_kind.value,
            "reserved_amount_atomic": self.reserved_amount_atomic,
            "capability_id": self.capability_id,
            "attribution": self.canonical_attribution(),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _canonicalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise TypeError("binary float is not permitted in canonical UBQ attribution")
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical UBQ attribution object keys must be strings")
        return {
            key: _canonicalize_json(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_json(item) for item in value]
    raise TypeError(f"unsupported canonical UBQ attribution value: {type(value)!r}")
