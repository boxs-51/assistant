from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import se.src.context.discovery_collection as discovery_collection
from se.src.context.discovery import (
    BranchDigest,
    SessionDigest,
    TaskDigest,
    project_branch_digest,
    project_session_digest,
    project_task_digest,
)
from se.src.context.discovery_collection import (
    DISCOVERY_COLLECTION_VERSION,
    DiscoveryCollection,
    build_discovery_collection,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


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


def _forge_digest(digest, **updates):
    values = {
        field_name: getattr(digest, field_name)
        for field_name in type(digest).model_fields
    }
    values.update(updates)
    return type(digest).model_construct(**values)


def _session_entry(digest: SessionDigest):
    source = digest.session_source
    session = Session(
        id=source.authority_id,
        user_id=source.owner_user_id,
        status=source.source_state,
        created_at=source.source_created_at or NOW,
    )
    return session, digest


def _task_entry(digest: TaskDigest):
    session_source = digest.session_source
    task_source = digest.task_source
    session = Session(
        id=session_source.authority_id,
        user_id=session_source.owner_user_id,
        status=session_source.source_state,
        created_at=session_source.source_created_at or NOW,
    )
    task = Task(
        id=task_source.authority_id,
        session_id=task_source.session_id or session.id,
        revision=task_source.authority_version or 0,
        status=task_source.source_state,
        created_at=task_source.source_created_at or NOW,
    )
    return session, task, digest


def _branch_entry(digest: BranchDigest):
    session_source = digest.session_source
    task_source = digest.task_source
    branch_source = digest.branch_source
    session = Session(
        id=session_source.authority_id,
        user_id=session_source.owner_user_id,
        status=session_source.source_state,
        created_at=session_source.source_created_at or NOW,
    )
    task = Task(
        id=task_source.authority_id,
        session_id=task_source.session_id or session.id,
        revision=task_source.authority_version or 0,
        status=task_source.source_state,
        created_at=task_source.source_created_at or NOW,
    )
    branch = Branch(
        branch_id=branch_source.authority_id,
        task_id=branch_source.task_id or task.id,
        revision=branch_source.authority_version or 0,
        resolution_state=branch_source.source_state,
        created_at=branch_source.source_created_at or NOW,
    )
    return session, task, branch, digest


def _entry_for_group(group: str, digest):
    if group == "sessions":
        return _session_entry(digest)
    if group == "tasks":
        return _task_entry(digest)
    if group == "branches":
        return _branch_entry(digest)
    raise AssertionError(f"unexpected group: {group}")


def test_ctx_f3b_builds_single_owner_immutable_collection():
    session_digest = project_session_digest(Session(), summary="Session")
    task_digest = project_task_digest(Session(), Task(), summary="Task")
    branch_digest = project_branch_digest(Session(), Task(), Branch(), summary="Branch")

    collection = build_discovery_collection(
        sessions=[_session_entry(session_digest)],
        tasks=[_task_entry(task_digest)],
        branches=[_branch_entry(branch_digest)],
    )

    assert isinstance(collection, DiscoveryCollection)
    assert collection.collection_version == DISCOVERY_COLLECTION_VERSION
    assert collection.owner_user_id == "user-1"
    assert collection.sessions == (session_digest,)
    assert collection.tasks == (task_digest,)
    assert collection.branches == (branch_digest,)

    with pytest.raises(ValidationError):
        collection.owner_user_id = "other-user"


def test_ctx_f3b_order_is_deterministic_for_equivalent_finite_input():
    first = project_task_digest(
        Session(),
        Task(id="task-a", revision=1),
        summary="A",
    )
    second = project_task_digest(
        Session(),
        Task(id="task-b", revision=1),
        summary="B",
    )

    forward = build_discovery_collection(tasks=[_task_entry(first), _task_entry(second)])
    reverse = build_discovery_collection(tasks=[_task_entry(second), _task_entry(first)])

    assert forward == reverse
    assert tuple(
        digest.task_source.context_source_id for digest in forward.tasks
    ) == tuple(sorted(
        digest.task_source.context_source_id for digest in forward.tasks
    ))


def test_ctx_f3b_mixed_owner_input_fails_closed():
    owner_one = project_session_digest(Session(id="session-1", user_id="user-1"))
    owner_two = project_session_digest(Session(id="session-2", user_id="user-2"))

    with pytest.raises(ValueError, match="exactly one canonical owner"):
        build_discovery_collection(
            sessions=[_session_entry(owner_one), _session_entry(owner_two)]
        )


def test_ctx_f3b_equivalent_duplicate_source_collapses_deterministically():
    first = project_task_digest(Session(), Task(), summary="same")
    equivalent = project_task_digest(Session(), Task(), summary="same")

    collection = build_discovery_collection(
        tasks=[_task_entry(first), _task_entry(equivalent)]
    )

    assert collection.tasks == (first,)


def test_ctx_f3b_conflicting_payload_for_same_source_identity_fails_closed():
    first = project_task_digest(Session(), Task(), summary="first")
    conflicting = project_task_digest(Session(), Task(), summary="different")

    assert first.task_source.context_source_id == conflicting.task_source.context_source_id

    with pytest.raises(
        ValueError,
        match="conflicting discovery payloads for one canonical source identity",
    ):
        build_discovery_collection(
            tasks=[_task_entry(first), _task_entry(conflicting)]
        )


def test_ctx_f3b_native_revision_change_remains_distinct_source_identity():
    revision_three = project_task_digest(
        Session(),
        Task(revision=3),
        summary="rev3",
    )
    revision_four = project_task_digest(
        Session(),
        Task(revision=4),
        summary="rev4",
    )

    collection = build_discovery_collection(
        tasks=[_task_entry(revision_four), _task_entry(revision_three)]
    )

    assert len(collection.tasks) == 2
    assert {
        digest.task_source.authority_version for digest in collection.tasks
    } == {3, 4}
    assert len({
        digest.task_source.context_source_id for digest in collection.tasks
    }) == 2


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_ctx_f3b_terminal_task_remains_historical_when_present_in_input(state):
    task_digest = project_task_digest(
        Session(),
        Task(status=state),
        summary="Historical task",
    )

    collection = build_discovery_collection(tasks=[_task_entry(task_digest)])

    assert collection.tasks[0].task_source.source_state == state


@pytest.mark.parametrize("state", ["ADOPTED", "SUPERSEDED", "DISCARDED", "CANCELLED"])
def test_ctx_f3b_resolved_branch_remains_historical_when_present_in_input(state):
    branch_digest = project_branch_digest(
        Session(),
        Task(),
        Branch(resolution_state=state),
        summary="Historical branch",
    )

    collection = build_discovery_collection(branches=[_branch_entry(branch_digest)])

    assert collection.branches[0].branch_source.source_state == state


def test_ctx_f3b_rejects_raw_source_ref_instead_of_upgrading_integrity_to_authority():
    raw_task = create_context_source_ref(
        source_kind=ContextSourceKind.TASK,
        authority_id="task-raw",
        authority_version=1,
        owner_user_id="user-1",
        session_id="session-1",
        task_id="task-raw",
        source_state="WAITING",
    )

    with pytest.raises(ValueError, match="canonical evidence alongside"):
        build_discovery_collection(tasks=[raw_task])


def test_ctx_f3b_revalidates_digest_lineage_even_if_model_construct_bypasses_f3a_init():
    session_one = project_session_digest(Session(id="session-1"))
    task_other_session = project_task_digest(
        Session(id="session-2"),
        Task(id="task-2", session_id="session-2"),
    )

    forged = TaskDigest.model_construct(
        session_source=session_one.session_source,
        task_source=task_other_session.task_source,
        projection_version=task_other_session.projection_version,
        summary=task_other_session.summary,
        topics=task_other_session.topics,
        keywords=task_other_session.keywords,
        objective=task_other_session.objective,
        important_decisions=task_other_session.important_decisions,
        remaining_items=task_other_session.remaining_items,
    )

    with pytest.raises(ValueError, match="session lineage mismatch"):
        build_discovery_collection(tasks=[_task_entry(forged)])


def test_ctx_f3b_accepts_fully_populated_canonical_f3a_projector_outputs():
    session_digest = project_session_digest(
        Session(),
        title="Session title",
        summary="Session summary",
        topics=["session-topic"],
        keywords=["session-keyword"],
    )
    task_digest = project_task_digest(
        Session(),
        Task(),
        objective="Task objective",
        summary="Task summary",
        topics=["task-topic"],
        keywords=["task-keyword"],
        important_decisions=["Keep canonical authority"],
        remaining_items=["Finish audit"],
    )
    branch_digest = project_branch_digest(
        Session(),
        Task(),
        Branch(),
        summary="Branch summary",
        topics=["branch-topic"],
        keywords=["branch-keyword"],
        important_decisions=["Keep branch immutable"],
    )

    collection = build_discovery_collection(
        sessions=[_session_entry(session_digest)],
        tasks=[_task_entry(task_digest)],
        branches=[_branch_entry(branch_digest)],
    )

    assert collection.sessions == (session_digest,)
    assert collection.tasks == (task_digest,)
    assert collection.branches == (branch_digest,)


def test_ctx_f3b_rejects_noncanonical_f3a_projection_version():
    digest = project_session_digest(Session())
    forged = _forge_digest(digest, projection_version="ctx-f3a-future")

    with pytest.raises(ValueError, match="projection_version"):
        build_discovery_collection(sessions=[_session_entry(forged)])


@pytest.mark.parametrize(
    ("group", "digest", "updates"),
    [
        (
            "sessions",
            project_session_digest(
                Session(),
                title="Title",
                summary="Summary",
                topics=["topic"],
                keywords=["keyword"],
            ),
            {"summary": " padded "},
        ),
        (
            "sessions",
            project_session_digest(Session(), title="Title"),
            {"title": " padded "},
        ),
        (
            "sessions",
            project_session_digest(Session(), topics=["topic"]),
            {"topics": ("topic", "topic")},
        ),
        (
            "sessions",
            project_session_digest(Session(), keywords=["keyword"]),
            {"keywords": (" keyword ",)},
        ),
        (
            "tasks",
            project_task_digest(Session(), Task(), objective="Objective"),
            {"objective": " padded "},
        ),
        (
            "tasks",
            project_task_digest(
                Session(),
                Task(),
                important_decisions=["decision"],
            ),
            {"important_decisions": ("decision", "decision")},
        ),
        (
            "tasks",
            project_task_digest(
                Session(),
                Task(),
                remaining_items=["remaining"],
            ),
            {"remaining_items": (" remaining ",)},
        ),
        (
            "branches",
            project_branch_digest(
                Session(),
                Task(),
                Branch(),
                important_decisions=["decision"],
            ),
            {"important_decisions": ("decision", "decision")},
        ),
    ],
)
def test_ctx_f3b_rejects_model_construct_digest_with_invalid_derived_payload(
    group,
    digest,
    updates,
):
    forged = _forge_digest(digest, **updates)

    with pytest.raises(ValueError):
        build_discovery_collection(**{group: [_entry_for_group(group, forged)]})


@pytest.mark.parametrize(
    ("group", "digest_type", "digest"),
    [
        ("sessions", SessionDigest, project_session_digest(Session())),
        ("tasks", TaskDigest, project_task_digest(Session(), Task())),
        ("branches", BranchDigest, project_branch_digest(Session(), Task(), Branch())),
    ],
)
def test_ctx_f3b_model_construct_regression_covers_every_digest_type(
    group,
    digest_type,
    digest,
):
    assert isinstance(digest, digest_type)
    forged = _forge_digest(digest, summary=" invalid ")

    with pytest.raises(ValueError):
        build_discovery_collection(**{group: [_entry_for_group(group, forged)]})


def test_ctx_f3b_rejects_forged_session_digest_without_matching_canonical_evidence():
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
        title="Forged title",
        summary="Forged summary",
        topics=("topic",),
        keywords=("keyword",),
    )

    with pytest.raises(ValueError, match="session digest provenance"):
        build_discovery_collection(sessions=[(Session(), forged)])


