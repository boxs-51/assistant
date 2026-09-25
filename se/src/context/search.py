from __future__ import annotations

from dataclasses import dataclass

from se.src.context.access import (
    BranchEntry,
    ContextAccessAuthorityError,
    DiscoveryDigest,
    SessionEntry,
    TaskEntry,
)
from se.src.context.discovery import BranchDigest, SessionDigest, TaskDigest
from se.src.context.discovery_collection import DiscoveryCollection, build_discovery_collection


@dataclass(frozen=True)
class ContextSearchResult:
    """Immutable finite search result over already-authorized F3A digests."""

    query: str
    hits: tuple[DiscoveryDigest, ...]


def _normalize_query(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("query must be non-empty after trimming whitespace")
    return normalized, normalized.casefold()


def _rebuild_authorized_collection(
    *,
    sessions: tuple[SessionEntry, ...] | list[SessionEntry],
    tasks: tuple[TaskEntry, ...] | list[TaskEntry],
    branches: tuple[BranchEntry, ...] | list[BranchEntry],
) -> DiscoveryCollection:
    if not sessions and not tasks and not branches:
        raise ContextAccessAuthorityError(
            "context search requires at least one canonical evidence entry"
        )

    return build_discovery_collection(
        sessions=sessions,
        tasks=tasks,
        branches=branches,
    )


def _matches_text(query: str, value: str | None) -> bool:
    return value is not None and query in value.casefold()


def _matches_values(query: str, values: tuple[str, ...]) -> bool:
    return any(query in value.casefold() for value in values)


def _matches_digest(query: str, digest: DiscoveryDigest) -> bool:
    if isinstance(digest, SessionDigest):
        return (
            _matches_text(query, digest.title)
            or _matches_text(query, digest.summary)
            or _matches_values(query, digest.topics)
            or _matches_values(query, digest.keywords)
        )

    if isinstance(digest, TaskDigest):
        return (
            _matches_text(query, digest.objective)
            or _matches_text(query, digest.summary)
            or _matches_values(query, digest.topics)
            or _matches_values(query, digest.keywords)
            or _matches_values(query, digest.important_decisions)
            or _matches_values(query, digest.remaining_items)
        )

    if isinstance(digest, BranchDigest):
        return (
            _matches_text(query, digest.summary)
            or _matches_values(query, digest.topics)
            or _matches_values(query, digest.keywords)
            or _matches_values(query, digest.important_decisions)
        )

    raise ValueError("unsupported discovery digest type")


def _context_source_id(digest: DiscoveryDigest) -> str:
    if isinstance(digest, SessionDigest):
        return digest.session_source.context_source_id
    if isinstance(digest, TaskDigest):
        return digest.task_source.context_source_id
    if isinstance(digest, BranchDigest):
        return digest.branch_source.context_source_id
    raise ValueError("unsupported discovery digest type")


def search_context_sources(
    query: object,
    *,
    sessions: tuple[SessionEntry, ...] | list[SessionEntry] = (),
    tasks: tuple[TaskEntry, ...] | list[TaskEntry] = (),
    branches: tuple[BranchEntry, ...] | list[BranchEntry] = (),
) -> ContextSearchResult:
    """Search finite already-loaded F3 evidence with literal structural matching."""

    normalized_query, match_query = _normalize_query(query)
    collection = _rebuild_authorized_collection(
        sessions=sessions,
        tasks=tasks,
        branches=branches,
    )

    hits = [
        digest
        for values in (collection.sessions, collection.tasks, collection.branches)
        for digest in values
        if _matches_digest(match_query, digest)
    ]
    hits.sort(key=_context_source_id)

    return ContextSearchResult(
        query=normalized_query,
        hits=tuple(hits),
    )


__all__ = [
    "ContextSearchResult",
    "search_context_sources",
]
