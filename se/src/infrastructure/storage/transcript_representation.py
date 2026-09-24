from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from ...runtimes.agent.contracts.inference import InferenceMessage


HARD_MAX_DELTA_DEPTH = 9
TRANSCRIPT_IDENTITY_DOMAIN = "agent-transcript-representation-v1"
TRANSCRIPT_CHUNK_DOMAIN = "agent-transcript-chunk-v1"
TRANSCRIPT_PAYLOAD_ROOT_DOMAIN = "agent-transcript-payload-root-v1"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_domain(domain: str, payload: Any) -> str:
    encoded = domain.encode("utf-8") + b"\x00" + canonical_json_bytes(payload)
    return hashlib.sha256(encoded).hexdigest()


def canonical_transcript_messages(
    messages: Sequence[InferenceMessage | dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json")
        if isinstance(item, InferenceMessage)
        else InferenceMessage.model_validate(item).model_dump(mode="json")
        for item in messages
    ]


def transcript_chunk_id(
    messages: Sequence[InferenceMessage | dict[str, Any]],
) -> str:
    return _sha256_domain(
        TRANSCRIPT_CHUNK_DOMAIN,
        canonical_transcript_messages(messages),
    )


def logical_transcript_fingerprint(
    messages: Sequence[InferenceMessage | dict[str, Any]],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(canonical_transcript_messages(messages))
    ).hexdigest()


def transcript_payload_root_ref(
    *,
    parent_payload_root_ref: str | None,
    chunk_id: str,
    logical_message_count: int,
) -> str:
    if logical_message_count < 0:
        raise ValueError("logical_message_count must be non-negative")
    return _sha256_domain(
        TRANSCRIPT_PAYLOAD_ROOT_DOMAIN,
        {
            "parent_payload_root_ref": parent_payload_root_ref,
            "chunk_id": chunk_id,
            "logical_message_count": logical_message_count,
        },
    )


def transcript_representation_ref(
    *,
    transcript_version: int,
    kind: str,
    parent_transcript_ref: str | None,
    parent_transcript_version: int | None,
    delta_depth: int,
    logical_message_count: int,
    logical_transcript_fingerprint: str,
    payload_root_ref: str,
) -> str:
    normalized_kind = str(kind).upper()
    if normalized_kind not in {"FULL", "DELTA"}:
        raise ValueError("kind must be FULL or DELTA")
    if transcript_version < 0:
        raise ValueError("transcript_version must be non-negative")
    if not 0 <= delta_depth <= HARD_MAX_DELTA_DEPTH:
        raise ValueError("delta_depth outside R11-B safety envelope")
    if logical_message_count < 0:
        raise ValueError("logical_message_count must be non-negative")
    return _sha256_domain(
        TRANSCRIPT_IDENTITY_DOMAIN,
        {
            "transcript_version": transcript_version,
            "kind": normalized_kind,
            "parent_transcript_ref": parent_transcript_ref,
            "parent_transcript_version": parent_transcript_version,
            "delta_depth": delta_depth,
            "logical_message_count": logical_message_count,
            "logical_transcript_fingerprint": logical_transcript_fingerprint,
            "payload_root_ref": payload_root_ref,
        },
    )
