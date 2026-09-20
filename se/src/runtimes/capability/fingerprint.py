from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def capability_request_fingerprint(
    *,
    capability_id: str,
    capability_version: str,
    arguments: Mapping[str, Any],
) -> str:
    """Return the R6 semantic identity for one logical capability request.

    The caller must provide JSON-safe arguments. R6 deliberately rejects
    Python-only values instead of silently inventing an encoding that a
    different worker or client might serialize differently.
    """
    if not capability_id:
        raise ValueError("capability_id must be non-empty")
    if not capability_version:
        raise ValueError("capability_version must be non-empty")

    payload = {
        "capability_id": capability_id,
        "capability_version": capability_version,
        "arguments": dict(arguments),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
