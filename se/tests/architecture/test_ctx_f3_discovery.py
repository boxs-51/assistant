from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import se.src.context.discovery as discovery
from se.src.context.discovery import (
    BranchDigest,
    SessionDigest,
    TaskDigest,
    project_branch_digest,
    project_session_digest,
    project_task_digest,
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


def _forged_session_ref(*, owner: str = "attacker"):
    return create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-forged",
        owner_user_id=owner,
        session_id="session-forged",
        source_state="active",
    )


def _forged_task_ref(*, owner: str = "attacker"):
    return create_context_source_ref(
        source_kind=ContextSourceKind.TASK,
        authority_id="task-forged",
        authority_version=9,
        owner_user_id=owner,
        session_id="session-forged",
        task_id="task-forged",
        source_state="RUNNING",
    )


def _forged_branch_ref(*, owner: str = "attacker"):
    return create_context_source_ref(
        source_kind=ContextSourceKind.BRANCH,
        authority_id="branch-forged",
        authority_version=4,
        owner_user_id=owner,
        session_id="session-forged",
        task_id="task-forged",
        branch_id="branch-forged",
        source_state="OPEN",
    )


def test_ctx_f3a_session_digest_derives_source_from_canonical_f2b_evidence():
    digest = project_session_digest(
        Session(),
        title="Architecture work",
        summary="Current context program.",
        topics=["context", "architecture"],
        keywords=["ctx-f3a"],
    )

    assert isinstance(digest, SessionDigest)
    assert digest.session_source.owner_user_id == "user-1"
    assert digest.session_source.authority_id == "session-1"
    assert digest.topics == ("context", "architecture")
    assert digest.keywords == ("ctx-f3a",)


def test_ctx_f3a_task_digest_derives_authority_from_session_and_task_evidence():
    digest = project_task_digest(
        Session(),
        Task(),
        objective="Prepare discovery projection",
    )

    assert isinstance(digest, TaskDigest)
    assert digest.session_source.owner_user_id == "user-1"
    assert digest.task_source.owner_user_id == "user-1"
    assert digest.task_source.task_id == "task-1"
    assert digest.task_source.authority_version == 3
    assert digest.task_source.source_state == "WAITING"


def test_ctx_f3a_task_projection_rejects_canonical_evidence_lineage_mismatch():
    with pytest.raises(ValueError, match="task.session_id mismatch"):
        project_task_digest(Session(), Task(session_id="other-session"))


def test_ctx_f3a_branch_digest_derives_full_canonical_lineage():
    digest = project_branch_digest(Session(), Task(), Branch())

    assert isinstance(digest, BranchDigest)
    assert digest.session_source.owner_user_id == "user-1"
    assert digest.task_source.task_id == "task-1"
    assert digest.branch_source.task_id == "task-1"
    assert digest.branch_source.branch_id == "branch-1"


def test_ctx_f3a_branch_projection_rejects_canonical_evidence_lineage_mismatch():
    with pytest.raises(ValueError, match="branch.task_id mismatch"):
        project_branch_digest(Session(), Task(), Branch(task_id="other-task"))

    with pytest.raises(ValueError, match="task.session_id mismatch"):
        project_branch_digest(
            Session(),
            Task(session_id="other-session"),
            Branch(),
        )


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_ctx_f3a_terminal_tasks_remain_discoverable_metadata_only(state):
    digest = project_task_digest(
        Session(),
        Task(status=state),
        summary="Historical task evidence.",
    )

    assert digest.task_source.source_state == state


@pytest.mark.parametrize("state", ["ADOPTED", "SUPERSEDED", "DISCARDED", "CANCELLED"])
def test_ctx_f3a_resolved_branches_remain_discoverable_metadata_only(state):
    digest = project_branch_digest(
        Session(),
        Task(),
        Branch(resolution_state=state),
        summary="Historical branch evidence.",
    )

    assert digest.branch_source.source_state == state


def test_ctx_f3a_derived_text_changes_never_rewrite_native_source_identity():
    first = project_task_digest(
        Session(),
        Task(),
        summary="First projection.",
        topics=["one"],
        keywords=["alpha"],
    )
    second = project_task_digest(
        Session(),
        Task(),
        summary="Second projection.",
        topics=["two"],
        keywords=["beta"],
    )

    assert first.task_source.context_source_id == second.task_source.context_source_id
    assert first.task_source.authority_id == second.task_source.authority_id
    assert first.task_source.authority_version == second.task_source.authority_version


def test_ctx_f3a_native_revision_change_produces_new_source_view():
    old_digest = project_task_digest(Session(), Task(revision=3), summary="Old")
    new_digest = project_task_digest(Session(), Task(revision=4), summary="New")

    assert old_digest.task_source.context_source_id != new_digest.task_source.context_source_id
    assert old_digest.task_source.authority_version == 3
    assert new_digest.task_source.authority_version == 4


def test_ctx_f3a_rejects_forged_self_consistent_raw_refs_as_authorization():
    forged_session = _forged_session_ref()
    forged_task = _forged_task_ref()

    with pytest.raises(
        ValueError,
        match="discovery digests must be created through canonical F3A projection entry points",
    ):
        TaskDigest(
            session_source=forged_session,
            task_source=forged_task,
        )

    with pytest.raises(ValueError, match="session.id is required"):
        project_task_digest(forged_session, forged_task)


def test_ctx_f3a_rejects_forged_self_consistent_raw_branch_ref_as_authorization():
    forged_session = _forged_session_ref()
    forged_task = _forged_task_ref()
    forged_branch = _forged_branch_ref()

    with pytest.raises(
        ValueError,
        match="discovery digests must be created through canonical F3A projection entry points",
    ):
        BranchDigest(
            session_source=forged_session,
            task_source=forged_task,
            branch_source=forged_branch,
        )

    with pytest.raises(ValueError, match="session.id is required"):
        project_branch_digest(forged_session, forged_task, forged_branch)


def test_ctx_f3a_canonical_f2b_evidence_still_projects_successfully():
    digest = project_task_digest(
        Session(user_id="canonical-user"),
        Task(),
        summary="Authorized.",
    )

    assert digest.session_source.owner_user_id == "canonical-user"
    assert digest.task_source.owner_user_id == "canonical-user"


def test_ctx_f3a_caller_cannot_supply_loose_owner_override():
    with pytest.raises(TypeError, match="unexpected keyword argument 'owner_user_id'"):
        project_task_digest(
            Session(),
            Task(),
            owner_user_id="other-user",
        )


def test_ctx_f3a_missing_canonical_session_owner_fails_closed():
    with pytest.raises(ValueError, match="session.user_id"):
        project_session_digest(Session(user_id=None))


def test_ctx_f3a_digest_contracts_are_frozen():
    digest = project_session_digest(Session(), summary="Frozen")

    with pytest.raises(ValidationError):
        digest.summary = "mutated"


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"topics": ["same", "same"]}, "must not contain duplicates"),
        ({"keywords": [""]}, "must be non-empty"),
        ({"projection_version": " "}, "projection_version must be non-empty"),
    ],
)
def test_ctx_f3a_derived_metadata_fails_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        project_session_digest(Session(), **kwargs)


def test_ctx_f3a_module_keeps_storage_runtime_and_f4_api_out_of_scope():
    path = Path(discovery.__file__)
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

    for name in (
        "resolve",
        "search",
        "describe",
        "read",
        "project_asset_source",
    ):
        assert not hasattr(discovery, name)
