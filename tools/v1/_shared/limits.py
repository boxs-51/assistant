from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ToolLimitConfigError


@dataclass(frozen=True, slots=True)
class IntLimitSpec:
    name: str
    default: int
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolLimitConfigError("limit name must be a non-empty string")
        for field_name in ("default", "minimum", "maximum"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise ToolLimitConfigError(f"{self.name}.{field_name} must be an integer")
        if self.minimum > self.maximum:
            raise ToolLimitConfigError(f"{self.name}: minimum exceeds maximum")
        if not self.minimum <= self.default <= self.maximum:
            raise ToolLimitConfigError(
                f"{self.name}: default must be within [{self.minimum}, {self.maximum}]"
            )


@dataclass(frozen=True, slots=True)
class FloatLimitSpec:
    name: str
    default: float
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolLimitConfigError("limit name must be a non-empty string")
        values: dict[str, float] = {}
        for field_name in ("default", "minimum", "maximum"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolLimitConfigError(f"{self.name}.{field_name} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ToolLimitConfigError(f"{self.name}.{field_name} must be finite")
            values[field_name] = numeric
        if values["minimum"] > values["maximum"]:
            raise ToolLimitConfigError(f"{self.name}: minimum exceeds maximum")
        if not values["minimum"] <= values["default"] <= values["maximum"]:
            raise ToolLimitConfigError(
                f"{self.name}: default must be within [{self.minimum}, {self.maximum}]"
            )


def resolve_int_limit(value: int | None, spec: IntLimitSpec) -> int:
    if value is None:
        return spec.default
    if type(value) is not int:
        raise ToolLimitConfigError(f"{spec.name} must be an integer")
    if value < spec.minimum or value > spec.maximum:
        raise ToolLimitConfigError(
            f"{spec.name} must be within [{spec.minimum}, {spec.maximum}]"
        )
    return value


def resolve_float_limit(value: float | int | None, spec: FloatLimitSpec) -> float:
    if value is None:
        return float(spec.default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolLimitConfigError(f"{spec.name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ToolLimitConfigError(f"{spec.name} must be finite")
    if numeric < spec.minimum or numeric > spec.maximum:
        raise ToolLimitConfigError(
            f"{spec.name} must be within [{spec.minimum}, {spec.maximum}]"
        )
    return numeric
