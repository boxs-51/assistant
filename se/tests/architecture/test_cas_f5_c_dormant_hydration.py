from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets.hydration import (
    AssetHydrationPreflightError,
    CanonicalAssetHydrationService,
    HydrationStatus,
)
from se.src.infrastructure.config.schemas import ProviderConfig
from se.src.infrastructure.storage.interfaces.object import (
    ObjectStat,
    ObjectStorageDriver,
    ObjectWriteResult,
)
from se.src.infrastructure.storage.models.sql.assets import (
    FileProviderBindingRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository
from se.src.provider.core.interfaces.file import ProviderUploadOutcome


class _TestObjectStore(ObjectStorageDriver):
    backend_name = "test-object"

    def __init__(
        self,
        *,
        chunks=(b"he", b"llo"),
        fail_stat_on_call: int | None = None,
        fail_open: bool = False,
        cancel_open: bool = False,
    ) -> None:
        self.chunks = tuple(chunks)
        self.object_key = "blobs/blob-f5c"
        self.open_calls = 0
        self.stat_calls = 0
        self.iterated_chunks: list[bytes] = []
        self.fail_stat_on_call = fail_stat_on_call
        self.fail_open = fail_open
        self.cancel_open = cancel_open

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def put_stream(
        self,
        object_key,
        stream,
        *,
        content_length=None,
        content_type=None,
    ) -> ObjectWriteResult:
        raise AssertionError("F5-C must not write canonical object storage")

    async def open_stream(
        self,
        object_key,
        *,
        byte_range=None,
        chunk_size=64 * 1024,
    ):
        assert object_key == self.object_key
        assert byte_range is None
        self.open_calls += 1
        if self.cancel_open:
            raise asyncio.CancelledError()
        if self.fail_open:
            raise RuntimeError("open_stream acquisition failed")

        async def iterator():
            for chunk in self.chunks:
                self.iterated_chunks.append(chunk)
                yield chunk

        return iterator()

    async def stat(self, object_key) -> ObjectStat:
        assert object_key == self.object_key
        self.stat_calls += 1
        if self.fail_stat_on_call == self.stat_calls:
            raise RuntimeError("object stat failed")
        return ObjectStat(
            object_key=object_key,
            size_bytes=sum(len(chunk) for chunk in self.chunks),
        )

    async def exists(self, object_key) -> bool:
        return object_key == self.object_key

    async def delete(self, object_key) -> None:
        raise AssertionError("F5-C must not delete provider/canonical objects")

    async def presign_get(self, object_key, *, expires_seconds=300):
        return None


class _FilesBoundary:
    def __init__(
        self,
        *,
        outcome: ProviderUploadOutcome,
        trace: list[str],
        raise_cancel: bool = False,
        shared: dict | None = None,
    ) -> None:
        self.outcome = outcome
        self.trace = trace
        self.raise_cancel = raise_cancel
        self.shared = shared if shared is not None else {}
        self.calls = 0
        self.received_async_stream = False
        self.chunks: list[bytes] = []

    async def upload_file_outcome(self, **kwargs):
        self.calls += 1
        self.trace.append("provider")
        self.shared["provider_called"] = True

        stream = kwargs["file_stream"]
        self.received_async_stream = hasattr(stream, "__aiter__")
        assert self.received_async_stream is True
        assert kwargs["file_size"] == 5
        assert kwargs["mime_type"] == "text/plain"
        assert kwargs["display_name"] == "f5c.txt"

        if self.raise_cancel:
            raise asyncio.CancelledError()

        async for chunk in stream:
            self.chunks.append(bytes(chunk))

        return self.outcome


class _Provider:
    name = "gemini"

    def __init__(
        self,
        files,
        *,
        namespace: str | None = "gemini-project-a",
    ) -> None:
        self.files = files
        self.config = ProviderConfig(
            enabled=True,
            api_key="test",
            base_url="https://provider.invalid",
            file_binding_namespace=namespace,
        )


class _Registry:
    def __init__(self, provider=None) -> None:
        self.provider = provider

    def get_provider(self, name: str):
        if self.provider is not None and name == self.provider.name:
            return self.provider
        return None


class _InstrumentedAssetRepository(AssetRepository):
    def __init__(self, session, shared):
        super().__init__(session)
        self.shared = shared

    async def get_live_provider_binding(self, *args, **kwargs):
        binding = await super().get_live_provider_binding(*args, **kwargs)
        if self.shared.get("hide_live_once") and not self.shared.get(
            "live_hidden"
        ):
            self.shared["live_hidden"] = True
            return None
        self.shared["live_reads"] = self.shared.get("live_reads", 0) + 1
        return binding

    async def expire_active_provider_binding(self, *args, **kwargs):
        if self.shared.get("lose_retirement_cas"):
            self.shared["retirement_attempted"] = True
            return None
        return await super().expire_active_provider_binding(*args, **kwargs)

    async def compare_and_set_provider_binding(self, *args, **kwargs):
        values = kwargs.get("values") or {}
        if (
            self.shared.get("fail_active_transition")
            and values.get("state") == "ACTIVE"
        ):
            self.shared["active_transition_failed"] = True
            return None
        return await super().compare_and_set_provider_binding(
            *args, **kwargs
        )


class _Uow:
    def __init__(self, sessions, trace, shared):
        self.sessions = sessions
        self.trace = trace
        self.shared = shared
        self.session = None

    async def __aenter__(self):
        self.session = self.sessions()
        self.assets = _InstrumentedAssetRepository(
            self.session, self.shared
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            await self.session.rollback()
        await self.session.close()

    async def commit(self):
        self.trace.append("commit")
        await self.session.commit()

    async def rollback(self):
        self.trace.append("rollback")
        await self.session.rollback()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _uow_factory(sessions, trace, shared):
    return lambda: _Uow(sessions, trace, shared)


async def _seed_ready_asset(
    sessions,
    *,
    owner_user_id="u-f5c",
    state="READY",
    sha256="a" * 64,
):
    async with sessions() as session:
        assets = AssetRepository(session)
        blob = await assets.create_blob(
            {
                "id": "blob-f5c",
                "storage_backend": "test-object",
                "bucket": None,
                "object_key": "blobs/blob-f5c",
                "state": "READY",
                "size_bytes": 5,
                "sha256": sha256,
                "detected_mime_type": "text/plain",
            }
        )
        file_record = await assets.create_file(
            {
                "id": "asset-f5c",
                "owner_user_id": owner_user_id,
                "blob_id": blob.id,
                "filename": "f5c.txt",
                "mime_type": "text/plain",
                "extension": "txt",
                "origin_type": "USER_UPLOAD",
                "state": state,
            }
        )
        await session.commit()
        return file_record, blob


async def _seed_binding(
    sessions,
    *,
    state,
    provider_file_id=None,
    live_claim_token="LIVE",
    source_blob_id="blob-f5c",
    source_sha256="a" * 64,
    expires_at=None,
):
    async with sessions() as session:
        assets = AssetRepository(session)
        binding = await assets.create_provider_binding(
            {
                "id": f"binding-{state.lower()}",
                "file_id": "asset-f5c",
                "provider_name": "gemini",
                "provider_namespace": "gemini-project-a",
                "provider_file_id": provider_file_id,
                "provider_uri": (
                    f"https://provider.invalid/{provider_file_id}"
                    if provider_file_id
                    else None
                ),
                "source_blob_id": source_blob_id,
                "source_sha256": source_sha256,
                "live_claim_token": live_claim_token,
                "state": state,
                "expires_at": expires_at,
            }
        )
        await session.commit()
        return binding


async def _live_binding(sessions):
    async with sessions() as session:
        assets = AssetRepository(session)
        return await assets.get_live_provider_binding(
            "asset-f5c",
            "gemini",
            provider_namespace="gemini-project-a",
        )


def _service(
    sessions,
    store,
    files,
    trace,
    shared,
    *,
    namespace="gemini-project-a",
):
    provider = _Provider(files, namespace=namespace)
    return CanonicalAssetHydrationService(
        uow_factory=_uow_factory(sessions, trace, shared),
        object_store=store,
        provider_registry=_Registry(provider),
        http_client=object(),
        timeout=10,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["missing", "foreign", "non_ready"],
)
async def test_f5c_invalid_asset_fails_before_provider_call(case):
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
        shared=shared,
    )
    store = _TestObjectStore()
    service = _service(sessions, store, files, trace, shared)

    try:
        if case == "foreign":
            await _seed_ready_asset(sessions, owner_user_id="someone-else")
        elif case == "non_ready":
            await _seed_ready_asset(sessions, state="QUARANTINED")

        with pytest.raises(AssetHydrationPreflightError):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )

        assert files.calls == 0
        assert store.open_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", [None, "default"])
