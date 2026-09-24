from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.messages import (
    CanonicalMessageService,
    MessageAccessDeniedError,
    MessageAssetStateError,
)
from se.src.context.manager import ContextEngine
from se.src.domain.schemas.attachment import GatewayAttachment, DocumentContent
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.message import (
    MessageContentPart,
    contains_canonical_asset_content,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository
from se.src.infrastructure.storage.repositories.chat_data.sessions import (
    SessionRepository,
    SessionAssetReferenceRetentionError,
)
from se.src.runtimes.workflow.runtime import WorkflowRuntime
from se.src.transport.gateway.api.v1.session_router import (
    delete_session,
    edit_session_message,
    regenerate_session_response,
)


class _Projects:
    async def get_by_id(self, *args, **kwargs):
        return None


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.sessions = SessionRepository(self.session)
        self.assets = AssetRepository(self.session)
        self.projects = _Projects()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _seed_asset(
    sessions,
    *,
    asset_id: str,
    owner: str,
    filename: str,
    mime_type: str,
):
    async with _Uow(sessions) as uow:
        blob = await uow.assets.create_blob(
            {
                "id": f"blob-{asset_id}",
                "storage_backend": "object-local",
                "object_key": f"blobs/{asset_id}",
                "state": "READY",
                "size_bytes": 123,
                "sha256": ("a" if asset_id.endswith("a") else "b") * 64,
                "detected_mime_type": mime_type,
            }
        )
        await uow.assets.create_file(
            {
                "id": asset_id,
                "owner_user_id": owner,
                "blob_id": blob.id,
                "filename": filename,
                "mime_type": mime_type,
                "extension": filename.rsplit(".", 1)[-1],
                "origin_type": "USER_UPLOAD",
                "state": "READY",
                "revision": 0,
            }
        )
        await uow.commit()


def _image(asset_id: str):
    return {
        "type": "image",
        "data": {
            "attachment": {
                "asset_id": asset_id,
                "filename": "fake.bin",
                "mime_type": "application/octet-stream",
                "size": 1,
                "source": "asset",
            },
            "detail": "auto",
        },
    }


def _file(asset_id: str):
    return {
        "type": "file",
        "data": {
            "attachment": {
                "asset_id": asset_id,
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
                "source": "asset",
            },
            "page_range": "1-2",
        },
    }


@pytest.mark.asyncio
async def test_f4_message_and_reference_commit_atomically_and_canonicalize():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="real.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-a",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-a",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "before"},
                        _image("asset-a"),
                    ],
                }
            ],
        )
        async with _Uow(sessions) as uow:
            history = await uow.sessions.get_messages_by_session_id("session-a")
            assert len(history) == 1
            attachment = history[0].content[1]["data"]["attachment"]
            assert attachment["asset_id"] == "asset-a"
            assert attachment["filename"] == "real.png"
            assert attachment["mime_type"] == "image/png"
            assert attachment["uri"] == "asset://asset-a"
            refs = await uow.assets.list_references_for_file("asset-a")
            assert len(refs) == 1
            assert refs[0].reference_type == "MESSAGE_CONTENT"
            assert refs[0].message_id == history[0].id
            assert refs[0].content_part_index == 1
            assert (await uow.assets.get_file("asset-a")).revision == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_foreign_asset_rolls_back_new_session_message_and_edge():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-foreign",
            owner="user-b",
            filename="foreign.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        with pytest.raises(MessageAccessDeniedError):
            await service.persist_request_messages(
                session_id="session-a",
                owner_user_id="user-a",
                organization_id=None,
                turn_id="turn-a",
                messages=[
                    {"role": "user", "content": [_image("asset-foreign")]}
                ],
            )
        async with _Uow(sessions) as uow:
            assert await uow.sessions.get_by_id("session-a") is None
            assert (
                await uow.assets.list_references_for_file("asset-foreign")
                == []
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_context_dual_read_preserves_canonical_asset():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-a",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-a",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        _image("asset-a"),
                    ],
                }
            ],
        )
        context = ContextEngine(object(), lambda: _Uow(sessions))
        loaded = await context.load_context(
            "session-a",
            Identity(
                user_id="user-a",
                organization_id=None,
                auth_type="guest",
            ),
        )
        assert loaded.session.messages[0].content[0].text == "look"
        image = loaded.session.messages[0].content[1]
        assert image.data.attachment.asset_id == "asset-a"
        assert [item.asset_id for item in loaded.accessible_files] == [
            "asset-a"
        ]
    finally:
        await engine.dispose()


