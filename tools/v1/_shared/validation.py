from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from .errors import ToolContractError, ToolJsonSafetyError

REDACTED: Final[str] = "[REDACTED]"

_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "client_key",
        "proxy_password",
    }
)


def _child_path(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{key!r}]"


def assert_json_safe(value: Any, *, path: str = "$", _stack: set[int] | None = None) -> None:
    """Validate the strict Tools V1 JSON value contract.

    Only None/bool/int/finite-float/str/list/dict[str, JSONValue] are accepted.
    Recursive containers are rejected with a logical path.
    """
    if value is None or isinstance(value, (bool, str)):
        return

    if type(value) is int:
        return

    if type(value) is float:
        if not math.isfinite(value):
            raise ToolJsonSafetyError(f"non-finite float at {path}")
        return

    if isinstance(value, (bytes, bytearray, memoryview, Path, set, frozenset, tuple)):
        raise ToolJsonSafetyError(
            f"non-JSON-safe {type(value).__name__} at {path}"
        )

    if _stack is None:
        _stack = set()

    if isinstance(value, list):
        marker = id(value)
        if marker in _stack:
            raise ToolJsonSafetyError(f"recursive container at {path}")
        _stack.add(marker)
        try:
            for index, item in enumerate(value):
                assert_json_safe(item, path=f"{path}[{index}]", _stack=_stack)
        finally:
            _stack.remove(marker)
        return

    if isinstance(value, Mapping):
        marker = id(value)
        if marker in _stack:
            raise ToolJsonSafetyError(f"recursive container at {path}")
        _stack.add(marker)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ToolJsonSafetyError(
                        f"non-string mapping key {key!r} at {path}"
                    )
                assert_json_safe(item, path=_child_path(path, key), _stack=_stack)
        finally:
            _stack.remove(marker)
        return

    raise ToolJsonSafetyError(
        f"non-JSON-safe {type(value).__name__} at {path}"
    )


def require_non_empty_string(
    value: Any,
    *,
    name: str,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolContractError(f"{name} must be a non-empty string")
    if max_length is not None:
        if type(max_length) is not int or max_length < 1:
            raise ToolContractError(f"{name} max_length must be a positive integer")
        if len(value) > max_length:
            raise ToolContractError(f"{name} exceeds maximum length {max_length}")
    return value


def require_string_list(
    value: Any,
    *,
    name: str,
    unique: bool = False,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise ToolContractError(f"{name} must be a list of strings")
    if not allow_empty and not value:
        raise ToolContractError(f"{name} must not be empty")

    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ToolContractError(f"{name}[{index}] must be a non-empty string")
        if unique and item in seen:
            raise ToolContractError(f"{name} contains duplicate value {item!r}")
        seen.add(item)
        result.append(item)
    return result


def _normalized_sensitive_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def redact_sensitive(value: Any) -> Any:
    """Return a recursively redacted copy of a JSON-like value."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _normalized_sensitive_key(key_str) in _SENSITIVE_KEYS:
                redacted[key_str] = REDACTED
            else:
                redacted[key_str] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def redact_url_credentials(url: str) -> str:
    """Redact URL userinfo while preserving the destination and path."""
    if not isinstance(url, str) or not url:
        return url

    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url

        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parts.port
        except ValueError:
            port = None
        hostport = f"{hostname}:{port}" if port is not None else hostname
        netloc = f"redacted@{hostport}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return REDACTED
