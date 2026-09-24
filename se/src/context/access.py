from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from se.src.context.discovery import BranchDigest, SessionDigest, TaskDigest
from se.src.context.discovery_collection import DiscoveryCollection, build_discovery_collection
from se.src.context.source_adapters import BranchEvidence, SessionEvidence, TaskEvidence
from se.src.context.source_identity import ContextSourceKind, ContextSourceRef


SessionEntry: TypeAlias = tuple[SessionEvidence, SessionDigest]
TaskEntry: TypeAlias = tuple[SessionEvidence, TaskEvidence, TaskDigest]
BranchEntry: TypeAlias = tuple[
    SessionEvidence,
    TaskEvidence,
    BranchEvidence,
    BranchDigest,
]
DiscoveryDigest: TypeAlias = SessionDigest | TaskDigest | BranchDigest

_CONTEXT_SOURCE_ID_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


class ContextAccessStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"


class ContextAccessAuthorityError(ValueError):
    """The access call has no canonical evidence from which to derive owner authority."""


@dataclass(frozen=True)
class ContextResolveResult:
    """Immutable structural resolve result for one exact canonical source identity."""

    status: ContextAccessStatus
    context_source_id: str
    digest: DiscoveryDigest | None = None


@dataclass(frozen=True)
class ContextDescription:
    """Structural metadata copied only from an authorized F3A/F3B value."""

    context_source_id: str
    source_kind: ContextSourceKind
    owner_user_id: str
    authority_id: str
    authority_version: int | None
    session_id: str | None
    task_id: str | None
    branch_id: str | None
    source_created_at: datetime | None
    source_state: str | None
    projection_version: str
    summary: str | None
    topics: tuple[str, ...]
    keywords: tuple[str, ...]
    title: str | None = None
    objective: str | None = None
    important_decisions: tuple[str, ...] = ()
    remaining_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextDescribeResult:
    """Immutable structural describe result for one exact canonical source identity."""

    status: ContextAccessStatus
    context_source_id: str
    description: ContextDescription | None = None


def _require_exact_context_source_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("context_source_id must be a string")
    if value != value.strip() or not value:
        raise ValueError("context_source_id must be non-empty and already normalized")
    if len(value) != _CONTEXT_SOURCE_ID_LENGTH or any(
        character not in _HEX_DIGITS for character in value
    ):
        raise ValueError("context_source_id must be an exact canonical lowercase sha256 identity")
    return value


def _rebuild_authorized_collection(
    *,
    sessions: tuple[SessionEntry, ...] | list[SessionEntry],
    tasks: tuple[TaskEntry, ...] | list[TaskEntry],
    branches: tuple[BranchEntry, ...] | list[BranchEntry],
) -> DiscoveryCollection:
    # No evidence means there is no canonical owner scope in which absence can
    # safely be classified as NOT_FOUND. Keep that state distinct and fail closed.
    if not sessions and not tasks and not branches:
        raise ContextAccessAuthorityError(
            "context access requires at least one canonical evidence entry"
        )

    # The public F4A boundary deliberately does not accept DiscoveryCollection.
    # Rebuilding here reuses the FINAL-GREEN F3B provenance boundary on every call.
    return build_discovery_collection(
        sessions=sessions,
        tasks=tasks,
        branches=branches,
    )


def _source_for_digest(digest: DiscoveryDigest) -> ContextSourceRef:
    if isinstance(digest, SessionDigest):
        return digest.session_source
    if isinstance(digest, TaskDigest):
        return digest.task_source
    if isinstance(digest, BranchDigest):
        return digest.branch_source
    raise ValueError("unsupported discovery digest type")


def _resolve_in_collection(
    collection: DiscoveryCollection,
    context_source_id: str,
) -> DiscoveryDigest | None:
    for values in (collection.sessions, collection.tasks, collection.branches):
        for digest in values:
            if _source_for_digest(digest).context_source_id == context_source_id:
                return digest
    return None