def test_f4_gateway_attachment_keeps_asset_identity_separate():
    canonical = GatewayAttachment(
        asset_id="asset-a",
        mime_type="image/png",
        source="asset",
    )
    assert canonical.asset_id == "asset-a"
    assert canonical.id is None
    assert canonical.uri == "asset://asset-a"

    legacy = GatewayAttachment(
        id="provider-file-1",
        provider_file_id="files/provider-file-1",
        mime_type="image/png",
        source="provider",
    )
    assert legacy.asset_id is None


def test_f4_file_content_keeps_document_subtype_and_page_range():
    part = MessageContentPart.model_validate(
        {
            "type": "file",
            "data": {
                "attachment": {
                    "asset_id": "asset-doc",
                    "mime_type": "application/pdf",
                    "source": "asset",
                },
                "page_range": "2-5",
                "extracted_text": "excerpt",
            },
        }
    )
    assert isinstance(part.data, DocumentContent)
    assert part.data.page_range == "2-5"
    assert part.data.extracted_text == "excerpt"


@pytest.mark.asyncio
async def test_f4_generic_reference_api_cannot_bypass_message_authority():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        async with _Uow(sessions) as uow:
            with pytest.raises(ValueError, match="create_message_references"):
                await uow.assets.create_reference(
                    {
                        "file_id": "asset-a",
                        "reference_type": "MESSAGE_CONTENT",
                        "message_id": "fake-message",
                        "content_part_index": 0,
                    }
                )
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_f4_session_delete_fails_closed_while_message_asset_edge_exists():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-a",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-a",
            messages=[{"role": "user", "content": [_image("asset-a")]}],
        )

        async with _Uow(sessions) as uow:
            with pytest.raises(SessionAssetReferenceRetentionError):
                await uow.sessions.delete_owned_session(
                    "session-a",
                    "user-a",
                )

        async with _Uow(sessions) as uow:
            assert await uow.sessions.get_by_id("session-a") is not None
            refs = await uow.assets.list_references_for_file("asset-a")
            assert len(refs) == 1
            assert refs[0].reference_type == "MESSAGE_CONTENT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_session_delete_route_maps_retention_block_to_409():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-a",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-a",
            messages=[{"role": "user", "content": [_image("asset-a")]}],
        )
        container = SimpleNamespace(
            uow_factory=lambda: _Uow(sessions),
            event_bus=None,
        )
        with pytest.raises(HTTPException) as caught:
            await delete_session(
                "session-a",
                Identity(
                    user_id="user-a",
                    organization_id=None,
                    auth_type="guest",
                ),
                container,
            )
        assert caught.value.status_code == 409

        async with _Uow(sessions) as uow:
            assert await uow.sessions.get_by_id("session-a") is not None
            assert len(
                await uow.assets.list_references_for_file("asset-a")
            ) == 1
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_f4_context_filters_foreign_session_and_project_resource_edges():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-owned",
            owner="user-a",
            filename="owned.png",
            mime_type="image/png",
        )
        await _seed_asset(
            sessions,
            asset_id="asset-foreign-session",
            owner="user-b",
            filename="foreign-session.png",
            mime_type="image/png",
        )
        await _seed_asset(
            sessions,
            asset_id="asset-foreign-project",
            owner="user-b",
            filename="foreign-project.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-owner-filter",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-owner-filter",
            messages=[
                {
                    "role": "user",
                    "content": [_image("asset-owned")],
                }
            ],
        )
        async with _Uow(sessions) as uow:
            session = await uow.sessions.get_by_id("session-owner-filter")
            session.project_id = "project-owner-filter"
            await uow.assets.create_reference(
                {
                    "file_id": "asset-owned",
                    "reference_type": "SESSION_RESOURCE",
                    "session_id": "session-owner-filter",
                }
            )
            await uow.assets.create_reference(
                {
                    "file_id": "asset-foreign-session",
                    "reference_type": "SESSION_RESOURCE",
                    "session_id": "session-owner-filter",
                }
            )
            await uow.assets.create_reference(
                {
                    "file_id": "asset-foreign-project",
                    "reference_type": "PROJECT_RESOURCE",
                    "project_id": "project-owner-filter",
                }
            )
            await uow.commit()

        context = ContextEngine(object(), lambda: _Uow(sessions))
        loaded = await context.load_context(
            "session-owner-filter",
            Identity(
                user_id="user-a",
                organization_id=None,
                auth_type="guest",
            ),
        )
        assert [item.asset_id for item in loaded.accessible_files] == [
            "asset-owned"
        ]

        async with _Uow(sessions) as uow:
            rows = await uow.assets.list_context_file_rows(
                session_id="session-owner-filter",
                owner_user_id="user-a",
                project_id="project-owner-filter",
            )
            assert [file_record.id for file_record, _ in rows] == [
                "asset-owned"
            ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_asset_bearing_message_edit_fails_closed_and_retains_edge():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-edit-asset",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-edit-asset",
            messages=[{"role": "user", "content": [_image("asset-a")]}],
        )
        async with _Uow(sessions) as uow:
            message = (
                await uow.sessions.get_messages_by_session_id(
                    "session-edit-asset"
                )
            )[0]
            message_id = message.id
            original_content = message.content

        container = SimpleNamespace(
            uow_factory=lambda: _Uow(sessions),
        )
        with pytest.raises(HTTPException) as caught:
            await edit_session_message(
                "session-edit-asset",
                message_id,
                SimpleNamespace(content="replace with text"),
                Identity(
                    user_id="user-a",
                    organization_id=None,
                    auth_type="guest",
                ),
                container,
            )
        assert caught.value.status_code == 409

        async with _Uow(sessions) as uow:
            message = await uow.sessions.get_message(
                "session-edit-asset",
                message_id,
            )
            assert message.content == original_content
            refs = await uow.assets.list_references_for_file("asset-a")
            assert len(refs) == 1
            assert refs[0].message_id == message_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_text_message_cannot_be_patched_into_canonical_asset():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-b",
            owner="user-b",
            filename="b.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-edit-text",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-edit-text",
            messages=[{"role": "user", "content": "old"}],
        )
        async with _Uow(sessions) as uow:
            message = (
                await uow.sessions.get_messages_by_session_id(
                    "session-edit-text"
                )
            )[0]
            message_id = message.id

        container = SimpleNamespace(
            uow_factory=lambda: _Uow(sessions),
        )
        with pytest.raises(HTTPException) as caught:
            await edit_session_message(
                "session-edit-text",
                message_id,
                SimpleNamespace(content=[_image("asset-b")]),
                Identity(
                    user_id="user-a",
                    organization_id=None,
                    auth_type="guest",
                ),
                container,
            )
        assert caught.value.status_code == 409

        async with _Uow(sessions) as uow:
            message = await uow.sessions.get_message(
                "session-edit-text",
                message_id,
            )
            assert message.content == {"type": "text", "data": "old"}
            assert (
                await uow.assets.list_references_for_file("asset-b")
                == []
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_plain_text_edit_without_asset_refs_remains_supported():
    engine, sessions = await _database()
    try:
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-edit-plain",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-edit-plain",
            messages=[{"role": "user", "content": "old"}],
        )
        async with _Uow(sessions) as uow:
            message = (
                await uow.sessions.get_messages_by_session_id(
                    "session-edit-plain"
                )
            )[0]
            message_id = message.id

        result = await edit_session_message(
            "session-edit-plain",
            message_id,
            SimpleNamespace(content="new"),
            Identity(
                user_id="user-a",
                organization_id=None,
                auth_type="guest",
            ),
            SimpleNamespace(uow_factory=lambda: _Uow(sessions)),
        )
        assert result["content"] == {"type": "text", "data": "new"}
    finally:
        await engine.dispose()


