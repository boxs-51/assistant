from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from se.src.context.source_adapters import (
    BranchEvidence,
    SessionEvidence,
    TaskEvidence,
    project_branch_source,
    project_session_source,
    project_task_source,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    ContextSourceRef,
    validate_context_source_ref_integrity,
)


DISCOVERY_PROJECTION_VERSION = "ctx-f3a-v1"
_DIGEST_FACTORY_TOKEN = object()


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _normalize_optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    return _require_text(name, value)


def _normalize_text_tuple(name: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list or tuple of strings")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _require_text(f"{name}[{index}]", item)
        if text in seen:
            raise ValueError(f"{name} must not contain duplicates")
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _validated_source(
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


def _validate_session_source(ref: ContextSourceRef) -> ContextSourceRef:
    ref = _validated_source(
        ref,
        expected_kind=ContextSourceKind.SESSION,
        label="session_source",
    )
    if ref.session_id != ref.authority_id:
        raise ValueError("session_source.session_id must match authority_id")
    if ref.task_id is not None or ref.branch_id is not None:
        raise ValueError("session_source must not carry task/branch lineage")
    return ref


def _validate_task_source(
    session_source: ContextSourceRef,
    task_source: ContextSourceRef,
) -> ContextSourceRef:
    session_source = _validate_session_source(session_source)
    task_source = _validated_source(
        task_source,
        expected_kind=ContextSourceKind.TASK,
        label="task_source",
    )
    if task_source.owner_user_id != session_source.owner_user_id:
        raise ValueError("task_source owner mismatch")
    if task_source.session_id != session_source.authority_id:
        raise ValueError("task_source session lineage mismatch")
    if task_source.task_id != task_source.authority_id:
        raise ValueError("task_source.task_id must match authority_id")
    if task_source.branch_id is not None:
        raise ValueError("task_source must not carry branch lineage")
    return task_source


def _validate_branch_source(
    session_source: ContextSourceRef,
    task_source: ContextSourceRef,
    branch_source: ContextSourceRef,
) -> ContextSourceRef:
    task_source = _validate_task_source(session_source, task_source)
    branch_source = _validated_source(
        branch_source,
        expected_kind=ContextSourceKind.BRANCH,
        label="branch_source",
    )
    if branch_source.owner_user_id != session_source.owner_user_id:
        raise ValueError("branch_source owner mismatch")
    if branch_source.session_id != session_source.authority_id:
        raise ValueError("branch_source session lineage mismatch")
    if branch_source.task_id != task_source.authority_id:
        raise ValueError("branch_source task lineage mismatch")
    if branch_source.branch_id != branch_source.authority_id:
        raise ValueError("branch_source.branch_id must match authority_id")
    return branch_source


class _DiscoveryDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    projection_version: str = DISCOVERY_PROJECTION_VERSION
    summary: str | None = None
    topics: tuple[str, ...] = Field(default_factory=tuple)
    keywords: tuple[str, ...] = Field(default_factory=tuple)

    def __init__(self, **data: Any) -> None:
        token = data.pop("_factory_token", None)
        if token is not _DIGEST_FACTORY_TOKEN:
            raise ValueError(
                "discovery digests must be created through canonical F3A projection entry points"
            )
        super().__init__(**data)

    @field_validator("projection_version", mode="before")
    @classmethod
    def validate_projection_version(cls, value: Any) -> str:
        return _require_text("projection_version", value)

    @field_validator("summary", mode="before")
    @classmethod
    def validate_summary(cls, value: Any) -> str | None:
        return _normalize_optional_text("summary", value)

    @field_validator("topics", mode="before")
    @classmethod
    def validate_topics(cls, value: Any) -> tuple[str, ...]:
        return _normalize_text_tuple("topics", value)

    @field_validator("keywords", mode="before")
    @classmethod
    def validate_keywords(cls, value: Any) -> tuple[str, ...]:
        return _normalize_text_tuple("keywords", value)


class SessionDigest(_DiscoveryDigest):
    """Dormant, rebuildable discovery view derived from canonical Session evidence."""

    session_source: ContextSourceRef
    title: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value: Any) -> str | None:
        return _normalize_optional_text("title", value)

    @model_validator(mode="after")
    def validate_source_authority(self) -> "SessionDigest":
        _validate_session_source(self.session_source)
        return self


class TaskDigest(_DiscoveryDigest):
    """Dormant, rebuildable discovery view derived from canonical Task evidence."""

    session_source: ContextSourceRef
    task_source: ContextSourceRef
    objective: str | None = None
    important_decisions: tuple[str, ...] = Field(default_factory=tuple)
    remaining_items: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("objective", mode="before")
    @classmethod
    def validate_objective(cls, value: Any) -> str | None:
        return _normalize_optional_text("objective", value)

    @field_validator("important_decisions", mode="before")
    @classmethod
    def validate_important_decisions(cls, value: Any) -> tuple[str, ...]:
        return _normalize_text_tuple("important_decisions", value)

    @field_validator("remaining_items", mode="before")
    @classmethod
    def validate_remaining_items(cls, value: Any) -> tuple[str, ...]:
        return _normalize_text_tuple("remaining_items", value)

    @model_validator(mode="after")
    def validate_source_authority(self) -> "TaskDigest":
        _validate_task_source(self.session_source, self.task_source)
        return self


class BranchDigest(_DiscoveryDigest):
    """Dormant, rebuildable discovery view derived from canonical Branch evidence."""

    session_source: ContextSourceRef
    task_source: ContextSourceRef
    branch_source: ContextSourceRef
    important_decisions: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("important_decisions", mode="before")
    @classmethod
    def validate_important_decisions(cls, value: Any) -> tuple[str, ...]:
        return _normalize_text_tuple("important_decisions", value)

    @model_validator(mode="after")
    def validate_source_authority(self) -> "BranchDigest":
        _validate_branch_source(
            self.session_source,
            self.task_source,
            self.branch_source,
        )
        return self


def project_session_digest(
    session: SessionEvidence,
    *,
    title: str | None = None,
    summary: str | None = None,
    topics: tuple[str, ...] | list[str] = (),
    keywords: tuple[str, ...] | list[str] = (),
    projection_version: str = DISCOVERY_PROJECTION_VERSION,
) -> SessionDigest:
    session_source = project_session_source(session)
    return SessionDigest(
        _factory_token=_DIGEST_FACTORY_TOKEN,
        session_source=session_source,
        title=title,
        summary=summary,
        topics=topics,
        keywords=keywords,
        projection_version=projection_version,
    )


def project_task_digest(
    session: SessionEvidence,
    task: TaskEvidence,
    *,
    objective: str | None = None,
    summary: str | None = None,
    topics: tuple[str, ...] | list[str] = (),
    keywords: tuple[str, ...] | list[str] = (),
    important_decisions: tuple[str, ...] | list[str] = (),
    remaining_items: tuple[str, ...] | list[str] = (),
    projection_version: str = DISCOVERY_PROJECTION_VERSION,
) -> TaskDigest:
    session_source = project_session_source(session)
    task_source = project_task_source(session, task)
    return TaskDigest(
        _factory_token=_DIGEST_FACTORY_TOKEN,
        session_source=session_source,
        task_source=task_source,
        objective=objective,
        summary=summary,
        topics=topics,
        keywords=keywords,
        important_decisions=important_decisions,
        remaining_items=remaining_items,
        projection_version=projection_version,
    )


def project_branch_digest(
    session: SessionEvidence,
    task: TaskEvidence,
    branch: BranchEvidence,
    *,
    summary: str | None = None,
    topics: tuple[str, ...] | list[str] = (),
    keywords: tuple[str, ...] | list[str] = (),
    important_decisions: tuple[str, ...] | list[str] = (),
    projection_version: str = DISCOVERY_PROJECTION_VERSION,
) -> BranchDigest:
    session_source = project_session_source(session)
    task_source = project_task_source(session, task)
    branch_source = project_branch_source(session, task, branch)
    return BranchDigest(
        _factory_token=_DIGEST_FACTORY_TOKEN,
        session_source=session_source,
        task_source=task_source,
        branch_source=branch_source,
        summary=summary,
        topics=topics,
        keywords=keywords,
        important_decisions=important_decisions,
        projection_version=projection_version,
    )


__all__ = [
    "BranchDigest",
    "DISCOVERY_PROJECTION_VERSION",
    "SessionDigest",
    "TaskDigest",
    "project_branch_digest",
    "project_session_digest",
    "project_task_digest",
]
