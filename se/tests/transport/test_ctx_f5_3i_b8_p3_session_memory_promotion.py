from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from se.src.context.memory import create_memory_record
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.domain.schemas.identity import Identity
from se.src.transport.gateway.api.v1.session_router import (
    promote_tool_response_payload_memory_for_session,
)


class _Sessions:
    def __init__(self, session):
        self._session = session

    async def get_by_id(self, session_id: str):
        if self._session is None or self._session.id != session_id:
            return None
        return self._session

    async def get_messages_by_session_id(self, session_id: str):
        return []


class _Uow:
    def __init__(self, session):
        self.sessions = _Sessions(session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Storage:
    def __init__(self, *, result=None, error: BaseException | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def promote_tool_response_payload_memory(
        self,
        *,
        source_ref,
        owner_user_id: str,
    ):
        self.calls.append(
            {
                "source_ref": source_ref,
                "owner_user_id": owner_user_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class _Container:
    def __init__(self, *, session, storage: _Storage):
        self._session = session
        self.storage = storage
        self.uow_calls = 0

    def uow_factory(self):
        self.uow_calls += 1
        return _Uow(self._session)


def _identity(user_id: str | None = "user-a") -> Identity:
    return Identity(user_id=user_id, auth_type="guest")


def _tool_source(
    *,
    session_id: str = "session-a",
    owner_user_id: str = "user-a",
):
    return create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id="payload-a",
        owner_user_id=owner_user_id,
        session_id=session_id,
        metadata={"source_result_id": "result-a"},
    )


def _session_source(*, session_id: str = "session-a"):
    return create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="session-source-a",
        owner_user_id="user-a",
        session_id=session_id,
    )


def _owned_session(owner_user_id: str = "user-a"):
    return SimpleNamespace(id="session-a", user_id=owner_user_id)


@pytest.mark.asyncio
async def test_p3_happy_path_awaits_one_promotion_with_authenticated_principal():
    source_ref = _tool_source()
    canonical = create_memory_record(
        source_ref=source_ref,
        promotion_authority_id="promotion-a",
        content={"remember": "value"},
    )
    storage = _Storage(result=canonical)
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    actual = await promote_tool_response_payload_memory_for_session(
        "session-a",
        source_ref,
        _identity(),
        container,
    )

    assert actual is canonical
    assert storage.calls == [
        {
            "source_ref": source_ref,
            "owner_user_id": "user-a",
        }
    ]
    assert container.uow_calls == 1


@pytest.mark.asyncio
async def test_p3_missing_authenticated_user_fails_403_before_session_lookup():
    source_ref = _tool_source()
    storage = _Storage()
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    with pytest.raises(HTTPException) as caught:
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            source_ref,
            _identity(None),
            container,
        )

    assert caught.value.status_code == 403
    assert container.uow_calls == 0
    assert storage.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "expected_status"),
    [
        (None, 404),
        (_owned_session("user-b"), 403),
    ],
)
async def test_p3_missing_or_foreign_session_fails_before_promotion(
    session,
    expected_status: int,
):
    source_ref = _tool_source()
    storage = _Storage()
    container = _Container(session=session, storage=storage)

    with pytest.raises(HTTPException) as caught:
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            source_ref,
            _identity(),
            container,
        )

    assert caught.value.status_code == expected_status
    assert storage.calls == []


@pytest.mark.asyncio
async def test_p3_wrong_source_kind_fails_422_without_promotion():
    storage = _Storage()
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    with pytest.raises(HTTPException) as caught:
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            _session_source(),
            _identity(),
            container,
        )

    assert caught.value.status_code == 422
    assert storage.calls == []


@pytest.mark.asyncio
async def test_p3_source_session_mismatch_fails_409_without_promotion():
    storage = _Storage()
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    with pytest.raises(HTTPException) as caught:
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            _tool_source(session_id="session-b"),
            _identity(),
            container,
        )

    assert caught.value.status_code == 409
    assert storage.calls == []


@pytest.mark.asyncio
async def test_p3_lower_layer_failure_propagates_without_normalization():
    source_ref = _tool_source()
    storage = _Storage(error=RuntimeError("promotion failed"))
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    with pytest.raises(RuntimeError, match="promotion failed"):
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            source_ref,
            _identity(),
            container,
        )

    assert storage.calls == [
        {
            "source_ref": source_ref,
            "owner_user_id": "user-a",
        }
    ]


@pytest.mark.asyncio
async def test_p3_cancellation_propagates_without_retry():
    source_ref = _tool_source()
    storage = _Storage(error=asyncio.CancelledError())
    container = _Container(
        session=_owned_session(),
        storage=storage,
    )

    with pytest.raises(asyncio.CancelledError):
        await promote_tool_response_payload_memory_for_session(
            "session-a",
            source_ref,
            _identity(),
            container,
        )

    assert storage.calls == [
        {
            "source_ref": source_ref,
            "owner_user_id": "user-a",
        }
    ]