class _CountingProviderHandler:
    def __init__(self):
        self.calls = 0

    async def execute_with_fallback(self, http_client, payload):
        self.calls += 1
        return SimpleNamespace(
            model_dump=lambda mode="json": {"choices": []}
        )


def _regen_body():
    return SimpleNamespace(
        model="test-model",
        config={},
        metadata={},
        tools=[],
    )


@pytest.mark.asyncio
async def test_f4_regenerate_asset_history_fails_before_provider_call():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-regen-asset",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-regen-asset",
            messages=[{"role": "user", "content": [_file("asset-a")]}],
        )
        handler = _CountingProviderHandler()
        container = SimpleNamespace(
            uow_factory=lambda: _Uow(sessions),
            provider_runtime=SimpleNamespace(chat_handler=handler),
            http_client=None,
            message_service=service,
        )
        with pytest.raises(HTTPException) as caught:
            await regenerate_session_response(
                "session-regen-asset",
                _regen_body(),
                Identity(
                    user_id="user-a",
                    organization_id=None,
                    auth_type="guest",
                ),
                container,
            )
        assert caught.value.status_code == 409
        assert handler.calls == 0
        async with _Uow(sessions) as uow:
            history = await uow.sessions.get_messages_by_session_id(
                "session-regen-asset"
            )
            assert len(history) == 1
            assert len(
                await uow.assets.list_references_for_file("asset-a")
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_regenerate_text_history_still_invokes_provider():
    engine, sessions = await _database()
    try:
        service = CanonicalMessageService(lambda: _Uow(sessions))
        await service.persist_request_messages(
            session_id="session-regen-text",
            owner_user_id="user-a",
            organization_id=None,
            turn_id="turn-regen-text",
            messages=[{"role": "user", "content": "hello"}],
        )
        handler = _CountingProviderHandler()
        response = await regenerate_session_response(
            "session-regen-text",
            _regen_body(),
            Identity(
                user_id="user-a",
                organization_id=None,
                auth_type="guest",
            ),
            SimpleNamespace(
                uow_factory=lambda: _Uow(sessions),
                provider_runtime=SimpleNamespace(chat_handler=handler),
                http_client=None,
                message_service=service,
            ),
        )
        assert response == {"choices": []}
        assert handler.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_ready_pin_accepts_repeated_independent_additive_pins():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-pin",
            owner="user-a",
            filename="pin.png",
            mime_type="image/png",
        )
        async with _Uow(sessions) as uow:
            first, _ = await uow.assets.pin_owned_ready_file(
                file_id="asset-pin",
                owner_user_id="user-a",
            )
            await uow.commit()
            assert first.state == "READY"
            assert first.revision == 1
        async with _Uow(sessions) as uow:
            second, _ = await uow.assets.pin_owned_ready_file(
                file_id="asset-pin",
                owner_user_id="user-a",
            )
            await uow.commit()
            assert second.state == "READY"
            assert second.revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_non_ready_asset_pin_fails_without_partial_message():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-error",
            owner="user-a",
            filename="error.png",
            mime_type="image/png",
        )
        async with _Uow(sessions) as uow:
            file_record = await uow.assets.get_file("asset-error")
            file_record.state = "ERROR"
            await uow.commit()

        service = CanonicalMessageService(lambda: _Uow(sessions))
        with pytest.raises(MessageAssetStateError):
            await service.persist_request_messages(
                session_id="session-error",
                owner_user_id="user-a",
                organization_id=None,
                turn_id="turn-error",
                messages=[
                    {"role": "user", "content": [_image("asset-error")]}
                ],
            )
        async with _Uow(sessions) as uow:
            assert await uow.sessions.get_by_id("session-error") is None
            assert (
                await uow.assets.list_references_for_file("asset-error")
                == []
            )
    finally:
        await engine.dispose()

