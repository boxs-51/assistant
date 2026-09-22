from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets import AssetInUseError, AssetService
from se.src.application.messages import (
    CanonicalMessageService,
    MessageAccessDeniedError,
)
from se.src.context.manager import ContextEngine
from se.src.domain.schemas.attachment import GatewayAttachment
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository
from se.src.infrastructure.storage.repositories.chat_data.sessions import (
    SessionRepository,
)
from se.src.provider.exceptions import ProviderError
from se.src.provider.handlers.chat_handler import _reject_unhydrated_assets


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

    async def rollback(self):
        await self.session.rollback()


class _NeverUsedStore:
    async def delete(self, *args, **kwargs):
        raise AssertionError("live message asset must be fenced before object delete")


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


def _image(asset_id: str, *, fake_name: str = "fake.bin"):
    return {
        "type": "image",
        "data": {
            "attachment": {
                "asset_id": asset_id,
                "filename": fake_name,
                "mime_type": "application/octet-stream",
                "size": 1,
                "source": "asset",
            },
            "detail": "auto",
        },
    }


@pytest.mark.asyncio
async def test_f4_message_and_asset_reference_commit_atomically_and_canonicalize():
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
                        {"type": "text", "text": "after"},
                    ],
                }
            ],
        )

        async with _Uow(sessions) as uow:
            history = await uow.sessions.get_messages_by_session_id("session-a")
            assert len(history) == 1
            content = history[0].content
            assert [part["type"] for part in content] == [
                "text",
                "image",
                "text",
            ]
            attachment = content[1]["data"]["attachment"]
            assert attachment["asset_id"] == "asset-a"
            assert attachment["filename"] == "real.png"
            assert attachment["mime_type"] == "image/png"
            assert attachment["size"] == 123
            assert attachment["uri"] == "asset://asset-a"
            assert "base64_data" not in attachment

            refs = await uow.assets.list_references_for_file("asset-a")
            assert len(refs) == 1
            assert refs[0].reference_type == "MESSAGE_CONTENT"
            assert refs[0].message_id == history[0].id
            assert refs[0].content_part_index == 1

            file_record = await uow.assets.get_file("asset-a")
            assert file_record.revision == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_foreign_asset_rolls_back_new_session_and_message():
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
            assert await uow.assets.list_references_for_file("asset-foreign") == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_edit_replaces_message_references_in_same_transaction():
    engine, sessions = await _database()
    try:
        await _seed_asset(
            sessions,
            asset_id="asset-a",
            owner="user-a",
            filename="a.png",
            mime_type="image/png",
        )
        await _seed_asset(
            sessions,
            asset_id="asset-b",
            owner="user-a",
            filename="b.png",
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
            message = (
                await uow.sessions.get_messages_by_session_id("session-a")
            )[0]
            message_id = message.id
            sequence = message.sequence

        edited = await service.edit_message(
            session_id="session-a",
            message_id=message_id,
            owner_user_id="user-a",
            content=[_image("asset-b")],
        )
        assert edited.sequence == sequence

        async with _Uow(sessions) as uow:
            assert await uow.assets.list_references_for_file("asset-a") == []
            refs = await uow.assets.list_references_for_file("asset-b")
            assert len(refs) == 1
            assert refs[0].message_id == message_id
            assert refs[0].content_part_index == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_message_asset_is_live_while_same_session_execution_waits():
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
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-a",
                    session_id="session-a",
                    agent_id="agent-a",
                    correlation_id="corr-a",
                    state="WAITING",
                    revision=1,
                    request={},
                )
            )
            await uow.commit()

        asset_service = AssetService(
            lambda: _Uow(sessions),
            _NeverUsedStore(),
        )
        with pytest.raises(AssetInUseError):
            await asset_service.delete_asset(
                owner_user_id="user-a",
                asset_id="asset-a",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_context_round_trip_preserves_structured_content_and_asset_access():
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
        assert [item.asset_id for item in loaded.accessible_files] == ["asset-a"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f4_session_delete_removes_message_reference_not_asset():
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
            assert await uow.sessions.delete_owned_session(
                "session-a",
                "user-a",
            )
            await uow.commit()

        async with _Uow(sessions) as uow:
            assert await uow.sessions.get_by_id("session-a") is None
            assert await uow.assets.get_file("asset-a") is not None
            assert await uow.assets.list_references_for_file("asset-a") == []
    finally:
        await engine.dispose()


def test_f4_gateway_attachment_keeps_asset_id_separate_from_legacy_id():
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


def test_f4_provider_rejects_unhydrated_canonical_asset():
    with pytest.raises(ProviderError, match="F5"):
        _reject_unhydrated_assets(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [_image("asset-a")],
                    }
                ]
            }
        )
