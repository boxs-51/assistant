from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.repositories.capability_publications import (
    CapabilityPublicationRepository,
    PublicationConflict,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)


PG_FLAG = "ASSISTANT_SECURITY_PG_E2E"
PG_DSN = "ASSISTANT_SKILL_PUBLICATION_DATABASE_URL"


def _require_pg() -> str:
    if os.environ.get(PG_FLAG) != "1":
        pytest.skip("real PostgreSQL Security evidence requires ASSISTANT_SECURITY_PG_E2E=1")
    dsn = str(os.environ.get(PG_DSN) or "").strip()
    assert dsn.startswith("postgresql+asyncpg://"), (
        f"{PG_DSN} must use postgresql+asyncpg:// when {PG_FLAG}=1"
    )
    return dsn


@pytest.fixture(scope="module", autouse=True)
def _migrated_publication_schema():
    _require_pg()
    command.upgrade(Config("alembic_skill_publications.ini"), "head")
    yield


def _definition(capability_id: str, instruction: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="Security P1A real PostgreSQL witness",
        kind=CapabilityKind.SKILL,
        execution_kind="SKILL",
        execution_mode=CapabilityExecutionMode.CONTEXT_ONLY,
        source="HTTP_CALLER",
        metadata={
            "kind": "SKILL",
            "instruction": instruction,
            "server_managed": False,
            "runtime_owned": True,
            "lazy": False,
            "loaded": True,
            "schema_version": "1",
            "skill_id": capability_id,
            "provenance": "HTTP_CALLER",
            "ownership": "CALLER_REGISTERED",
        },
    )


async def _authority(dsn: str):
    engine = create_async_engine(dsn, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    authority = CapabilityPublicationRepository(factory)
    await authority.ensure_ready()
    return engine, authority


@pytest.mark.asyncio
async def test_skill_publication_migration_head_and_table_exist():
    dsn = _require_pg()
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            head = (
                await connection.execute(
                    text(
                        "SELECT version_num FROM "
                        "skill_publications.alembic_version_skill_publications"
                    )
                )
            ).scalar_one()
            table = (
                await connection.execute(
                    text(
                        "SELECT to_regclass("
                        "'skill_publications.capability_publications')"
                    )
                )
            ).scalar_one()
        assert head == "skillpub_0001"
        assert table == "skill_publications.capability_publications"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_worker_owner_read_collision_restart_and_revoke():
    dsn = _require_pg()
    suffix = uuid.uuid4().hex
    capability_id = f"sec-p1a-worker-{suffix}"
    definition = _definition(capability_id, f"OWNER_ONLY_{suffix}")
    owner = SimpleNamespace(user_id=f"owner-{suffix}", scopes=set(), permissions=[])
    other = SimpleNamespace(user_id=f"other-{suffix}", scopes=set(), permissions=[])

    engine_a, worker_a = await _authority(dsn)
    engine_b, worker_b = await _authority(dsn)
    try:
        await worker_a.publish_user_context(
            definition,
            publisher_id=owner.user_id,
        )

        visible_owner = await worker_b.list_visible_definitions(owner)
        assert [item.capability_id for item in visible_owner if item.capability_id == capability_id] == [
            capability_id
        ]
        assert definition.metadata["instruction"] == next(
            item.metadata["instruction"]
            for item in visible_owner
            if item.capability_id == capability_id
        )
        assert all(
            item.capability_id != capability_id
            for item in await worker_b.list_visible_definitions(other)
        )

        with pytest.raises(PublicationConflict):
            await worker_b.publish_user_context(
                _definition(capability_id, "FOREIGN_REPLACEMENT"),
                publisher_id=other.user_id,
            )

        # A fresh engine/session pair models a worker restart with no local preload.
        engine_c, restarted_worker = await _authority(dsn)
        try:
            restarted = await restarted_worker.list_visible_definitions(owner)
            assert any(item.capability_id == capability_id for item in restarted)

            await worker_a.revoke_user_context(
                capability_id,
                publisher_id=owner.user_id,
            )
            assert all(
                item.capability_id != capability_id
                for item in await restarted_worker.list_visible_definitions(owner)
            )
        finally:
            await engine_c.dispose()
    finally:
        await engine_b.dispose()
        await engine_a.dispose()


@pytest.mark.asyncio
async def test_system_namespace_blocks_caller_and_materializes_public_skill():
    dsn = _require_pg()
    suffix = uuid.uuid4().hex
    capability_id = f"sec-p1a-system-{suffix}"
    definition = CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="Trusted server positive",
        kind=CapabilityKind.SKILL,
        execution_kind="SKILL",
        execution_mode=CapabilityExecutionMode.CONTEXT_ONLY,
        source="BUILTIN",
        metadata={
            "kind": "SKILL",
            "instruction": f"SERVER_PUBLIC_{suffix}",
            "server_managed": True,
            "runtime_owned": True,
            "provenance": "SERVER_SKILL_MANIFEST",
            "required_permissions": [],
        },
    )
    any_identity = SimpleNamespace(user_id=f"viewer-{suffix}", scopes=set(), permissions=[])

    engine_a, worker_a = await _authority(dsn)
    engine_b, worker_b = await _authority(dsn)
    try:
        await worker_a.reserve_system_namespace(definition)
        assert all(
            item.capability_id != capability_id
            for item in await worker_b.list_visible_definitions(any_identity)
        )

        with pytest.raises(PublicationConflict):
            await worker_b.publish_user_context(
                _definition(capability_id, "CALLER_TAKEOVER"),
                publisher_id=f"attacker-{suffix}",
            )

        await worker_a.publish_system_direct_context(definition)
        visible = await worker_b.list_visible_definitions(any_identity)
        published = next(item for item in visible if item.capability_id == capability_id)
        assert published.metadata["instruction"] == f"SERVER_PUBLIC_{suffix}"
    finally:
        await engine_b.dispose()
        await engine_a.dispose()