class _RecordingBus:
    def __init__(self):
        self.published = []

    async def publish(self, event):
        self.published.append(event)


@pytest.mark.parametrize(
    "content",
    [
        _image("asset-a"),
        _file("asset-a"),
        {
            "type": "audio",
            "data": {
                "attachment": {
                    "asset_id": "asset-a",
                    "source": "asset",
                    "uri": "asset://asset-a",
                }
            },
        },
        {
            "type": "video",
            "data": {
                "attachment": {
                    "asset_id": "asset-a",
                    "source": "asset",
                    "uri": "asset://asset-a",
                }
            },
        },
    ],
)
def test_f4_shared_detector_finds_nested_canonical_asset_shapes(content):
    assert contains_canonical_asset_content([content]) is True
    assert contains_canonical_asset_content(
        {"type": "text", "data": "plain"}
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DIRECT", "AGENT"])
async def test_f4_workflow_blocks_asset_history_before_direct_or_agent(mode):
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        direct_chat_runtime=object(),
        agent_runtime=object(),
    )
    calls = {"direct": 0, "agent": 0}

    async def direct(*args, **kwargs):
        calls["direct"] += 1

    async def agent(*args, **kwargs):
        calls["agent"] += 1

    runtime._execute_direct = direct
    runtime._execute_agent = agent

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-workflow-asset",
            turn_id="turn-workflow-asset",
            payload={
                "request_body": {
                    "_chat_execution_mode": mode,
                    "messages": [
                        {
                            "role": "user",
                            "content": [_image("asset-a")],
                        }
                    ],
                }
            },
        )
    )

    assert calls == {"direct": 0, "agent": 0}
    assert len(runtime.event_bus.published) == 1
    failure = runtime.event_bus.published[0]
    assert failure.event_name == "provider.failed"
    assert failure.payload["status_code"] == 409
    assert failure.payload["retryable"] is False
    assert failure.payload["failure_domain"] == "MESSAGE_ASSET"
    assert failure.payload["error_code"] == "ASSET_HYDRATION_REQUIRED"


