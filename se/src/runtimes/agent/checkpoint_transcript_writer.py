from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ...infrastructure.storage.transcript_representation import (
    HARD_MAX_DELTA_DEPTH,
    canonical_json_bytes,
    canonical_transcript_messages,
    logical_transcript_fingerprint,
    transcript_chunk_id,
    transcript_payload_root_ref,
    transcript_representation_ref,
)
from .checkpoint_transcript import materialize_checkpoint_transcript_in_uow


@dataclass(frozen=True)
class ProvenTranscriptRepresentation:
    transcript_ref: str
    transcript_version: int


async def _save_chunk(repo, messages: Sequence[dict[str, Any]]):
    payload = canonical_transcript_messages(messages)
    chunk_id = transcript_chunk_id(payload)
    return await repo.save_transcript_chunk(
        {
            "chunk_id": chunk_id,
            "payload": payload,
            "message_count": len(payload),
            "canonical_bytes": len(canonical_json_bytes(payload)),
        }
    )


async def _save_payload_root(
    repo,
    *,
    messages: Sequence[dict[str, Any]],
    parent_payload_root_ref: str | None = None,
):
    payload = canonical_transcript_messages(messages)
    chunk = await _save_chunk(repo, payload)
    parent_count = 0
    if parent_payload_root_ref is not None:
        parent = await repo.get_transcript_payload_node(parent_payload_root_ref)
        if parent is None:
            raise ValueError("Missing structural-share payload parent.")
        parent_count = int(parent.logical_message_count)
    logical_count = parent_count + len(payload)
    root_ref = transcript_payload_root_ref(
        parent_payload_root_ref=parent_payload_root_ref,
        chunk_id=chunk.chunk_id,
        logical_message_count=logical_count,
    )
    return await repo.save_transcript_payload_node(
        {
            "payload_root_ref": root_ref,
            "parent_payload_root_ref": parent_payload_root_ref,
            "chunk_id": chunk.chunk_id,
            "logical_message_count": logical_count,
        }
    )


async def _save_payload_chain(
    repo,
    *,
    messages: Sequence[dict[str, Any]],
    parent_payload_root_ref: str | None = None,
):
    canonical = canonical_transcript_messages(messages)
    current = parent_payload_root_ref
    if not canonical:
        if current is not None:
            return await repo.get_transcript_payload_node(current)
        return await _save_payload_root(repo, messages=())

    node = None
    for message in canonical:
        node = await _save_payload_root(
            repo,
            messages=(message,),
            parent_payload_root_ref=current,
        )
        current = str(node.payload_root_ref)
    return node


async def _ensure_cumulative_payload_root(repo, representation) -> str:
    kind = str(representation.kind).upper()
    if kind == "FULL":
        return str(representation.payload_root_ref)
    if kind != "DELTA":
        raise ValueError("Stored transcript representation kind is invalid.")

    parent_ref = representation.parent_transcript_ref
    parent_version = representation.parent_transcript_version
    if parent_ref is None or parent_version is None:
        raise ValueError("DELTA transcript representation has no exact parent.")

    parent = await repo.get_transcript_representation(
        str(parent_ref),
        int(parent_version),
    )
    if parent is None:
        raise ValueError("DELTA transcript representation parent is missing.")

    cumulative_parent = await _ensure_cumulative_payload_root(repo, parent)
    suffix = await repo.materialize_transcript_payload_root(
        str(representation.payload_root_ref)
    )
    root = await _save_payload_chain(
        repo,
        messages=suffix,
        parent_payload_root_ref=cumulative_parent,
    )
    if root is None:
        raise ValueError("DELTA cumulative payload root could not be created.")
    return str(root.payload_root_ref)


