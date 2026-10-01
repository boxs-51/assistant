from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from jsonschema import SchemaError, ValidationError, validate

from .contracts.definition import CapabilityDefinition, validate_input_schema


CAPABILITY_INVALID_ARGUMENT = "CAPABILITY_INVALID_ARGUMENT"
CAPABILITY_SCHEMA_INVALID = "CAPABILITY_SCHEMA_INVALID"


@dataclass(frozen=True, slots=True)
class CapabilityArgumentValidationResult:
    valid: bool
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def ok(cls) -> "CapabilityArgumentValidationResult":
        return cls(valid=True)

    @classmethod
    def invalid(
        cls,
        code: str,
        message: str,
    ) -> "CapabilityArgumentValidationResult":
        return cls(
            valid=False,
            error_code=code,
            error_message=message,
        )


class CapabilityArgumentValidator(Protocol):
    def validate(
        self,
        definition: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> CapabilityArgumentValidationResult:
        ...


class JsonSchemaCapabilityArgumentValidator:
    """Pure capability-layer JSON Schema validation with no side effects."""

    def validate(
        self,
        definition: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> CapabilityArgumentValidationResult:
        schema = dict(definition.input_schema or {"type": "object"})
        try:
            validate_input_schema(schema)
            validate(instance=dict(arguments), schema=schema)
        except ValidationError as exc:
            return CapabilityArgumentValidationResult.invalid(
                CAPABILITY_INVALID_ARGUMENT,
                str(exc.message),
            )
        except SchemaError as exc:
            return CapabilityArgumentValidationResult.invalid(
                CAPABILITY_SCHEMA_INVALID,
                str(exc),
            )
        return CapabilityArgumentValidationResult.ok()