def test_ctx_f3b_rejects_forged_task_digest_without_matching_canonical_evidence():
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
        summary="Forged summary",
        topics=("topic",),
        keywords=("keyword",),
        objective="Forged objective",
        important_decisions=("decision",),
        remaining_items=("remaining",),
    )

    with pytest.raises(ValueError, match="task digest provenance"):
        build_discovery_collection(tasks=[(Session(), Task(), forged)])


def test_ctx_f3b_rejects_forged_branch_digest_without_matching_canonical_evidence():
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
    forged_branch = create_context_source_ref(
        source_kind=ContextSourceKind.BRANCH,
        authority_id="forged-branch",
        authority_version=4,
        owner_user_id="forged-user",
        session_id="forged-session",
        task_id="forged-task",
        branch_id="forged-branch",
        source_state="OPEN",
        source_created_at=NOW,
    )
    forged = BranchDigest.model_construct(
        session_source=forged_session,
        task_source=forged_task,
        branch_source=forged_branch,
        projection_version="ctx-f3a-v1",
        summary="Forged summary",
        topics=("topic",),
        keywords=("keyword",),
        important_decisions=("decision",),
    )

    with pytest.raises(ValueError, match="branch digest provenance"):
        build_discovery_collection(
            branches=[(Session(), Task(), Branch(), forged)]
        )


