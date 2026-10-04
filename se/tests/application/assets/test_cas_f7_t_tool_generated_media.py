from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets.tool_generated_media import (
    EMPTY_F7T_ENROLLMENT,
    F7T_INLINE_BASE64_V1,
    ToolGeneratedMediaAmbiguousError,
    ToolGeneratedMediaCanonicalizer,
    ToolGeneratedMediaRejectedError,
    origin_id_for_source_key,
    projection_id_for_source_key,
)
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_call import (
    AgentToolCallRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.assets import (
    ToolMediaAssetProjectionRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import Session
from se.src.infrastructure.storage.repositories.assets import AssetRepository


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


class _AssetService:
    def __init__(self, *, fail=False, cancel=False, block=None):
        self.calls = []
        self.fail = fail
        self.cancel = cancel
        self.block = block

    async def ingest_stream(self, **kwargs):
        payload = b"".join([chunk async for chunk in kwargs["stream"]])
        call = dict(kwargs)
        call.pop("stream")
        call["payload"] = payload
        self.calls.append(call)
        if self.block is not None:
            await self.block.wait()
        if self.cancel:
            raise asyncio.CancelledError()
        if self.fail:
            raise RuntimeError("simulated ingest uncertainty")
        return SimpleNamespace(asset_id=f"asset-{len(self.calls)}")


async def _database(tmp_path):
    db_path = tmp_path / "f7t.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _tool_output(payload=b"media-bytes"):
    encoded = base64.b64encode(payload).decode("ascii")
    return {
        "ok": True,
        "tool": "image.generate",
        "action": "generate",
        "data": {
            "$f7t_media": {
                "contract": F7T_INLINE_BASE64_V1,
                "items": [
                    {
                        "ordinal": 0,
                        "media_kind": "image",
                        "mime_type": "image/png",
                        "filename": "generated.png",
                        "encoding": "base64",
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "data_base64": encoded,
                    }
                ],
            }
        },
        "error": None,
        "meta": {
            "version": "1.0",
            "truncated": False,
            "warnings": [],
        },
    }


async def _seed(
    sessions,
    *,
    output=None,
    result_capability_id="tool.image",
    invocation_capability_id="tool.image",
    capability_version="1.0",
    invocation_state="COMPLETED",
    invocation_kind="TOOL",
):
    async with _Uow(sessions) as uow:
        uow.session.add(
            Session(id="session-1", user_id="user-1", status="active")
        )
        uow.session.add(
            AgentExecutionRecord(
                id="exec-1",
                session_id="session-1",
                agent_id="agent-1",
                correlation_id="corr-1",
                state="COMPLETED",
                revision=0,
                request={},
            )
        )
        uow.session.add(
            AgentToolCallRecord(
                id="call-row-1",
                execution_id="exec-1",
                iteration_id="iteration-1",
                invocation_id="inv-1",
                tool_call_id="call-1",
                capability_id=result_capability_id,
                arguments={},
                status="COMPLETED",
            )
        )
        uow.session.add(
            CapabilityInvocationRecord(
                invocation_id="inv-1",
                capability_id=invocation_capability_id,
                capability_version=capability_version,
                kind=invocation_kind,
                execution_mode="ONE_SHOT",
                idempotency="UNKNOWN",
                owner_user_id="user-1",
                state=invocation_state,
                session_id="session-1",
                execution_id="exec-1",
                tool_call_id="call-1",
                attempt=1,
                max_attempts=1,
                arguments={},
            )
        )
        uow.session.add(
            AgentToolResultRecord(
                id="result-1",
                execution_id="exec-1",
                iteration_id="iteration-1",
                invocation_id="inv-1",
                tool_call_id="call-1",
                capability_id=result_capability_id,
                success=True,
                output=copy.deepcopy(output or _tool_output()),
                error_code=None,
                error_message=None,
                retryable=False,
                commit_state="COMMITTED",
                attempt=1,
            )
        )
        await uow.commit()


def _canonicalizer(sessions, asset_service, enrollment=None):
    return ToolGeneratedMediaCanonicalizer(
        uow_factory=lambda: _Uow(sessions),
        asset_service=asset_service,
        enrollment=(
            EMPTY_F7T_ENROLLMENT
            if enrollment is None
            else enrollment
        ),
        max_media_bytes=1024 * 1024,
    )


async def _projection_rows(sessions):
    async with _Uow(sessions) as uow:
        result = await uow.session.execute(
            select(ToolMediaAssetProjectionRecord)
        )
        return list(result.scalars().all())


def test_f7_t_schema_and_deterministic_identity_contract():
    table = ToolMediaAssetProjectionRecord.__table__
    unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert (
        "source_result_id",
        "invocation_id",
        "tool_call_id",
        "capability_id",
        "media_ordinal",
    ) in unique_sets
    assert ("origin_id",) in unique_sets

    key = {
        "source_result_id": "result-1",
        "invocation_id": "inv-1",
        "tool_call_id": "call-1",
        "capability_id": "tool.image",
        "media_ordinal": 0,
    }
    assert projection_id_for_source_key(**key).startswith("f7tp_")
    assert origin_id_for_source_key(**key).startswith("f7t:v1:")
    assert projection_id_for_source_key(**key)[5:] == (
        origin_id_for_source_key(**key)[7:]
    )

    migration = Path(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "27a_cas_f7_t_tool_media_projection.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "27a_cas_f7_t_tool_media_projection"' in migration
    assert 'down_revision: Union[str, None] = "26a_ubq2_dual_accounting_bridge"' in migration
    assert "Cannot downgrade CAS-F7-T while durable tool-media projections exist." in migration


@pytest.mark.asyncio
async def test_empty_production_enrollment_is_zero_mutation(tmp_path):
    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService()
    try:
        await _seed(sessions)
        result = await _canonicalizer(
            sessions, asset_service
        ).canonicalize_committed_result(source_result_id="result-1")
        assert result == ()
        assert asset_service.calls == []
        assert await _projection_rows(sessions) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lineage_mismatch_fails_before_projection_or_ingest(tmp_path):
    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService()
    try:
        await _seed(
            sessions,
            result_capability_id="tool.image",
            invocation_capability_id="tool.other",
        )
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        with pytest.raises(
            ToolGeneratedMediaRejectedError,
            match="lineage mismatch",
        ):
            await service.canonicalize_committed_result(
                source_result_id="result-1"
            )
        assert asset_service.calls == []
        assert await _projection_rows(sessions) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["failed", "truncated", "path", "base64", "size", "hash"])
async def test_invalid_source_or_envelope_is_zero_mutation(tmp_path, mutation):
    output = _tool_output()
    if mutation == "failed":
        output["ok"] = False
        output["error"] = {
            "code": "FAIL",
            "message": "failed",
            "retryable": False,
            "details": {},
        }
    elif mutation == "truncated":
        output["meta"]["truncated"] = True
    elif mutation == "path":
        item = output["data"]["$f7t_media"]["items"][0]
        item["path"] = "/tmp/file"
    elif mutation == "base64":
        output["data"]["$f7t_media"]["items"][0]["data_base64"] = "***"
    elif mutation == "size":
        output["data"]["$f7t_media"]["items"][0]["size_bytes"] += 1
    elif mutation == "hash":
        output["data"]["$f7t_media"]["items"][0]["sha256"] = "0" * 64

    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService()
    try:
        await _seed(sessions, output=output)
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        with pytest.raises(ToolGeneratedMediaRejectedError):
            await service.canonicalize_committed_result(
                source_result_id="result-1"
            )
        assert asset_service.calls == []
        assert await _projection_rows(sessions) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ready_reuse_is_zero_additional_ingest_and_source_is_immutable(tmp_path):
    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService()
    try:
        original = _tool_output()
        await _seed(sessions, output=original)
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        first = await service.canonicalize_committed_result(
            source_result_id="result-1"
        )
        second = await service.canonicalize_committed_result(
            source_result_id="result-1"
        )
        assert first[0].state == "READY"
        assert second == first
        assert len(asset_service.calls) == 1
        assert asset_service.calls[0]["origin_type"] == "TOOL"
        assert asset_service.calls[0]["origin_id"] == first[0].origin_id
        async with _Uow(sessions) as uow:
            durable = await uow.session.get(
                AgentToolResultRecord, "result-1"
            )
            assert durable.output == original
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["INGESTING", "AMBIGUOUS"])
async def test_restart_unsafe_states_never_reingest(tmp_path, state):
    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService()
    try:
        await _seed(sessions)
        key = {
            "source_result_id": "result-1",
            "invocation_id": "inv-1",
            "tool_call_id": "call-1",
            "capability_id": "tool.image",
            "media_ordinal": 0,
        }
        async with _Uow(sessions) as uow:
            uow.session.add(
                ToolMediaAssetProjectionRecord(
                    id=projection_id_for_source_key(**key),
                    **key,
                    execution_id="exec-1",
                    capability_version="1.0",
                    source_contract_id=F7T_INLINE_BASE64_V1,
                    owner_user_id="user-1",
                    origin_id=origin_id_for_source_key(**key),
                    state=state,
                    revision=1 if state == "INGESTING" else 2,
                )
            )
            await uow.commit()
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        with pytest.raises(ToolGeneratedMediaAmbiguousError):
            await service.canonicalize_committed_result(
                source_result_id="result-1"
            )
        assert asset_service.calls == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "cancel"])
