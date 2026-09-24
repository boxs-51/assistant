from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import se.src.context.access as access
from se.src.context.access import (
    ContextAccessAuthorityError,
    ContextAccessStatus,
    describe_context_source,
    resolve_context_source,
)
from se.src.context.discovery import (
    SessionDigest,
    TaskDigest,
    project_branch_digest,
    project_session_digest,
    project_task_digest,
)
from se.src.context.discovery_collection import (
    DiscoveryCollection,
    build_discovery_collection,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)


NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Session:
    id: str = "session-1"
    user_id: str | None = "user-1"
    status: str = "active"
    created_at: datetime = NOW


@dataclass(frozen=True)
class Task:
    id: str = "task-1"
    session_id: str = "session-1"
    revision: int = 3
    status: str = "WAITING"
    created_at: datetime = NOW
    created_by: str = "agent-creator"


@dataclass(frozen=True)
class Branch:
    branch_id: str = "branch-1"
    task_id: str = "task-1"
    revision: int = 2
    resolution_state: str = "OPEN"
    created_at: datetime = NOW
    created_by: str = "agent-creator"


def _session_entry(digest):
    return Session(), digest


def _task_entry(digest, *, task: Task | None = None):
    return Session(), task or Task(), digest


def _branch_entry(digest, *, branch: Branch | None = None):
    return Session(), Task(), branch or Branch(), digest


def test_ctx_f4a_resolve_exact_context_source_id_reuses_f3b_authority():
    digest = project_task_digest(
        Session(),
        Task(),
        objective="Ship bounded structural access",
        summary="F4A exact resolve.",
        topics=["context"],
        keywords=["ctx-f4a"],
    )

    result = resolve_context_source(
        digest.task_source.context_source_id,
        tasks=[_task_entry(digest)],
    )

    assert result.status is ContextAccessStatus.FOUND
    assert result.context_source_id == digest.task_source.context_source_id
    assert result.digest == digest


def test_ctx_f4a_task_revisions_require_distinct_exact_source_ids():
    revision_three = project_task_digest(
        Session(),
        Task(revision=3),
        summary="revision three",
    )
    revision_four = project_task_digest(
        Session(),
        Task(revision=4),
        summary="revision four",
    )
    entries = [
        _task_entry(revision_three, task=Task(revision=3)),
        _task_entry(revision_four, task=Task(revision=4)),
    ]

    first = resolve_context_source(
        revision_three.task_source.context_source_id,
        tasks=entries,
    )
    second = resolve_context_source(
        revision_four.task_source.context_source_id,
        tasks=entries,
    )

    assert first.status is ContextAccessStatus.FOUND
    assert second.status is ContextAccessStatus.FOUND
    assert first.digest == revision_three
    assert second.digest == revision_four
    assert first.context_source_id != second.context_source_id


def test_ctx_f4a_zero_evidence_is_explicit_authority_error():
    selector = "0" * 64

    for operation in (resolve_context_source, describe_context_source):
        with pytest.raises(
            ContextAccessAuthorityError,
            match="at least one canonical evidence entry",
        ):
            operation(selector)


def test_ctx_f4a_missing_exact_source_is_deterministic_not_found():
    digest = project_session_digest(Session(), summary="known")
    missing = "0" * 64
    assert missing != digest.session_source.context_source_id

    result = resolve_context_source(
        missing,
        sessions=[_session_entry(digest)],
    )
    description = describe_context_source(
        missing,
        sessions=[_session_entry(digest)],
    )

    assert result.status is ContextAccessStatus.NOT_FOUND
    assert result.digest is None
    assert result.context_source_id == missing
    assert description.status is ContextAccessStatus.NOT_FOUND
    assert description.description is None
    assert description.context_source_id == missing


@pytest.mark.parametrize(
    "selector",
    [
        "",
        " padded ",
        "A" * 64,
        "g" * 64,
        "abc",
    ],
)
def test_ctx_f4a_selector_must_be_exact_canonical_context_source_id(selector):
    digest = project_session_digest(Session())

    with pytest.raises(ValueError, match="context_source_id"):
        resolve_context_source(
            selector,
            sessions=[_session_entry(digest)],
        )


def test_ctx_f4a_describe_copies_only_existing_task_structural_metadata():
    digest = project_task_digest(
        Session(),
        Task(),
        objective="Keep exact authority",
        summary="Structural metadata only",
        topics=["context", "access"],
        keywords=["ctx-f4a"],
        important_decisions=["No storage reads"],
        remaining_items=["Independent audit"],
    )

    result = describe_context_source(
        digest.task_source.context_source_id,
        tasks=[_task_entry(digest)],
    )

    assert result.status is ContextAccessStatus.FOUND
    description = result.description
    assert description is not None
    assert description.context_source_id == digest.task_source.context_source_id
    assert description.source_kind is ContextSourceKind.TASK
    assert description.owner_user_id == digest.task_source.owner_user_id
    assert description.authority_id == digest.task_source.authority_id
    assert description.authority_version == digest.task_source.authority_version
    assert description.session_id == digest.task_source.session_id
    assert description.task_id == digest.task_source.task_id
    assert description.branch_id is None
    assert description.source_state == digest.task_source.source_state
    assert description.projection_version == digest.projection_version
    assert description.summary == digest.summary
    assert description.topics == digest.topics
    assert description.keywords == digest.keywords
    assert description.objective == digest.objective
    assert description.important_decisions == digest.important_decisions
    assert description.remaining_items == digest.remaining_items
    assert description.title is None