async def _payload_root_at_count(
    repo,
    *,
    cumulative_payload_root_ref: str,
    logical_message_count: int,
) -> str | None:
    if logical_message_count < 0:
        raise ValueError("logical_message_count must be non-negative")
    if logical_message_count == 0:
        return None

    current_ref: str | None = cumulative_payload_root_ref
    while current_ref is not None:
        node = await repo.get_transcript_payload_node(current_ref)
        if node is None:
            raise ValueError("Missing structural-share payload node.")
        count = int(node.logical_message_count)
        if count == logical_message_count:
            return str(node.payload_root_ref)
        if count < logical_message_count:
            raise ValueError("No exact payload-root boundary for proven prefix.")
        current_ref = node.parent_payload_root_ref
    raise ValueError("No payload-root boundary for proven prefix.")


async def _save_full(
    repo,
    *,
    messages: Sequence[dict[str, Any]],
    payload_root_ref: str,
):
    canonical = canonical_transcript_messages(messages)
    fingerprint = logical_transcript_fingerprint(canonical)
    ref = transcript_representation_ref(
        transcript_version=0,
        kind="FULL",
        parent_transcript_ref=None,
        parent_transcript_version=None,
        delta_depth=0,
        logical_message_count=len(canonical),
        logical_transcript_fingerprint=fingerprint,
        payload_root_ref=payload_root_ref,
    )
    return await repo.save_transcript_representation(
        {
            "transcript_ref": ref,
            "transcript_version": 0,
            "kind": "FULL",
            "parent_transcript_ref": None,
            "parent_transcript_version": None,
            "delta_depth": 0,
            "logical_message_count": len(canonical),
            "logical_transcript_fingerprint": fingerprint,
            "payload_root_ref": payload_root_ref,
        }
    )


async def _save_delta(
    repo,
    *,
    parent,
    messages: Sequence[dict[str, Any]],
    suffix: Sequence[dict[str, Any]],
):
    canonical = canonical_transcript_messages(messages)
    suffix_root = await _save_payload_chain(repo, messages=suffix)
    if suffix_root is None:
        raise ValueError("DELTA suffix must be non-empty.")
    version = int(parent.transcript_version) + 1
    depth = int(parent.delta_depth) + 1
    fingerprint = logical_transcript_fingerprint(canonical)
    ref = transcript_representation_ref(
        transcript_version=version,
        kind="DELTA",
        parent_transcript_ref=str(parent.transcript_ref),
        parent_transcript_version=int(parent.transcript_version),
        delta_depth=depth,
        logical_message_count=len(canonical),
        logical_transcript_fingerprint=fingerprint,
        payload_root_ref=str(suffix_root.payload_root_ref),
    )
    return await repo.save_transcript_representation(
        {
            "transcript_ref": ref,
            "transcript_version": version,
            "kind": "DELTA",
            "parent_transcript_ref": str(parent.transcript_ref),
            "parent_transcript_version": int(parent.transcript_version),
            "delta_depth": depth,
            "logical_message_count": len(canonical),
            "logical_transcript_fingerprint": fingerprint,
            "payload_root_ref": str(suffix_root.payload_root_ref),
        }
    )


def _common_prefix_length(
    left: Sequence[dict[str, Any]],
    right: Sequence[dict[str, Any]],
) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