async def test_f5c_missing_or_default_namespace_fails_before_provider_call(
    namespace,
):
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    service = _service(
        sessions,
        store,
        files,
        trace,
        shared,
        namespace=namespace,
    )

    try:
        with pytest.raises(ValueError, match="HYDRATION_PROVIDER_SCOPE_UNCONFIGURED"):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )
        assert files.calls == 0
        assert store.stat_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_valid_active_exact_fingerprint_reuses_with_zero_upload():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    await _seed_binding(
        sessions,
        state="ACTIVE",
        provider_file_id="files/existing",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.REUSED
        assert result.provider_file_id == "files/existing"
        assert files.calls == 0
        assert store.open_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("PROCESSING", HydrationStatus.HYDRATION_IN_PROGRESS),
        ("UNKNOWN", HydrationStatus.HYDRATION_OUTCOME_UNKNOWN),
    ],
)
async def test_f5c_processing_and_unknown_fail_closed_without_upload(
    state,
    expected,
):
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    await _seed_binding(sessions, state=state)
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is expected
        assert files.calls == 0
        assert store.open_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_invalid_active_retirement_and_claim_commit_before_upload():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new",
            provider_uri="https://provider.invalid/files/new",
        ),
        trace=trace,
        shared=shared,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    await _seed_binding(
        sessions,
        state="ACTIVE",
        provider_file_id="files/old",
        source_sha256="b" * 64,
    )
    trace.clear()
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.HYDRATED
        provider_index = trace.index("provider")
        assert trace[:provider_index].count("commit") == 2
        assert files.calls == 1

        async with sessions() as session:
            rows = (
                await session.execute(
                    FileProviderBindingRecord.__table__.select().order_by(
                        FileProviderBindingRecord.created_at
                    )
                )
            ).mappings().all()
            assert len(rows) == 2
            assert rows[0]["state"] == "EXPIRED"
            assert rows[0]["live_claim_token"] is None
            assert rows[1]["state"] == "ACTIVE"
            assert rows[1]["provider_file_id"] == "files/new"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_retirement_cas_loser_rereads_and_uploads_zero_times():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {"lose_retirement_cas": True}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    await _seed_binding(
        sessions,
        state="ACTIVE",
        provider_file_id="files/old",
        source_sha256="b" * 64,
    )
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.HYDRATION_RACE_LOST
        assert shared["retirement_attempted"] is True
        assert shared.get("live_reads", 0) >= 1
        assert files.calls == 0
        assert store.open_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_claim_race_loser_rereads_winner_and_uploads_zero_times():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {"hide_live_once": True}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    await _seed_binding(sessions, state="PROCESSING")
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.HYDRATION_IN_PROGRESS
        assert shared["live_hidden"] is True
        assert files.calls == 0
        assert store.open_calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_state", "expected_live"),
    [
        (
            ProviderUploadOutcome.safe_no_remote_commit(),
            HydrationStatus.HYDRATION_FAILED_SAFE,
            "ERROR",
            None,
        ),
        (
            ProviderUploadOutcome.remote_outcome_unknown(),
            HydrationStatus.HYDRATION_OUTCOME_UNKNOWN,
            "UNKNOWN",
            "LIVE",
        ),
        (
            ProviderUploadOutcome.remote_success_known(
                provider_file_id="files/new",
                provider_uri="https://provider.invalid/files/new",
            ),
            HydrationStatus.HYDRATED,
            "ACTIVE",
            "LIVE",
        ),
    ],
)
async def test_f5c_claim_stream_and_durable_outcome_mapping(
    outcome,
    expected_status,
    expected_state,
    expected_live,
):
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=outcome,
        trace=trace,
        shared=shared,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    trace.clear()
    service = _service(sessions, store, files, trace, shared)

    try:
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )

        assert result.status is expected_status
        assert files.calls == 1
        assert files.received_async_stream is True
        assert files.chunks == [b"he", b"llo"]
        assert store.open_calls == 1
        assert store.iterated_chunks == [b"he", b"llo"]
        assert trace.index("commit") < trace.index("provider")

        binding = await _live_binding(sessions)
        if expected_state == "ERROR":
            assert binding is None
            async with sessions() as session:
                stored = await session.get(
                    FileProviderBindingRecord, result.binding_id
                )
                assert stored.state == "ERROR"
                assert stored.live_claim_token is None
                assert stored.provider_file_id is None
                assert (
                    stored.metadata_json["upload_outcome"]
                    == "SAFE_NO_REMOTE_COMMIT"
                )
        else:
            assert binding is not None
            assert binding.state == expected_state
            assert binding.live_claim_token == expected_live
            if expected_state == "UNKNOWN":
                assert binding.provider_file_id is None
                assert (
                    binding.metadata_json["upload_outcome"]
                    == "REMOTE_OUTCOME_UNKNOWN"
                )
            else:
                assert binding.provider_file_id == "files/new"
                assert (
                    binding.metadata_json["upload_outcome"]
                    == "REMOTE_SUCCESS_KNOWN"
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_post_claim_second_stat_failure_safe_releases_claim():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore(fail_stat_on_call=2)
    await _seed_ready_asset(sessions)
    service = _service(sessions, store, files, trace, shared)

    try:
        with pytest.raises(AssetHydrationPreflightError):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )

        assert files.calls == 0
        assert store.open_calls == 0

        async with sessions() as session:
            rows = (
                await session.execute(
                    FileProviderBindingRecord.__table__.select()
                )
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["state"] == "ERROR"
            assert rows[0]["live_claim_token"] is None
            assert rows[0]["metadata_json"]["upload_outcome"] == (
                "SAFE_NO_REMOTE_COMMIT"
            )
            assert rows[0]["metadata_json"]["upload_outcome_reason"] == (
                "PRE_PROVIDER_AssetHydrationPreflightError"
            )

        # Proven SAFE local failure releases the slot. A later invocation may
        # acquire a fresh claim rather than remaining permanently PROCESSING.
        store.fail_stat_on_call = None
        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.HYDRATED
        assert files.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_post_claim_open_stream_failure_safe_releases_claim():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore(fail_open=True)
    await _seed_ready_asset(sessions)
    service = _service(sessions, store, files, trace, shared)

    try:
        with pytest.raises(RuntimeError, match="open_stream acquisition failed"):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )

        assert files.calls == 0
        async with sessions() as session:
            rows = (
                await session.execute(
                    FileProviderBindingRecord.__table__.select()
                )
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["state"] == "ERROR"
            assert rows[0]["live_claim_token"] is None
            assert rows[0]["metadata_json"]["upload_outcome"] == (
                "SAFE_NO_REMOTE_COMMIT"
            )
            assert rows[0]["metadata_json"]["upload_outcome_reason"] == (
                "PRE_PROVIDER_RuntimeError"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_pre_provider_cancellation_shields_safe_release():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/new"
        ),
        trace=trace,
    )
    store = _TestObjectStore(cancel_open=True)
    await _seed_ready_asset(sessions)
    service = _service(sessions, store, files, trace, shared)

    try:
        with pytest.raises(asyncio.CancelledError):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )

        assert files.calls == 0
        async with sessions() as session:
            rows = (
                await session.execute(
                    FileProviderBindingRecord.__table__.select()
                )
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["state"] == "ERROR"
            assert rows[0]["live_claim_token"] is None
            assert rows[0]["metadata_json"]["upload_outcome"] == (
                "SAFE_NO_REMOTE_COMMIT"
            )
            assert rows[0]["metadata_json"]["upload_outcome_reason"] == (
                "PRE_PROVIDER_CANCELLED"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_cancellation_persists_unknown_and_never_reuploads():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_outcome_unknown(),
        trace=trace,
        raise_cancel=True,
        shared=shared,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    service = _service(sessions, store, files, trace, shared)

    try:
        with pytest.raises(asyncio.CancelledError):
            await service.hydrate(
                owner_user_id="u-f5c",
                asset_id="asset-f5c",
                provider_name="gemini",
            )

        binding = await _live_binding(sessions)
        assert binding is not None
        assert binding.state == "UNKNOWN"
        assert binding.live_claim_token == "LIVE"
        assert (
            binding.metadata_json["upload_outcome"]
            == "REMOTE_OUTCOME_UNKNOWN"
        )
        assert binding.metadata_json["upload_outcome_reason"] == (
            "BOUNDARY_CANCELLED"
        )
        assert files.calls == 1

        result = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert result.status is HydrationStatus.HYDRATION_OUTCOME_UNKNOWN
        assert files.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5c_known_success_persistence_conflict_never_reenters_upload():
    engine, sessions = await _database()
    trace: list[str] = []
    shared = {"fail_active_transition": True}
    files = _FilesBoundary(
        outcome=ProviderUploadOutcome.remote_success_known(
            provider_file_id="files/known"
        ),
        trace=trace,
        shared=shared,
    )
    store = _TestObjectStore()
    await _seed_ready_asset(sessions)
    service = _service(sessions, store, files, trace, shared)

    try:
        first = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert (
            first.status
            is HydrationStatus.HYDRATION_PERSISTENCE_CONFLICT
        )
        assert first.provider_file_id == "files/known"
        assert shared["active_transition_failed"] is True
        assert files.calls == 1

        second = await service.hydrate(
            owner_user_id="u-f5c",
            asset_id="asset-f5c",
            provider_name="gemini",
        )
        assert second.status is HydrationStatus.HYDRATION_IN_PROGRESS
        assert files.calls == 1
    finally:
        await engine.dispose()


def test_f5c_remains_dormant_and_never_buffers_canonical_asset():
    source = __import__("pathlib").Path(
        "se/src/application/assets/hydration.py"
    ).read_text(encoding="utf-8")
    workflow = __import__("pathlib").Path(
        "se/src/runtimes/workflow/runtime.py"
    ).read_text(encoding="utf-8")

    assert "BytesIO" not in source
    assert "b''.join" not in source
    assert "self.object_store.open_stream(" in source
    assert "current.object_key" in source
    assert "upload_file_outcome(" not in workflow
    assert "CanonicalAssetHydrationService" not in workflow
    assert "ASSET_HYDRATION_REQUIRED" in workflow