def test_ctx_f4a_describe_does_not_reinterpret_branch_lifecycle_state():
    branch = Branch(resolution_state="SUPERSEDED")
    digest = project_branch_digest(
        Session(),
        Task(),
        branch,
        summary="Historical branch",
        important_decisions=["Remain historical only"],
    )

    result = describe_context_source(
        digest.branch_source.context_source_id,
        branches=[_branch_entry(digest, branch=branch)],
    )

    assert result.status is ContextAccessStatus.FOUND
    assert result.description is not None
    assert result.description.source_kind is ContextSourceKind.BRANCH
    assert result.description.source_state == "SUPERSEDED"
    assert result.description.summary == "Historical branch"


def test_ctx_f4a_reproof_rejects_forged_digest_with_self_consistent_raw_source():
    forged_source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="forged-session",
        owner_user_id="forged-user",
        session_id="forged-session",
        source_state="active",
        source_created_at=NOW,
    )
    forged = SessionDigest.model_construct(
        session_source=forged_source,
        projection_version="ctx-f3a-v1",
        title="Forged",
        summary="Canonical-shaped but unauthorized",
        topics=("context",),
        keywords=("forged",),
    )

    with pytest.raises(ValueError, match="session digest provenance"):
        resolve_context_source(
            forged_source.context_source_id,
            sessions=[(Session(), forged)],
        )


def test_ctx_f4a_reproof_rejects_forged_task_digest_with_valid_shape_and_lineage():
    forged_session = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="forged-session",
        owner_user_id="forged-user",
        session_id="forged-session",
        source_state="active",
        source_created_at=NOW,
    )
    forged_task = create_context_source_ref(
        source_kind=ContextSourceKind.TASK,
        authority_id="forged-task",
        authority_version=7,
        owner_user_id="forged-user",
        session_id="forged-session",
        task_id="forged-task",
        source_state="WAITING",
        source_created_at=NOW,
    )
    forged = TaskDigest.model_construct(
        session_source=forged_session,
        task_source=forged_task,
        projection_version="ctx-f3a-v1",
        summary="Forged",
        topics=("context",),
        keywords=("forged",),
        objective="Unauthorized",
        important_decisions=("Keep shape valid",),
        remaining_items=("Still unauthorized",),
    )

    with pytest.raises(ValueError, match="task digest provenance"):
        resolve_context_source(
            forged_task.context_source_id,
            tasks=[(Session(), Task(), forged)],
        )


def test_ctx_f4a_public_boundary_does_not_accept_discovery_collection_authority():
    digest = project_session_digest(Session())
    collection = build_discovery_collection(sessions=[_session_entry(digest)])

    with pytest.raises(TypeError, match="unexpected keyword argument 'collection'"):
        resolve_context_source(
            digest.session_source.context_source_id,
            collection=collection,
        )

    forged_collection = DiscoveryCollection.model_construct(
        collection_version=collection.collection_version,
        owner_user_id=collection.owner_user_id,
        sessions=collection.sessions,
        tasks=collection.tasks,
        branches=collection.branches,
    )
    with pytest.raises(TypeError, match="unexpected keyword argument 'collection'"):
        describe_context_source(
            digest.session_source.context_source_id,
            collection=forged_collection,
        )


def test_ctx_f4a_results_are_immutable_structural_values():
    digest = project_session_digest(Session(), title="Frozen")
    result = describe_context_source(
        digest.session_source.context_source_id,
        sessions=[_session_entry(digest)],
    )

    with pytest.raises(FrozenInstanceError):
        result.context_source_id = "0" * 64

    assert result.description is not None
    with pytest.raises(FrozenInstanceError):
        result.description.summary = "mutated"


def test_ctx_f4a_module_has_no_search_read_storage_runtime_or_retention_surface():
    for name in (
        "search",
        "read",
        "context_search",
        "context_read",
        "load",
        "hydrate",
        "rank",
        "retain",
        "collect",
    ):
        assert not hasattr(access, name)

    path = Path(access.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_prefixes = (
        "sqlalchemy",
        "alembic",
        "se.src.infrastructure",
        "se.src.runtimes",
        "se.src.assets",
    )
    assert not any(
        module.startswith(forbidden_prefixes)
        for module in imported_modules
    )


def test_ctx_f4a_has_no_native_id_or_owner_override_selector_parameters():
    digest = project_task_digest(Session(), Task())

    with pytest.raises(TypeError, match="unexpected keyword argument 'task_id'"):
        resolve_context_source(
            digest.task_source.context_source_id,
            tasks=[_task_entry(digest)],
            task_id="task-1",
        )

    with pytest.raises(TypeError, match="unexpected keyword argument 'owner_user_id'"):
        describe_context_source(
            digest.task_source.context_source_id,
            tasks=[_task_entry(digest)],
            owner_user_id="user-1",
        )
