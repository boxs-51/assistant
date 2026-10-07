from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Self

import pytest

from se.src.context.access import ContextAccessAuthorityError
from se.src.context.manager import ContextEngine
from se.src.domain.schemas.identity import Identity


class _SessionRepository:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, int]] = []

    async def list_by_user_id(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[SimpleNamespace]:
        self.calls.append((user_id, limit))
        return self.rows[:limit]


class _UnitOfWork:
    def __init__(self, sessions: _SessionRepository) -> None:
        self.sessions = sessions
        self.commit_calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def commit(self) -> None:
        self.commit_calls += 1
        raise AssertionError("F4D persisted search must not commit")


def _identity(user_id: str | None = "owner-1") -> Identity:
    return Identity(user_id=user_id, auth_type="jwt")


def _session(
    session_id: str,
    *,
    owner_user_id: str = "owner-1",
    title: str | None = None,
    status: str = "ACTIVE",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        user_id=owner_user_id,
        title=title,
        status=status,
        created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
        metadata_json={},
    )


@pytest.mark.asyncio
async def test_f4d_searches_only_bounded_owner_sessions_by_title() -> None:
    sessions = _SessionRepository(
        [
            _session("session-z", title="Needle planning"),
            _session("session-a", title="unrelated"),
            _session("session-m", title="another NEEDLE"),
        ]
    )
    uow = _UnitOfWork(sessions)
    engine = ContextEngine(object(), lambda: uow)

    result = await engine._search_owner_wide_persisted_sessions(
        "  needle  ",
        _identity(),
        limit=1_000,
    )

    assert sessions.calls == [("owner-1", 100)]
    assert result.query == "needle"
    assert len(result.hits) == 2
    assert [hit.session_source.context_source_id for hit in result.hits] == sorted(
        hit.session_source.context_source_id for hit in result.hits
    )
    assert {
        hit.session_source.owner_user_id for hit in result.hits
    } == {"owner-1"}
    assert uow.commit_calls == 0


@pytest.mark.asyncio
async def test_f4d_title_only_slice_does_not_search_metadata_or_messages() -> None:
    hidden_match = _session("session-hidden", title="ordinary title")
    hidden_match.metadata_json = {
        "summary": "needle",
        "messages": [{"content": "needle"}],
    }
    sessions = _SessionRepository([hidden_match])
    engine = ContextEngine(object(), lambda: _UnitOfWork(sessions))

    result = await engine._search_owner_wide_persisted_sessions(
        "needle",
        _identity(),
    )

    assert result.hits == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [None, "", " owner-1 "])
async def test_f4d_fails_closed_without_normalized_trusted_owner(
    user_id: str | None,
) -> None:
    sessions = _SessionRepository([_session("session-1", title="needle")])
    engine = ContextEngine(object(), lambda: _UnitOfWork(sessions))

    with pytest.raises(
        ValueError,
        match="normalized trusted user identity",
    ):
        await engine._search_owner_wide_persisted_sessions(
            "needle",
            _identity(user_id),
        )

    assert sessions.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_f4d_rejects_invalid_scan_limits(limit: object) -> None:
    sessions = _SessionRepository([_session("session-1", title="needle")])
    engine = ContextEngine(object(), lambda: _UnitOfWork(sessions))

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        await engine._search_owner_wide_persisted_sessions(
            "needle",
            _identity(),
            limit=limit,  # type: ignore[arg-type]
        )

    assert sessions.calls == []


@pytest.mark.asyncio
async def test_f4d_rejects_repository_owner_mismatch() -> None:
    sessions = _SessionRepository(
        [_session("foreign-session", owner_user_id="owner-2", title="needle")]
    )
    engine = ContextEngine(object(), lambda: _UnitOfWork(sessions))

    with pytest.raises(ValueError, match="persisted session owner mismatch"):
        await engine._search_owner_wide_persisted_sessions(
            "needle",
            _identity(),
        )


@pytest.mark.asyncio
async def test_f4d_preserves_f4b_empty_evidence_fail_closed_boundary() -> None:
    sessions = _SessionRepository([])
    engine = ContextEngine(object(), lambda: _UnitOfWork(sessions))

    with pytest.raises(
        ContextAccessAuthorityError,
        match="at least one canonical evidence entry",
    ):
        await engine._search_owner_wide_persisted_sessions(
            "needle",
            _identity(),
        )

    assert sessions.calls == [("owner-1", 100)]