@pytest.mark.asyncio
async def test_f4_workflow_blocks_asset_history_before_legacy_provider_event():
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        direct_chat_runtime=None,
        agent_runtime=None,
    )

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-workflow-fallback-asset",
            turn_id="turn-workflow-fallback-asset",
            payload={
                "request_body": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [_file("asset-a")],
                        }
                    ]
                }
            },
        )
    )

    assert [event.event_name for event in runtime.event_bus.published] == [
        "provider.failed"
    ]
    assert runtime.event_bus.published[0].payload["status_code"] == 409


@pytest.mark.asyncio
async def test_f4_workflow_text_only_direct_still_dispatches():
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        direct_chat_runtime=object(),
        agent_runtime=None,
    )
    calls = {"direct": 0}

    async def direct(*args, **kwargs):
        calls["direct"] += 1

    runtime._execute_direct = direct

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-workflow-text",
            turn_id="turn-workflow-text",
            payload={
                "request_body": {
                    "_chat_execution_mode": "DIRECT",
                    "messages": [
                        {"role": "user", "content": "hello"}
                    ],
                }
            },
        )
    )

    assert calls["direct"] == 1
    assert runtime.event_bus.published == []


@pytest.mark.asyncio
async def test_f4_workflow_text_only_legacy_fallback_still_publishes():
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        direct_chat_runtime=None,
        agent_runtime=None,
    )

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-workflow-fallback-text",
            turn_id="turn-workflow-fallback-text",
            payload={
                "request_body": {
                    "messages": [
                        {"role": "user", "content": "hello"}
                    ]
                }
            },
        )
    )

    assert [event.event_name for event in runtime.event_bus.published] == [
        "provider.chat.execute"
    ]

