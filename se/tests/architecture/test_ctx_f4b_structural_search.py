from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import se.src.context.search as search
from se.src.context.access import ContextAccessAuthorityError
from se.src.context.discovery import (
    SessionDigest,
    project_branch_digest,
    project_session_digest,
    project_task_digest,
)
from se.src.context.source_identity import ContextSourceKind, create_context_source_ref
from se.src.context.search import ContextSearchResult, search_context_sources


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


def _session_entry(digest, *, session: Session | None = None):
    return session or Session(), digest


def _task_entry(
    digest,
    *,
    session: Session | None = None,
    task: Task | None = None,
):
    return session or Session(), task or Task(), digest


def _branch_entry(
    digest,
    *,
    session: Session | None = None,
    task: Task | None = None,
    branch: Branch | None = None,
):
    return session or Session(), task or Task(), branch or Branch(), digest


def _source_id(digest):
    if hasattr(digest, "branch_source"):
        return digest.branch_source.context_source_id
    if hasattr(digest, "task_source"):
        return digest.task_source.context_source_id
    return digest.session_source.context_source_id


def test_ctx_f4b_query_normalization_is_internal_casefolded_and_result_is_immutable():
    digest = project_session_digest(
        Session(),
        title="Straße Context",
        summary="already loaded",
    )

    result = search_context_sources(
        "  STRASSE  ",
        sessions=[_session_entry(digest)],
    )

    assert result.query == "STRASSE"
    assert result.hits == (digest,)
    with pytest.raises(FrozenInstanceError):
        result.query = "mutated"


@pytest.mark.parametrize("query", [None, 3, (), object()])
def test_ctx_f4b_non_string_query_fails_explicitly(query):
    digest = project_session_digest(Session(), summary="known")

    with pytest.raises(ValueError, match="query must be a string"):
        search_context_sources(query, sessions=[_session_entry(digest)])


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_ctx_f4b_empty_after_trim_query_fails_explicitly(query):
    digest = project_session_digest(Session(), summary="known")

    with pytest.raises(ValueError, match="non-empty"):
        search_context_sources(query, sessions=[_session_entry(digest)])


def test_ctx_f4b_matching_is_field_local_literal_substring_only():
    digest = project_task_digest(
        Session(),
        Task(),
        objective="alpha",
        summary="other",
        keywords=["beta"],
    )

    no_cross_field_match = search_context_sources(
        "alpha beta",
        tasks=[_task_entry(digest)],
    )
    literal_match = search_context_sources(
        "LPH",
        tasks=[_task_entry(digest)],
    )

    assert no_cross_field_match.hits == ()
    assert literal_match.hits == (digest,)


def test_ctx_f4b_searchable_fields_are_closed_and_metadata_is_not_searchable():
    session = Session(id="session-secret", user_id="owner-secret", status="state-secret")
    task = Task(
        id="task-secret",
        session_id=session.id,
        revision=987654,
        status="state-secret",
    )
    digest = project_task_digest(
        session,
        task,
        objective="visible objective",
        summary="visible summary",
        topics=["visible topic"],
        keywords=["visible keyword"],
        important_decisions=["visible decision"],
        remaining_items=["visible remaining"],
    )
    entry = _task_entry(digest, session=session, task=task)

    for metadata_query in (
        "owner-secret",
        "session-secret",
        "task-secret",
        "state-secret",
        "987654",
        digest.task_source.context_source_id,
        ContextSourceKind.TASK.value,
    ):
        assert search_context_sources(metadata_query, tasks=[entry]).hits == ()

    for allowed_query in (
        "objective",
        "summary",
        "topic",
        "keyword",
        "decision",
        "remaining",
    ):
        assert search_context_sources(allowed_query, tasks=[entry]).hits == (digest,)



@pytest.mark.parametrize(
    ("field_name", "query"),
    [
        ("title", "session-title-needle"),
        ("summary", "session-summary-needle"),
        ("topics", "session-topic-needle"),
        ("keywords", "session-keyword-needle"),
    ],
)
def test_ctx_f4b_session_positive_field_freeze(field_name, query):
    kwargs = {
        "title": "session-title-needle",
        "summary": "session-summary-needle",
        "topics": ["session-topic-needle"],
        "keywords": ["session-keyword-needle"],
    }
    digest = project_session_digest(Session(), **kwargs)

    result = search_context_sources(
        query,
        sessions=[_session_entry(digest)],
    )

    assert result.hits == (digest,)


@pytest.mark.parametrize(
    ("field_name", "query"),
    [
        ("summary", "branch-summary-needle"),
        ("topics", "branch-topic-needle"),
        ("keywords", "branch-keyword-needle"),
        ("important_decisions", "branch-decision-needle"),
    ],
)
def test_ctx_f4b_branch_positive_field_freeze(field_name, query):
    branch = Branch()
    digest = project_branch_digest(
        Session(),
        Task(),
        branch,
        summary="branch-summary-needle",
        topics=["branch-topic-needle"],
        keywords=["branch-keyword-needle"],
        important_decisions=["branch-decision-needle"],
    )

    result = search_context_sources(
        query,
        branches=[_branch_entry(digest, branch=branch)],
    )

    assert result.hits == (digest,)