def test_ctx_f3b_requires_evidence_alongside_digest_not_digest_object_alone():
    digest = project_session_digest(Session())

    with pytest.raises(ValueError, match="canonical evidence alongside"):
        build_discovery_collection(sessions=[digest])


def test_ctx_f3b_empty_collection_is_not_a_caller_created_owner_partition():
    with pytest.raises(ValueError, match="at least one canonical F3A digest with matching evidence"):
        build_discovery_collection()


def test_ctx_f3b_direct_collection_construction_is_closed():
    digest = project_session_digest(Session())

    with pytest.raises(
        ValueError,
        match="must be created through build_discovery_collection",
    ):
        DiscoveryCollection(
            owner_user_id="user-1",
            sessions=(digest,),
        )


def test_ctx_f3b_has_no_incremental_mutation_query_or_retention_surface():
    forbidden = (
        "append",
        "update",
        "delete",
        "resolve",
        "search",
        "describe",
        "read",
        "rank",
        "ttl",
        "retain",
        "collect",
    )
    for name in forbidden:
        assert not hasattr(discovery_collection, name)
        assert not hasattr(DiscoveryCollection, name)

    forbidden_fields = {
        "ttl",
        "expires_at",
        "cursor",
        "watermark",
        "retention",
        "gc_root",
    }
    assert forbidden_fields.isdisjoint(DiscoveryCollection.model_fields)


def test_ctx_f3b_module_keeps_storage_runtime_and_physical_index_out_of_scope():
    path = Path(discovery_collection.__file__)
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
    )
    assert not any(
        module.startswith(forbidden_prefixes)
        for module in imported_modules
    )
