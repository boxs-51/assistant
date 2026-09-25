from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, MetaData, Table, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.infrastructure.config.schemas import ProviderConfig
from se.src.infrastructure.storage.models.sql.assets import (
    FileBlobRecord,
    FileProviderBindingRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def _ready_asset(
    assets: AssetRepository,
    *,
    file_id: str = "asset-f5a",
    blob_id: str = "blob-f5a",
    sha256: str = "a" * 64,
):
    blob = await assets.create_blob(
        {
            "id": blob_id,
            "storage_backend": "local",
            "bucket": None,
            "object_key": f"objects/{blob_id}",
            "state": "READY",
            "size_bytes": 4,
            "sha256": sha256,
            "detected_mime_type": "text/plain",
        }
    )
    file_record = await assets.create_file(
        {
            "id": file_id,
            "owner_user_id": "u-f5a",
            "blob_id": blob.id,
            "filename": "f5a.txt",
            "mime_type": "text/plain",
            "extension": "txt",
            "origin_type": "USER_UPLOAD",
            "state": "READY",
        }
    )
    return file_record, blob


def test_f5a_provider_namespace_is_explicit_server_config():
    field = ProviderConfig.model_fields["file_binding_namespace"]
    assert field.default is None
    assert ProviderConfig(file_binding_namespace="gemini-project-a").file_binding_namespace == (
        "gemini-project-a"
    )
    with pytest.raises(ValidationError):
        ProviderConfig(file_binding_namespace="")


def test_f5a_binding_model_freezes_live_slot_and_unknown_state():
    table = FileProviderBindingRecord.__table__

    assert table.c.provider_file_id.nullable is True
    assert table.c.source_blob_id.nullable is True
    assert table.c.source_sha256.nullable is True
    assert table.c.live_claim_token.nullable is True

    live_slot = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_file_provider_bindings_live_slot"
    ]
    assert len(live_slot) == 1
    assert [column.name for column in live_slot[0].columns] == [
        "file_id",
        "provider_name",
        "provider_namespace",
        "live_claim_token",
    ]

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "UNKNOWN" in checks["ck_file_provider_bindings_state"]
    assert "provider_file_id IS NOT NULL" in checks[
        "ck_file_provider_bindings_active_provider_identity"
    ]
    assert "live_claim_token = 'LIVE'" in checks[
        "ck_file_provider_bindings_live_claim"
    ]


