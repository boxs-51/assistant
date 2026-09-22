from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets import (
    AssetInUseError,
    AssetMimeMismatchError,
    AssetService,
)
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.object_local.driver import (
    LocalObjectStorageDriver,
)
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.agent.iteration import (
    AgentIterationRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository
from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.serialization import to_json_safe


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.assets = AssetRepository(self.session)
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


async def _chunks(payload: bytes, split: int = 5):
    for start in range(0, len(payload), split):
        yield payload[start:start + split]


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _service(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    return engine, sessions, driver, AssetService(lambda: _Uow(sessions), driver)


@pytest.mark.asyncio
async def test_f2h_provisional_tool_result_cannot_pin_asset_but_committed_can(tmp_path):
    engine, sessions, driver, service = await _service(tmp_path)
    try:
        asset = await service.ingest_stream(
            owner_user_id="user-a",
            filename="result.txt",
            mime_type="text/plain",
            stream=_chunks(b"result"),
            content_length=6,
        )
        async with _Uow(sessions) as uow:
            execution = AgentExecutionRecord(
                id="exec-f2h",
                session_id="session-f2h",
                agent_id="agent-f2h",
                correlation_id="corr-f2h",
                state="WAITING",
            )
            iteration = AgentIterationRecord(
                id="iter-f2h",
                execution_id=execution.id,
                iteration=1,
                state="WAITING_TOOL",
                tool_call_ids=["call-f2h"],
            )
            tool_result = AgentToolResultRecord(
                id="tool-result-f2h",
                execution_id=execution.id,
                iteration_id=iteration.id,
                invocation_id="inv-f2h",
                tool_call_id="call-f2h",
                capability_id="tool.file",
                success=True,
                output={"asset_id": asset.asset_id, "uri": asset.uri},
                commit_state="PROVISIONAL",
            )
            uow.session.add_all([execution, iteration, tool_result])
            await uow.session.flush()

            with pytest.raises(ValueError, match="PROVISIONAL"):
                await uow.assets.create_tool_result_reference(
                    file_id=asset.asset_id,
                    agent_tool_result_id=tool_result.id,
                )

            tool_result.commit_state = "COMMITTED"
            await uow.session.flush()
            reference = await uow.assets.create_tool_result_reference(
                file_id=asset.asset_id,
                agent_tool_result_id=tool_result.id,
            )
            duplicate = await uow.assets.create_tool_result_reference(
                file_id=asset.asset_id,
                agent_tool_result_id=tool_result.id,
            )
            assert reference.id == duplicate.id
            assert reference.reference_type == "AGENT_TOOL_RESULT"
            assert await uow.assets.has_live_references(asset.asset_id) is True
            await uow.commit()
    finally:
        await driver.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_f2h_r7_live_reference_blocks_delete_until_execution_terminal(tmp_path):
    engine, sessions, driver, service = await _service(tmp_path)
    try:
        asset = await service.ingest_stream(
            owner_user_id="user-a",
            filename="resume.txt",
            mime_type="text/plain",
            stream=_chunks(b"resume-safe"),
            content_length=11,
        )
        async with _Uow(sessions) as uow:
            execution = AgentExecutionRecord(
                id="exec-live",
                session_id="session-live",
                agent_id="agent-live",
                correlation_id="corr-live",
                state="WAITING",
            )
            iteration = AgentIterationRecord(
                id="iter-live",
                execution_id=execution.id,
                iteration=1,
                state="WAITING_TOOL",
                tool_call_ids=["call-live"],
            )
            tool_result = AgentToolResultRecord(
                id="tool-result-live",
                execution_id=execution.id,
                iteration_id=iteration.id,
                invocation_id="inv-live",
                tool_call_id="call-live",
                capability_id="tool.file",
                success=True,
                output={"asset_id": asset.asset_id, "uri": asset.uri},
                commit_state="COMMITTED",
            )
            uow.session.add_all([execution, iteration, tool_result])
            await uow.session.flush()
            await uow.assets.create_tool_result_reference(
                file_id=asset.asset_id,
                agent_tool_result_id=tool_result.id,
            )
            await uow.commit()

        with pytest.raises(AssetInUseError):
            await service.delete_asset(
                owner_user_id="user-a",
                asset_id=asset.asset_id,
            )

        async with _Uow(sessions) as uow:
            execution = await uow.session.get(AgentExecutionRecord, "exec-live")
            execution.state = "COMPLETED"
            await uow.commit()

        deleting = await service.delete_asset(
            owner_user_id="user-a",
            asset_id=asset.asset_id,
        )
        assert deleting.state == "DELETING"
        collected = await service.collect_deleting_asset(asset.asset_id)
        assert collected.state == "DELETED"
    finally:
        await driver.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_f2h_mime_detection_is_independent_from_declared_type(tmp_path):
    engine, sessions, driver, service = await _service(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    try:
        asset = await service.ingest_stream(
            owner_user_id="user-a",
            filename="image.bin",
            mime_type="application/octet-stream",
            stream=_chunks(png),
            content_length=len(png),
        )
        assert asset.declared_mime_type == "application/octet-stream"
        assert asset.detected_mime_type == "image/png"
        assert asset.mime_type == "image/png"

        with pytest.raises(AssetMimeMismatchError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="spoof.txt",
                mime_type="text/plain",
                stream=_chunks(png),
                content_length=len(png),
            )

        async with _Uow(sessions) as uow:
            errors = await uow.assets.list_files_by_owner(
                "user-a",
                states=["ERROR"],
            )
            assert len(errors) == 1
            blob = await uow.assets.get_blob(errors[0].blob_id)
            assert blob.detected_mime_type == "image/png"
            assert await driver.exists(blob.object_key) is False
    finally:
        await driver.disconnect()
        await engine.dispose()


def test_f2h_r7_checkpoint_json_round_trip_preserves_stable_asset_reference():
    original = InferenceMessage(
        role="tool",
        name="tool.file",
        tool_call_id="call-asset",
        content={
            "artifact": {
                "asset_id": "asset_stable",
                "uri": "asset://asset_stable",
                "filename": "report.pdf",
            }
        },
    )
    snapshot = to_json_safe(
        [original.model_dump(mode="json")],
        path="agent_execution_checkpoints.transcript_snapshot",
    )
    restored = InferenceMessage.model_validate(snapshot[0])

    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    assert restored.content["artifact"]["asset_id"] == "asset_stable"
    assert restored.content["artifact"]["uri"] == "asset://asset_stable"