async def _save_logical_full_with_prefix_share(
    repo,
    *,
    messages: Sequence[dict[str, Any]],
    parent_representation,
    parent_messages: Sequence[dict[str, Any]] | None,
):
    canonical = canonical_transcript_messages(messages)
    common = (
        _common_prefix_length(parent_messages, canonical)
        if parent_representation is not None and parent_messages is not None
        else 0
    )

    shared_root: str | None = None
    if parent_representation is not None and common > 0:
        cumulative = await _ensure_cumulative_payload_root(repo, parent_representation)
        try:
            shared_root = await _payload_root_at_count(
                repo,
                cumulative_payload_root_ref=cumulative,
                logical_message_count=common,
            )
        except ValueError as exc:
            if "payload-root boundary" not in str(exc):
                raise
            # B1 permits coarse immutable chunks with no node boundary at the
            # proven common-prefix length. Normalize only that proven prefix
            # into deterministic content-addressed per-message roots. Existing
            # B1 rows remain immutable; repeated normalization converges.
            normalized = await _save_payload_chain(
                repo,
                messages=parent_messages[:common],
            )
            if normalized is None:
                raise ValueError(
                    "Proven common prefix could not be normalized."
                ) from exc
            shared_root = str(normalized.payload_root_ref)

    suffix = canonical[common:]
    if suffix:
        root = await _save_payload_chain(
            repo,
            messages=suffix,
            parent_payload_root_ref=shared_root,
        )
        if root is None:
            raise ValueError("FULL payload root could not be created.")
        payload_root_ref = str(root.payload_root_ref)
    elif shared_root is not None:
        payload_root_ref = shared_root
    else:
        root = await _save_payload_chain(repo, messages=canonical)
        if root is None:
            raise ValueError("FULL payload root could not be created.")
        payload_root_ref = str(root.payload_root_ref)

    return await _save_full(
        repo,
        messages=canonical,
        payload_root_ref=payload_root_ref,
    )


async def write_transcript_representation_in_uow(
    uow,
    *,
    messages: Sequence[dict[str, Any]],
    candidate_parent_checkpoint_id: str | None = None,
) -> ProvenTranscriptRepresentation:
    """Create/reuse one immutable representation inside the caller's UoW."""

    repo = uow.agents
    canonical = canonical_transcript_messages(messages)

    parent_representation = None
    parent_messages: list[dict[str, Any]] | None = None
    if candidate_parent_checkpoint_id is not None:
        candidate = await repo.get_execution_checkpoint(candidate_parent_checkpoint_id)
        if candidate is not None:
            proven_parent = await materialize_checkpoint_transcript_in_uow(
                uow,
                candidate,
            )
            parent_messages = canonical_transcript_messages(proven_parent)
            if (
                candidate.transcript_ref is not None
                and candidate.transcript_version is not None
            ):
                parent_representation = await repo.get_transcript_representation(
                    str(candidate.transcript_ref),
                    int(candidate.transcript_version),
                )
                if parent_representation is None:
                    raise ValueError(
                        "Candidate checkpoint references missing representation."
                    )

    if parent_representation is not None and parent_messages == canonical:
        return ProvenTranscriptRepresentation(
            transcript_ref=str(parent_representation.transcript_ref),
            transcript_version=int(parent_representation.transcript_version),
        )

    if (
        parent_representation is not None
        and parent_messages is not None
        and len(canonical) > len(parent_messages)
        and canonical[: len(parent_messages)] == parent_messages
    ):
        suffix = canonical[len(parent_messages) :]
        next_depth = int(parent_representation.delta_depth) + 1
        if next_depth <= HARD_MAX_DELTA_DEPTH:
            record = await _save_delta(
                repo,
                parent=parent_representation,
                messages=canonical,
                suffix=suffix,
            )
        else:
            cumulative_parent = await _ensure_cumulative_payload_root(
                repo,
                parent_representation,
            )
            full_root = await _save_payload_chain(
                repo,
                messages=suffix,
                parent_payload_root_ref=cumulative_parent,
            )
            if full_root is None:
                raise ValueError("FULL re-anchor payload root could not be created.")
            record = await _save_full(
                repo,
                messages=canonical,
                payload_root_ref=str(full_root.payload_root_ref),
            )
    else:
        record = await _save_logical_full_with_prefix_share(
            repo,
            messages=canonical,
            parent_representation=parent_representation,
            parent_messages=parent_messages,
        )

    materialized = await repo.materialize_transcript_representation(
        str(record.transcript_ref),
        int(record.transcript_version),
    )
    if canonical_transcript_messages(materialized) != canonical:
        raise ValueError(
            "Written transcript representation does not round-trip canonically."
        )

    return ProvenTranscriptRepresentation(
        transcript_ref=str(record.transcript_ref),
        transcript_version=int(record.transcript_version),
    )