def test_ctx_f4b_multiple_matching_fields_emit_one_authorized_source():
    digest = project_task_digest(
        Session(),
        Task(),
        objective="needle objective",
        summary="needle summary",
        topics=["needle topic"],
        keywords=["needle keyword"],
        important_decisions=["needle decision"],
        remaining_items=["needle remaining"],
    )

    result = search_context_sources("needle", tasks=[_task_entry(digest)])

    assert result.hits == (digest,)
    assert len(result.hits) == 1


def test_ctx_f4b_result_order_is_global_context_source_id_across_kinds_and_input_order():
    session_digest = project_session_digest(
        Session(id="session-a"),
        title="common",
    )
    task = Task(id="task-b", session_id="session-b")
    task_digest = project_task_digest(
        Session(id="session-b"),
        task,
        objective="common",
    )
    branch = Branch(branch_id="branch-c", task_id="task-c")
    branch_task = Task(id="task-c", session_id="session-c")
    branch_digest = project_branch_digest(
        Session(id="session-c"),
        branch_task,
        branch,
        summary="common",
    )

    result = search_context_sources(
        "common",
        sessions=[_session_entry(session_digest, session=Session(id="session-a"))],
        tasks=[_task_entry(
            task_digest,
            session=Session(id="session-b"),
            task=task,
        )],
        branches=[_branch_entry(
            branch_digest,
            session=Session(id="session-c"),
            task=branch_task,
            branch=branch,
        )],
    )

    expected = tuple(
        sorted(
            (session_digest, task_digest, branch_digest),
            key=_source_id,
        )
    )
    assert result.hits == expected


def test_ctx_f4b_valid_owner_scope_with_no_match_returns_immutable_empty_result():
    digest = project_session_digest(Session(), summary="known")

    result = search_context_sources(
        "absent",
        sessions=[_session_entry(digest)],
    )

    assert result == ContextSearchResult(query="absent", hits=())
    assert isinstance(result.hits, tuple)
    with pytest.raises(FrozenInstanceError):
        result.hits = (digest,)


def test_ctx_f4b_zero_evidence_is_authority_error_not_empty_result():
    with pytest.raises(
        ContextAccessAuthorityError,
        match="at least one canonical evidence entry",
    ):
        search_context_sources("known")


def test_ctx_f4b_mixed_owner_evidence_still_fails_closed_through_f3b():
    first_session = Session(id="session-a", user_id="owner-a")
    second_session = Session(id="session-b", user_id="owner-b")
    first = project_session_digest(first_session, summary="needle")
    second = project_session_digest(second_session, summary="needle")

    with pytest.raises(ValueError, match="owner"):
        search_context_sources(
            "needle",
            sessions=[
                _session_entry(first, session=first_session),
                _session_entry(second, session=second_session),
            ],
        )


def test_ctx_f4b_forged_digest_still_fails_closed_through_f3b_reproof():
    forged_source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="forged-session",
        owner_user_id="user-1",
        session_id="forged-session",
        source_state="active",
        source_created_at=NOW,
    )
    forged = SessionDigest.model_construct(
        session_source=forged_source,
        projection_version="ctx-f3a-v1",
        title="needle",
        summary=None,
        topics=(),
        keywords=(),
    )

    with pytest.raises(ValueError, match="provenance"):
        search_context_sources(
            "needle",
            sessions=[(Session(), forged)],
        )


def test_ctx_f4b_historical_branch_state_is_descriptive_not_searchable_or_mutated():
    branch = Branch(resolution_state="SUPERSEDED")
    digest = project_branch_digest(
        Session(),
        Task(),
        branch,
        summary="historical needle",
        important_decisions=["remain historical"],
    )

    match = search_context_sources(
        "needle",
        branches=[_branch_entry(digest, branch=branch)],
    )
    state_only = search_context_sources(
        "SUPERSEDED",
        branches=[_branch_entry(digest, branch=branch)],
    )

    assert match.hits == (digest,)
    assert match.hits[0].branch_source.source_state == "SUPERSEDED"
    assert state_only.hits == ()


def test_ctx_f4b_public_boundary_exposes_no_collection_owner_native_id_or_ranking_controls():
    digest = project_session_digest(Session(), summary="needle")
    entry = _session_entry(digest)

    for forbidden_kwargs in (
        {"collection": object()},
        {"owner_user_id": "user-1"},
        {"task_id": "task-1"},
        {"branch_id": "branch-1"},
        {"top_k": 1},
        {"max_results": 1},
        {"cursor": "next"},
        {"sort": "recent"},
    ):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            search_context_sources(
                "needle",
                sessions=[entry],
                **forbidden_kwargs,
            )


def test_ctx_f4b_module_has_no_backend_asset_runtime_retention_or_advanced_search_surface():
    path = Path(search.__file__)
    source_text = path.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_prefixes = (
        "re",
        "sqlalchemy",
        "alembic",
        "se.src.infrastructure",
        "se.src.runtimes",
        "se.src.assets",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )

    for forbidden_name in (
        "read_context_source",
        "rank",
        "score",
        "search_backend",
        "embedding",
        "vector",
        "top_k",
        "max_results",
        "cursor",
        "paginate",
        "hydrate",
        "retain",
        "delete",
        "collect",
    ):
        assert not hasattr(search, forbidden_name)

    for forbidden_text in (
        "AssetEvidence",
        "project_asset_source",
        "AssetService",
        "ObjectStorage",
        "FileAsset",
        "FileBlob",
        "FileReference",
        "FileProviderBinding",
        "AgentRepository",
    ):
        assert forbidden_text not in source_text
