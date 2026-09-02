from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from jsonschema import SchemaError, ValidationError, validate

from ....runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    validate_input_schema,
)
from .errors import CAPABILITY_INVALID_ARGUMENT, CAPABILITY_SCHEMA_INVALID


def normalized_input_schema(definition: CapabilityDefinition) -> dict[str, Any]:
    """Return the schema used by the Agent tool execution boundary."""
    return dict(definition.input_schema or {"type": "object"})


@dataclass(frozen=True, slots=True)
class ToolArgumentValidationResult:
    valid: bool
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def ok(cls) -> "ToolArgumentValidationResult":
        return cls(valid=True)

    @classmethod
    def invalid(cls, message: str) -> "ToolArgumentValidationResult":
        return cls(
            valid=False,
            error_code=CAPABILITY_INVALID_ARGUMENT,
            error_message=message,
        )

    @classmethod
    def invalid_schema(cls, message: str) -> "ToolArgumentValidationResult":
        return cls(
            valid=False,
            error_code=CAPABILITY_SCHEMA_INVALID,
            error_message=message,
        )


class ToolArgumentValidator(Protocol):
    def validate(
        self,
        definition: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> ToolArgumentValidationResult:
        ...


class JsonSchemaToolArgumentValidator:
    """Pure JSON Schema validation with no capability side effects."""

    def validate(
        self,
        definition: CapabilityDefinition,
        arguments: Mapping[str, Any],
    ) -> ToolArgumentValidationResult:
        schema = normalized_input_schema(definition)
        try:
            validate_input_schema(schema)
            validate(instance=dict(arguments), schema=schema)
        except ValidationError as exc:
            return ToolArgumentValidationResult.invalid(str(exc.message))
        except SchemaError as exc:
            return ToolArgumentValidationResult.invalid_schema(str(exc))
        return ToolArgumentValidationResult.ok()
