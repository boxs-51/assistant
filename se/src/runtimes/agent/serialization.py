from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AgentPersistenceSerializationError(TypeError):
    """A runtime value cannot be represented safely in durable Agent JSON."""

    code = "AGENT_PERSISTENCE_SERIALIZATION_FAILED"
    failure_domain = "AGENT_PERSISTENCE"
    retryable = False

    def __init__(self, path: str, value: Any, reason: str | None = None) -> None:
        self.path = path
        self.value_type = type(value).__name__
        suffix = f": {reason}" if reason else ""
        super().__init__(
            f"{self.code}: {path} contains unsupported {self.value_type}{suffix}"
        )


def _sort_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def to_json_safe(value: Any, *, path: str = "$") -> Any:
    """Convert one durable value to canonical JSON-safe Python primitives.

    The conversion is recursive and fail-closed. Unknown objects are never
    stringified because that would silently corrupt durable structure.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentPersistenceSerializationError(
                path,
                value,
                "non-finite floats are not canonical JSON",
            )
        return value

    if isinstance(value, BaseModel):
        return to_json_safe(value.model_dump(mode="python"), path=path)

    if isinstance(value, Enum):
        return to_json_safe(value.value, path=path)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AgentPersistenceSerializationError(
                    path,
                    key,
                    "JSON object keys must be strings",
                )
            child_path = f"{path}.{key}" if path else key
            result[key] = to_json_safe(item, path=child_path)
        return result

    if isinstance(value, (list, tuple)):
        return [
            to_json_safe(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    if isinstance(value, (set, frozenset)):
        normalized = [
            to_json_safe(item, path=f"{path}[*]")
            for item in value
        ]
        return sorted(normalized, key=_sort_key)

    raise AgentPersistenceSerializationError(path, value)


__all__ = [
    "AgentPersistenceSerializationError",
    "to_json_safe",
]