@pytest.mark.asyncio
async def test_f5a_processing_claim_fails_closed_without_explicit_namespace():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            await _ready_asset(assets)
            for namespace in (None, "", "default", "   "):
                with pytest.raises(
                    ValueError,
                    match="HYDRATION_PROVIDER_SCOPE_UNCONFIGURED",
                ):
                    await assets.try_create_processing_provider_binding(
                        file_id="asset-f5a",
                        owner_user_id="u-f5a",
                        provider_name="gemini",
                        provider_namespace=namespace,
                    )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5a_processing_claim_persists_fingerprint_and_single_live_slot():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            _, blob = await _ready_asset(assets)
            first, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            assert first.state == "PROCESSING"
            assert first.live_claim_token == "LIVE"
            assert first.provider_file_id is None
            assert first.source_blob_id == blob.id
            assert first.source_sha256 == blob.sha256
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            same, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is False
            assert same.id == first.id

            other, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-b",
            )
            assert created is True
            assert other.id != same.id
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5a_db_rejects_two_live_rows_but_historical_error_releases_slot():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            _, blob = await _ready_asset(assets)
            first, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            await session.commit()

        async with sessions() as session:
            session.add(
                FileProviderBindingRecord(
                    file_id="asset-f5a",
                    provider_name="gemini",
                    provider_namespace="gemini-project-a",
                    provider_file_id=None,
                    source_blob_id="blob-f5a",
                    source_sha256=blob.sha256,
                    live_claim_token="LIVE",
                    state="PROCESSING",
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        async with sessions() as session:
            assets = AssetRepository(session)
            stored = await assets.get_live_provider_binding(
                "asset-f5a",
                "gemini",
                provider_namespace="gemini-project-a",
            )
            assert stored is not None
            terminal = await assets.compare_and_set_provider_binding(
                stored.id,
                expected_revision=stored.revision,
                expected_state="PROCESSING",
                expected_live_claim_token="LIVE",
                values={"state": "ERROR", "live_claim_token": None},
            )
            assert terminal is not None
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            replacement, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            assert replacement.id != first.id
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5a_active_requires_identity_and_retirement_precedes_replacement():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            _, blob = await _ready_asset(assets)
            claim, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            claim = await assets.get_live_provider_binding(
                "asset-f5a",
                "gemini",
                provider_namespace="gemini-project-a",
            )
            assert claim is not None
            with pytest.raises(IntegrityError):
                await assets.compare_and_set_provider_binding(
                    claim.id,
                    expected_revision=claim.revision,
                    expected_state="PROCESSING",
                    expected_live_claim_token="LIVE",
                    values={"state": "ACTIVE"},
                )
            await session.rollback()

        async with sessions() as session:
            assets = AssetRepository(session)
            claim = await assets.get_live_provider_binding(
                "asset-f5a",
                "gemini",
                provider_namespace="gemini-project-a",
            )
            assert claim is not None
            active = await assets.compare_and_set_provider_binding(
                claim.id,
                expected_revision=claim.revision,
                expected_state="PROCESSING",
                expected_live_claim_token="LIVE",
                values={
                    "state": "ACTIVE",
                    "provider_file_id": "files/provider-f5a",
                    "provider_uri": "https://provider.invalid/f5a",
                },
            )
            assert active is not None
            canonical_blob = await assets.get_blob("blob-f5a")
            assert canonical_blob is not None
            assert AssetRepository.provider_binding_matches_blob(
                active,
                canonical_blob,
                now=datetime.now(timezone.utc),
            )
            mismatched_blob = FileBlobRecord(
                id="blob-other",
                storage_backend="local",
                object_key="objects/blob-other",
                state="READY",
                size_bytes=4,
                sha256="b" * 64,
            )
            assert not AssetRepository.provider_binding_matches_blob(
                active,
                mismatched_blob,
                now=datetime.now(timezone.utc),
            )
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            active = await assets.get_live_provider_binding(
                "asset-f5a",
                "gemini",
                provider_namespace="gemini-project-a",
            )
            assert active is not None
            retired = await assets.expire_active_provider_binding(
                active.id,
                expected_revision=active.revision,
            )
            assert retired is not None
            assert retired.state == "EXPIRED"
            assert retired.live_claim_token is None
            assert retired.provider_file_id == "files/provider-f5a"
            # The released contract requires this slot release to commit before
            # a replacement PROCESSING claim can be created.
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            replacement, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            assert replacement.provider_file_id is None
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f5a_unique_race_loser_rereads_winner_without_second_claim(monkeypatch):
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            await _ready_asset(assets)
            winner, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is True
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            original = assets.get_live_provider_binding
            calls = 0

            async def stale_then_real(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return None
                return await original(*args, **kwargs)

            monkeypatch.setattr(
                assets,
                "get_live_provider_binding",
                stale_then_real,
            )
            observed, created = await assets.try_create_processing_provider_binding(
                file_id="asset-f5a",
                owner_user_id="u-f5a",
                provider_name="gemini",
                provider_namespace="gemini-project-a",
            )
            assert created is False
            assert observed.id == winner.id
            assert calls >= 2
    finally:
        await engine.dispose()


def _legacy_binding_schema(metadata: MetaData) -> None:
    Table(
        "file_blobs",
        metadata,
        sa.Column("id", sa.String(255), primary_key=True),
    )
    Table(
        "files",
        metadata,
        sa.Column("id", sa.String(255), primary_key=True),
    )
    Table(
        "file_provider_bindings",
        metadata,
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "file_id",
            sa.String(255),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column(
            "provider_namespace",
            sa.String(255),
            nullable=False,
            server_default="default",
        ),
        sa.Column("provider_file_id", sa.String(1024), nullable=False),
        sa.Column("provider_uri", sa.String(2048), nullable=True),
        sa.Column(
            "state",
            sa.String(32),
            nullable=False,
            server_default="PROCESSING",
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_file_provider_bindings_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "state IN ('PROCESSING', 'ACTIVE', 'EXPIRED', 'DELETING', "
            "'DELETED', 'ERROR')",
            name="ck_file_provider_bindings_state",
        ),
        sa.UniqueConstraint(
            "provider_name",
            "provider_namespace",
            "provider_file_id",
            name="uq_file_provider_bindings_provider_identity",
        ),
    )


def test_f5a_migration_upgrades_and_downgrades_real_sqlite_schema():
    migration = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions."
        "20a_cas_f5_binding_foundation"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = MetaData()
    _legacy_binding_schema(metadata)
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO files (id) VALUES ('asset-a'), ('asset-b')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO file_provider_bindings "
                "(id, file_id, provider_name, provider_namespace, "
                "provider_file_id, state, revision) VALUES "
                "('binding-live', 'asset-a', 'gemini', 'ns-a', "
                "'files/a', 'ACTIVE', 0), "
                "('binding-old', 'asset-b', 'gemini', 'ns-a', "
                "'files/b', 'EXPIRED', 0)"
            )
        )

        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("file_provider_bindings")
        }
        assert columns["provider_file_id"]["nullable"] is True
        assert "source_blob_id" in columns
        assert "source_sha256" in columns
        assert "live_claim_token" in columns

        rows = connection.execute(
            sa.text(
                "SELECT id, live_claim_token "
                "FROM file_provider_bindings ORDER BY id"
            )
        ).all()
        assert rows == [
            ("binding-live", "LIVE"),
            ("binding-old", None),
        ]

        uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(
                "file_provider_bindings"
            )
        }
        assert uniques["uq_file_provider_bindings_live_slot"] == (
            "file_id",
            "provider_name",
            "provider_namespace",
            "live_claim_token",
        )

        migration.downgrade()
        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("file_provider_bindings")
        }
        assert columns["provider_file_id"]["nullable"] is False
        assert "source_blob_id" not in columns
        assert "source_sha256" not in columns
        assert "live_claim_token" not in columns

    engine.dispose()