def resolve_context_source(
    context_source_id: str,
    *,
    sessions: tuple[SessionEntry, ...] | list[SessionEntry] = (),
    tasks: tuple[TaskEntry, ...] | list[TaskEntry] = (),
    branches: tuple[BranchEntry, ...] | list[BranchEntry] = (),
) -> ContextResolveResult:
    """Resolve one exact source identity from freshly re-proven finite F3B inputs."""

    selector = _require_exact_context_source_id(context_source_id)
    collection = _rebuild_authorized_collection(
        sessions=sessions,
        tasks=tasks,
        branches=branches,
    )
    digest = _resolve_in_collection(collection, selector)
    if digest is None:
        return ContextResolveResult(
            status=ContextAccessStatus.NOT_FOUND,
            context_source_id=selector,
        )
    return ContextResolveResult(
        status=ContextAccessStatus.FOUND,
        context_source_id=selector,
        digest=digest,
    )


def _describe_digest(digest: DiscoveryDigest) -> ContextDescription:
    source = _source_for_digest(digest)

    if isinstance(digest, SessionDigest):
        return ContextDescription(
            context_source_id=source.context_source_id,
            source_kind=source.source_kind,
            owner_user_id=source.owner_user_id,
            authority_id=source.authority_id,
            authority_version=source.authority_version,
            session_id=source.session_id,
            task_id=source.task_id,
            branch_id=source.branch_id,
            source_created_at=source.source_created_at,
            source_state=source.source_state,
            projection_version=digest.projection_version,
            summary=digest.summary,
            topics=digest.topics,
            keywords=digest.keywords,
            title=digest.title,
        )

    if isinstance(digest, TaskDigest):
        return ContextDescription(
            context_source_id=source.context_source_id,
            source_kind=source.source_kind,
            owner_user_id=source.owner_user_id,
            authority_id=source.authority_id,
            authority_version=source.authority_version,
            session_id=source.session_id,
            task_id=source.task_id,
            branch_id=source.branch_id,
            source_created_at=source.source_created_at,
            source_state=source.source_state,
            projection_version=digest.projection_version,
            summary=digest.summary,
            topics=digest.topics,
            keywords=digest.keywords,
            objective=digest.objective,
            important_decisions=digest.important_decisions,
            remaining_items=digest.remaining_items,
        )

    if isinstance(digest, BranchDigest):
        return ContextDescription(
            context_source_id=source.context_source_id,
            source_kind=source.source_kind,
            owner_user_id=source.owner_user_id,
            authority_id=source.authority_id,
            authority_version=source.authority_version,
            session_id=source.session_id,
            task_id=source.task_id,
            branch_id=source.branch_id,
            source_created_at=source.source_created_at,
            source_state=source.source_state,
            projection_version=digest.projection_version,
            summary=digest.summary,
            topics=digest.topics,
            keywords=digest.keywords,
            important_decisions=digest.important_decisions,
        )

    raise ValueError("unsupported discovery digest type")


def describe_context_source(
    context_source_id: str,
    *,
    sessions: tuple[SessionEntry, ...] | list[SessionEntry] = (),
    tasks: tuple[TaskEntry, ...] | list[TaskEntry] = (),
    branches: tuple[BranchEntry, ...] | list[BranchEntry] = (),
) -> ContextDescribeResult:
    """Describe one exact source using only metadata already present in F3A/F3B."""

    selector = _require_exact_context_source_id(context_source_id)
    collection = _rebuild_authorized_collection(
        sessions=sessions,
        tasks=tasks,
        branches=branches,
    )
    digest = _resolve_in_collection(collection, selector)
    if digest is None:
        return ContextDescribeResult(
            status=ContextAccessStatus.NOT_FOUND,
            context_source_id=selector,
        )
    return ContextDescribeResult(
        status=ContextAccessStatus.FOUND,
        context_source_id=selector,
        description=_describe_digest(digest),
    )


__all__ = [
    "BranchEntry",
    "ContextAccessAuthorityError",
    "ContextAccessStatus",
    "ContextDescribeResult",
    "ContextDescription",
    "ContextResolveResult",
    "DiscoveryDigest",
    "SessionEntry",
    "TaskEntry",
    "describe_context_source",
    "resolve_context_source",
]