async def test_ingest_failure_or_cancel_projects_ambiguous(tmp_path, mode):
    engine, sessions = await _database(tmp_path)
    asset_service = _AssetService(
        fail=mode == "raise",
        cancel=mode == "cancel",
    )
    try:
        await _seed(sessions)
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        expected = asyncio.CancelledError if mode == "cancel" else RuntimeError
        with pytest.raises(expected):
            await service.canonicalize_committed_result(
                source_result_id="result-1"
            )
        rows = await _projection_rows(sessions)
        assert len(rows) == 1
        assert rows[0].state == "AMBIGUOUS"
        assert rows[0].asset_id is None
        assert len(asset_service.calls) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_concurrent_source_key_has_one_ingest_winner(tmp_path):
    engine, sessions = await _database(tmp_path)
    gate = asyncio.Event()
    asset_service = _AssetService(block=gate)
    try:
        await _seed(sessions)
        service = _canonicalizer(
            sessions,
            asset_service,
            {("tool.image", "1.0"): F7T_INLINE_BASE64_V1},
        )
        first = asyncio.create_task(
            service.canonicalize_committed_result(
                source_result_id="result-1"
            )
        )
        for _ in range(100):
            if asset_service.calls:
                break
            await asyncio.sleep(0.01)
        assert len(asset_service.calls) == 1

        second = await asyncio.gather(
            service.canonicalize_committed_result(
                source_result_id="result-1"
            ),
            return_exceptions=True,
        )
        assert isinstance(second[0], ToolGeneratedMediaAmbiguousError)
        assert len(asset_service.calls) == 1

        gate.set()
        completed = await first
        assert completed[0].state == "READY"
        assert len(await _projection_rows(sessions)) == 1
    finally:
        gate.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_one_reserved_to_ingesting_revision_cas_wins(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed(sessions)
        key = {
            "source_result_id": "result-1",
            "invocation_id": "inv-1",
            "tool_call_id": "call-1",
            "capability_id": "tool.image",
            "media_ordinal": 0,
        }
        values = {
            "id": projection_id_for_source_key(**key),
            **key,
            "execution_id": "exec-1",
            "capability_version": "1.0",
            "source_contract_id": F7T_INLINE_BASE64_V1,
            "owner_user_id": "user-1",
            "origin_id": origin_id_for_source_key(**key),
            "state": "RESERVED",
            "revision": 0,
        }
        async with _Uow(sessions) as uow:
            _, created = (
                await uow.assets.try_create_tool_media_projection_reservation(
                    values
                )
            )
            assert created is True
            await uow.commit()

        async with _Uow(sessions) as uow:
            winner = await uow.assets.compare_and_set_tool_media_projection(
                values["id"],
                expected_revision=0,
                expected_state="RESERVED",
                values={"state": "INGESTING"},
            )
            assert winner is not None
            await uow.commit()

        async with _Uow(sessions) as uow:
            loser = await uow.assets.compare_and_set_tool_media_projection(
                values["id"],
                expected_revision=0,
                expected_state="RESERVED",
                values={"state": "INGESTING"},
            )
            assert loser is None
    finally:
        await engine.dispose()


def test_slice_contains_no_live_enrollment_replay_or_cleanup_authority():
    source = Path(
        "se/src/application/assets/tool_generated_media.py"
    ).read_text(encoding="utf-8")
    assert "EMPTY_F7T_ENROLLMENT" in source
    assert "MappingProxyType({})" in source
    for forbidden in (
        "execute_capability(",
        "continue_invocation(",
        "delete_asset(",
        "object_store.delete(",
        "provider_file_id",
        "sandbox",
        "http://",
        "https://",
    ):
        assert forbidden not in source
