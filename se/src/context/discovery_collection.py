from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from se.src.context.discovery import (
    DISCOVERY_PROJECTION_VERSION,
    BranchDigest,
    SessionDigest,
    TaskDigest,
    project_branch_digest,
    project_session_digest,
    project_task_digest,
)
from se.src.context.source_adapters import (
    BranchEvidence,
    SessionEvidence,
    TaskEvidence,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    ContextSourceRef,
    validate_context_source_ref_integrity,
)


DISCOVERY_COLLECTION_VERSION = "ctx-f3b-v1"
_COLLECTION_FACTORY_TOKEN = object()


def _require_non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    if normalized != value:
        raise ValueError(f"{name} must already be normalized")
    return normalized


def _require_finite_sequence(name: str, value: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a finite list or tuple")
    return tuple(value)


def _require_entry(name: str, value: Any, size: int) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{name} must include canonical evidence alongside one F3A digest")
    return tuple(value)


def _validate_optional_canonical_text(name: str, value: Any) -> None:
    if value is None:
        return
    _require_non_empty(name, value)


def _validate_canonical_text_tuple(name: str, value: Any) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be a canonical tuple of strings")

    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _require_non_empty(f"{name}[{index}]", item)
        if text in seen:
            raise ValueError(f"{name} must not contain duplicates")
        seen.add(text)


def _validate_common_digest_contract(digest: SessionDigest | TaskDigest | BranchDigest) -> None:
    if digest.projection_version != DISCOVERY_PROJECTION_VERSION:
        raise ValueError(
            "digest projection_version must match the CTX-F3A contract consumed by F3B"
        )
    _validate_optional_canonical_text("digest.summary", digest.summary)
    _validate_canonical_text_tuple("digest.topics", digest.topics)
    _validate_canonical_text_tuple("digest.keywords", digest.keywords)


def _validate_source(
    ref: ContextSourceRef,
    *,
    expected_kind: ContextSourceKind,
    label: str,
) -> ContextSourceRef:
    validate_context_source_ref_integrity(ref)
    if ref.source_kind is not expected_kind:
        raise ValueError(f"{label} must be {expected_kind.value}")
    if not isinstance(ref.source_state, str) or not ref.source_state.strip():
        raise ValueError(f"{label}.source_state must be present")
    if ref.source_state != ref.source_state.strip():
        raise ValueError(f"{label}.source_state must already be normalized")
    return ref


def _validate_session_digest(digest: SessionDigest) -> tuple[str, str]:
    if not isinstance(digest, SessionDigest):
        raise ValueError("sessions must contain SessionDigest values")

    _validate_common_digest_contract(digest)
    _validate_optional_canonical_text("session.title", digest.title)

    source = _validate_source(
        digest.session_source,
        expected_kind=ContextSourceKind.SESSION,
        label="session_source",
    )
    if source.session_id != source.authority_id:
        raise ValueError("session digest lineage mismatch")
    if source.task_id is not None or source.branch_id is not None:
        raise ValueError("session digest must not carry task/branch lineage")
    return source.owner_user_id, source.context_source_id


def _validate_task_digest(digest: TaskDigest) -> tuple[str, str]:
    if not isinstance(digest, TaskDigest):
        raise ValueError("tasks must contain TaskDigest values")

    _validate_common_digest_contract(digest)
    _validate_optional_canonical_text("task.objective", digest.objective)
    _validate_canonical_text_tuple(
        "task.important_decisions",
        digest.important_decisions,
    )
    _validate_canonical_text_tuple("task.remaining_items", digest.remaining_items)

    session_source = _validate_source(
        digest.session_source,
        expected_kind=ContextSourceKind.SESSION,
        label="task.session_source",
    )
    task_source = _validate_source(
        digest.task_source,
        expected_kind=ContextSourceKind.TASK,
        label="task.task_source",
    )

    if session_source.session_id != session_source.authority_id:
        raise ValueError("task session authority mismatch")
    if session_source.task_id is not None or session_source.branch_id is not None:
        raise ValueError("task session source must not carry task/branch lineage")
    if task_source.owner_user_id != session_source.owner_user_id:
        raise ValueError("task digest owner mismatch")
    if task_source.session_id != session_source.authority_id:
        raise ValueError("task digest session lineage mismatch")
    if task_source.task_id != task_source.authority_id:
        raise ValueError("task digest task authority mismatch")
    if task_source.branch_id is not None:
        raise ValueError("task digest must not carry branch lineage")

    return task_source.owner_user_id, task_source.context_source_id


def _validate_branch_digest(digest: BranchDigest) -> tuple[str, str]:
    if not isinstance(digest, BranchDigest):
        raise ValueError("branches must contain BranchDigest values")

    _validate_common_digest_contract(digest)
    _validate_canonical_text_tuple(
        "branch.important_decisions",
        digest.important_decisions,
    )

    session_source = _validate_source(
        digest.session_source,
        expected_kind=ContextSourceKind.SESSION,
        label="branch.session_source",
    )
    task_source = _validate_source(
        digest.task_source,
        expected_kind=ContextSourceKind.TASK,
        label="branch.task_source",
    )
    branch_source = _validate_source(
        digest.branch_source,
        expected_kind=ContextSourceKind.BRANCH,
        label="branch.branch_source",
    )

    if session_source.session_id != session_source.authority_id:
        raise ValueError("branch session authority mismatch")
    if session_source.task_id is not None or session_source.branch_id is not None:
        raise ValueError("branch session source must not carry task/branch lineage")
    if task_source.owner_user_id != session_source.owner_user_id:
        raise ValueError("branch task owner mismatch")
    if task_source.session_id != session_source.authority_id:
        raise ValueError("branch task session lineage mismatch")
    if task_source.task_id != task_source.authority_id:
        raise ValueError("branch task authority mismatch")
    if task_source.branch_id is not None:
        raise ValueError("branch task source must not carry branch lineage")
    if branch_source.owner_user_id != session_source.owner_user_id:
        raise ValueError("branch digest owner mismatch")
    if branch_source.session_id != session_source.authority_id:
        raise ValueError("branch digest session lineage mismatch")
    if branch_source.task_id != task_source.authority_id:
        raise ValueError("branch digest task lineage mismatch")
    if branch_source.branch_id != branch_source.authority_id:
        raise ValueError("branch digest branch authority mismatch")

    return branch_source.owner_user_id, branch_source.context_source_id


def _canonical_session_from_entry(entry: Any) -> SessionDigest:
    session, digest = _require_entry("session entry", entry, 2)
    if not isinstance(session, SessionEvidence):
        raise ValueError("session entry requires canonical SessionEvidence")
    _validate_session_digest(digest)

    expected = project_session_digest(
        session,
        title=digest.title,
        summary=digest.summary,
        topics=digest.topics,
        keywords=digest.keywords,
        projection_version=digest.projection_version,
    )
    if expected != digest:
        raise ValueError("session digest provenance does not match canonical evidence")
    return digest


def _canonical_task_from_entry(entry: Any) -> TaskDigest:
    session, task, digest = _require_entry("task entry", entry, 3)
    if not isinstance(session, SessionEvidence):
        raise ValueError("task entry requires canonical SessionEvidence")
    if not isinstance(task, TaskEvidence):
        raise ValueError("task entry requires canonical TaskEvidence")
    _validate_task_digest(digest)

    expected = project_task_digest(
        session,
        task,
        objective=digest.objective,
        summary=digest.summary,
        topics=digest.topics,
        keywords=digest.keywords,
        important_decisions=digest.important_decisions,
        remaining_items=digest.remaining_items,
        projection_version=digest.projection_version,
    )
    if expected != digest:
        raise ValueError("task digest provenance does not match canonical evidence")
    return digest


def _canonical_branch_from_entry(entry: Any) -> BranchDigest:
    session, task, branch, digest = _require_entry("branch entry", entry, 4)
    if not isinstance(session, SessionEvidence):
        raise ValueError("branch entry requires canonical SessionEvidence")
    if not isinstance(task, TaskEvidence):
        raise ValueError("branch entry requires canonical TaskEvidence")
    if not isinstance(branch, BranchEvidence):
        raise ValueError("branch entry requires canonical BranchEvidence")
    _validate_branch_digest(digest)

    expected = project_branch_digest(
        session,
        task,
        branch,
        summary=digest.summary,
        topics=digest.topics,
        keywords=digest.keywords,
        important_decisions=digest.important_decisions,
        projection_version=digest.projection_version,
    )
    if expected != digest:
        raise ValueError("branch digest provenance does not match canonical evidence")
    return digest


def _dedupe_and_sort(
    *,
    name: str,
    values: tuple[Any, ...],
    expected_type: type,
    validator,
    seen: dict[str, Any],
    owners: set[str],
) -> tuple[Any, ...]:
    unique: dict[str, Any] = {}

    for digest in values:
        if not isinstance(digest, expected_type):
            raise ValueError(f"{name} must contain {expected_type.__name__} values")

        owner_user_id, source_id = validator(digest)
        owner_user_id = _require_non_empty("owner_user_id", owner_user_id)
        source_id = _require_non_empty("context_source_id", source_id)
        owners.add(owner_user_id)

        existing = seen.get(source_id)
        if existing is not None:
            if existing != digest:
                raise ValueError(
                    "conflicting discovery payloads for one canonical source identity"
                )
            continue

        seen[source_id] = digest
        unique[source_id] = digest

    return tuple(unique[source_id] for source_id in sorted(unique))


class DiscoveryCollection(BaseModel):
    """Pure, immutable CTX-F3B collection derived from canonical F3A digests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_version: str = DISCOVERY_COLLECTION_VERSION
    owner_user_id: str
    sessions: tuple[SessionDigest, ...] = ()
    tasks: tuple[TaskDigest, ...] = ()
    branches: tuple[BranchDigest, ...] = ()

    def __init__(self, **data: Any) -> None:
        token = data.pop("_factory_token", None)
        if token is not _COLLECTION_FACTORY_TOKEN:
            raise ValueError(
                "discovery collections must be created through build_discovery_collection"
            )
        super().__init__(**data)

    @model_validator(mode="after")
    def validate_collection_contract(self) -> "DiscoveryCollection":
        if self.collection_version != DISCOVERY_COLLECTION_VERSION:
            raise ValueError("collection_version must match CTX-F3B code authority")

        canonical_owner = _require_non_empty("owner_user_id", self.owner_user_id)
        if canonical_owner != self.owner_user_id:
            raise ValueError("owner_user_id must already be normalized")

        owners: set[str] = set()
        seen: set[str] = set()

        groups = (
            ("sessions", self.sessions, _validate_session_digest),
            ("tasks", self.tasks, _validate_task_digest),
            ("branches", self.branches, _validate_branch_digest),
        )
        for name, values, validator in groups:
            previous_id: str | None = None
            for digest in values:
                owner, source_id = validator(digest)
                owners.add(owner)
                if source_id in seen:
                    raise ValueError("collection contains duplicate canonical source identity")
                seen.add(source_id)
                if previous_id is not None and source_id <= previous_id:
                    raise ValueError(f"{name} must be deterministically source-id sorted")
                previous_id = source_id

        if not seen:
            raise ValueError("discovery collection requires at least one canonical F3A digest")
        if owners != {self.owner_user_id}:
            raise ValueError("discovery collection must contain exactly one canonical owner")

        return self


def build_discovery_collection(
    *,
    sessions: tuple[tuple[SessionEvidence, SessionDigest], ...]
    | list[tuple[SessionEvidence, SessionDigest]] = (),
    tasks: tuple[tuple[SessionEvidence, TaskEvidence, TaskDigest], ...]
    | list[tuple[SessionEvidence, TaskEvidence, TaskDigest]] = (),
    branches: tuple[
        tuple[SessionEvidence, TaskEvidence, BranchEvidence, BranchDigest], ...
    ]
    | list[tuple[SessionEvidence, TaskEvidence, BranchEvidence, BranchDigest]] = (),
) -> DiscoveryCollection:
    """Rebuild one owner-scoped collection from F3A digests plus canonical evidence."""

    session_entries = _require_finite_sequence("sessions", sessions)
    task_entries = _require_finite_sequence("tasks", tasks)
    branch_entries = _require_finite_sequence("branches", branches)

    if not session_entries and not task_entries and not branch_entries:
        raise ValueError("at least one canonical F3A digest with matching evidence is required")

    session_values = tuple(_canonical_session_from_entry(entry) for entry in session_entries)
    task_values = tuple(_canonical_task_from_entry(entry) for entry in task_entries)
    branch_values = tuple(_canonical_branch_from_entry(entry) for entry in branch_entries)

    seen: dict[str, Any] = {}
    owners: set[str] = set()

    canonical_sessions = _dedupe_and_sort(
        name="sessions",
        values=session_values,
        expected_type=SessionDigest,
        validator=_validate_session_digest,
        seen=seen,
        owners=owners,
    )
    canonical_tasks = _dedupe_and_sort(
        name="tasks",
        values=task_values,
        expected_type=TaskDigest,
        validator=_validate_task_digest,
        seen=seen,
        owners=owners,
    )
    canonical_branches = _dedupe_and_sort(
        name="branches",
        values=branch_values,
        expected_type=BranchDigest,
        validator=_validate_branch_digest,
        seen=seen,
        owners=owners,
    )

    if len(owners) != 1:
        raise ValueError("discovery collection must contain exactly one canonical owner")

    owner_user_id = next(iter(owners))
    return DiscoveryCollection(
        _factory_token=_COLLECTION_FACTORY_TOKEN,
        collection_version=DISCOVERY_COLLECTION_VERSION,
        owner_user_id=owner_user_id,
        sessions=canonical_sessions,
        tasks=canonical_tasks,
        branches=canonical_branches,
    )


__all__ = [
    "DISCOVERY_COLLECTION_VERSION",
    "DiscoveryCollection",
    "build_discovery_collection",
]
