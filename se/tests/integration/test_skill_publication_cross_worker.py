from __future__ import annotations

import asyncio
import multiprocessing
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
    PublicationAuthorityUnavailable,
    PublicationConflict,
    PublicationStaleRevision,
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


def _spawned_publication_worker(connection, dsn: str) -> None:
    """Process-local SQLAlchemy engine and event loop, safe under Windows spawn."""
    async def serve():
        engine, authority = await _authority(dsn)
        try:
            connection.send({"ready": True, "pid": os.getpid()})
            while True:
                action, data = connection.recv()
                if action == "stop":
                    connection.send({"ok": True, "pid": os.getpid()})
                    return
                try:
                    if action == "publish":
                        await authority.publish_user_context(
                            _definition(data["capability_id"], data["instruction"]),
                            publisher_id=data["publisher_id"],
                        )
                        response = {"ok": True}
                    elif action == "revoke":
                        await authority.revoke_user_context(
                            data["capability_id"], publisher_id=data["publisher_id"]
                        )
                        response = {"ok": True}
                    elif action == "visible":
                        identity = SimpleNamespace(
                            user_id=data["user_id"], scopes=set(), permissions=[]
                        )
                        definitions = await authority.list_visible_definitions(identity)
                        visible = [
                            item for item in definitions
                            if item.capability_id == data["capability_id"]
                        ]
                        response = {
                            "ok": True,
                            "ids": [item.capability_id for item in visible],
                            "instructions": [
                                item.metadata.get("instruction") for item in visible
                            ],
                        }
                    else:
                        raise ValueError("Unrecognized worker command")
                except Exception as exc:
                    # Never include a DSN or SQL exception message in test output.
                    response = {"ok": False, "error": type(exc).__name__}
                connection.send({"pid": os.getpid(), **response})
        finally:
            await engine.dispose()

    try:
        asyncio.run(serve())
    except BaseException as exc:
        try:
            connection.send({
                "ready": False, "pid": os.getpid(), "error": type(exc).__name__
            })
        except (OSError, EOFError):
            pass
        raise SystemExit(1) from None
    finally:
        connection.close()


def _start_spawned_publication_worker(context, dsn: str):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_spawned_publication_worker,
        args=(child, dsn),
        name="security-p1a-real-pg-worker",
    )
    process.start()
    child.close()
    try:
        assert parent.poll(60), "Spawned worker readiness timeout"
        assert parent.recv() == {"ready": True, "pid": process.pid}, (
            "Spawned worker startup failure (sensitive details suppressed)"
        )
        assert process.pid != os.getpid()
    except BaseException:
        if process.is_alive():
            process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
        parent.close()
        raise
    return process, parent


def _request_spawned_worker(process, connection, action: str, **data):
    assert process.is_alive(), "Spawned worker exited before request"
    connection.send((action, data))
    assert connection.poll(45), "Spawned worker request timeout"
    answer = connection.recv()
    assert answer["pid"] == process.pid, "Worker reply not from expected PID"
    return answer


def _close_spawned_workers(workers):
    for process, connection in reversed(workers):
        try:
            if process.is_alive():
                try:
                    connection.send(("stop", {}))
                    if connection.poll(5):
                        connection.recv()
                except (OSError, EOFError, BrokenPipeError):
                    pass
                process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join(timeout=10)
        finally:
            connection.close()


def test_two_worker_owner_read_collision_restart_and_revoke():
    """A/B are real spawned OS processes; B is exited and a fresh C reads PG."""
    dsn = _require_pg()
    suffix = uuid.uuid4().hex
    capability_id = f"sec-p1a-worker-{suffix}"
    instruction = f"OWNER_ONLY_{suffix}"
    owner_id = f"owner-{suffix}"
    other_id = f"other-{suffix}"
    context = multiprocessing.get_context("spawn")
    workers = []
    try:
        process_a, channel_a = _start_spawned_publication_worker(context, dsn)
        workers.append((process_a, channel_a))
        process_b, channel_b = _start_spawned_publication_worker(context, dsn)
        workers.append((process_b, channel_b))
        assert process_a.pid != process_b.pid, "A and B share a PID"

        assert _request_spawned_worker(
            process_a, channel_a, "publish",
            capability_id=capability_id, instruction=instruction,
            publisher_id=owner_id,
        )["ok"]
        owner_view = _request_spawned_worker(
            process_b, channel_b, "visible",
            capability_id=capability_id, user_id=owner_id,
        )
        assert owner_view["ids"] == [capability_id]
        assert owner_view["instructions"] == [instruction]
        assert _request_spawned_worker(
            process_b, channel_b, "visible",
            capability_id=capability_id, user_id=other_id,
        )["ids"] == []

        foreign = _request_spawned_worker(
            process_b, channel_b, "publish",
            capability_id=capability_id, instruction="FOREIGN_REPLACEMENT",
            publisher_id=other_id,
        )
        assert foreign == {
            "pid": process_b.pid, "ok": False, "error": "PublicationConflict"
        }
        stale = _request_spawned_worker(
            process_b, channel_b, "publish",
            capability_id=capability_id, instruction="OWNER_CHANGED_WITHOUT_CAS",
            publisher_id=owner_id,
        )
        assert stale == {
            "pid": process_b.pid, "ok": False, "error": "PublicationStaleRevision"
        }

        # This closes the actual child PID, not merely one engine/session.
        assert _request_spawned_worker(process_b, channel_b, "stop")["ok"]
        process_b.join(timeout=10)
        assert process_b.exitcode == 0, "Child B did not terminate cleanly"

        process_c, channel_c = _start_spawned_publication_worker(context, dsn)
        workers.append((process_c, channel_c))
        assert process_c.pid != process_a.pid
        restarted_view = _request_spawned_worker(
            process_c, channel_c, "visible",
            capability_id=capability_id, user_id=owner_id,
        )
        assert restarted_view["ids"] == [capability_id]
        assert restarted_view["instructions"] == [instruction]

        assert _request_spawned_worker(
            process_a, channel_a, "revoke",
            capability_id=capability_id, publisher_id=owner_id,
        )["ok"]
        assert _request_spawned_worker(
            process_c, channel_c, "visible",
            capability_id=capability_id, user_id=owner_id,
        )["ids"] == []
        for publisher_id in (other_id, owner_id):
            denied = _request_spawned_worker(
                process_c, channel_c, "publish",
                capability_id=capability_id, instruction=instruction,
                publisher_id=publisher_id,
            )
            assert denied == {
                "pid": process_c.pid, "ok": False, "error": "PublicationConflict"
            }
    finally:
        _close_spawned_workers(workers)

@pytest.mark.asyncio
async def test_same_origin_concurrent_publish_is_idempotent():
    dsn = _require_pg()
    suffix = uuid.uuid4().hex
    capability_id = f"sec-p1a-idempotent-{suffix}"
    definition = _definition(capability_id, f"SAME_ORIGIN_{suffix}")
    publisher_id = f"owner-{suffix}"

    engine_a, worker_a = await _authority(dsn)
    engine_b, worker_b = await _authority(dsn)
    try:
        first, second = await asyncio.gather(
            worker_a.publish_user_context(definition, publisher_id=publisher_id),
            worker_b.publish_user_context(definition, publisher_id=publisher_id),
        )
        assert first.capability_id == capability_id
        assert second.capability_id == capability_id
        visible = await worker_b.list_visible_definitions(
            SimpleNamespace(user_id=publisher_id, scopes=set(), permissions=[])
        )
        assert sum(item.capability_id == capability_id for item in visible) == 1
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


async def _assert_provider_not_called_on_authority_fault(authority, identity):
    """Exercise the genuine DIRECT unsent-call boundary, with a provider spy."""
    from se.src.runtimes.chat.direct import DirectChatRuntime

    class _Runtime:
        async def get_available_capabilities(self, _identity, _profile):
            return []

        async def get_direct_context_skills(self, _identity):
            return await authority.list_visible_definitions(_identity)

    class _Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, _request):
            self.calls += 1
            raise AssertionError("provider must not be called on authority failure")

    provider = _Provider()
    direct = DirectChatRuntime(inference=provider, capability_runtime=_Runtime())
    with pytest.raises(PublicationAuthorityUnavailable):
        await direct.execute(
            messages=(),
            identity=identity,
            session_id="sec-p1b-authority-fault",
            model="offline",
        )
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_post_start_wrong_schema_head_blocks_provider_before_send():
    """Real PG: a changed Alembic head after startup cannot authorize Skill text."""
    dsn = _require_pg()
    engine, authority = await _authority(dsn)
    suffix = uuid.uuid4().hex
    identity = SimpleNamespace(user_id=f"owner-{suffix}", scopes=set(), permissions=[])
    definition = _definition(f"sec-p1b-head-{suffix}", "NEVER_SEND_ON_BAD_HEAD")
    try:
        await authority.publish_user_context(definition, publisher_id=identity.user_id)
        assert any(
            item.capability_id == definition.capability_id
            for item in await authority.list_visible_definitions(identity)
        )
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    "UPDATE skill_publications.alembic_version_skill_publications "
                    "SET version_num = :invalid WHERE version_num = :valid"
                ),
                {"invalid": "skillpub_unauthorized_head", "valid": "skillpub_0001"},
            )
            assert result.rowcount == 1
        try:
            await _assert_provider_not_called_on_authority_fault(authority, identity)
            with pytest.raises(PublicationAuthorityUnavailable):
                await authority.publish_user_context(
                    _definition(f"sec-p1b-head-new-{suffix}", "DENY_WRITES"),
                    publisher_id=identity.user_id,
                )
        finally:
            async with engine.begin() as connection:
                result = await connection.execute(
                    text(
                        "UPDATE skill_publications.alembic_version_skill_publications "
                        "SET version_num = :valid WHERE version_num = :invalid"
                    ),
                    {"valid": "skillpub_0001", "invalid": "skillpub_unauthorized_head"},
                )
                assert result.rowcount == 1
        assert any(
            item.capability_id == definition.capability_id
            for item in await authority.list_visible_definitions(identity)
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_post_start_missing_version_table_blocks_provider_before_send():
    """Real PG DDL loss: do not silently rely on startup readiness."""
    dsn = _require_pg()
    engine, authority = await _authority(dsn)
    suffix = uuid.uuid4().hex
    identity = SimpleNamespace(user_id=f"owner-{suffix}", scopes=set(), permissions=[])
    try:
        await authority.publish_user_context(
            _definition(f"sec-p1b-missing-head-{suffix}", "NEVER_SEND_ON_MISSING_SCHEMA"),
            publisher_id=identity.user_id,
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE skill_publications.alembic_version_skill_publications "
                    "RENAME TO alembic_version_skill_publications_fault"
                )
            )
        try:
            await _assert_provider_not_called_on_authority_fault(authority, identity)
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE skill_publications.alembic_version_skill_publications_fault "
                        "RENAME TO alembic_version_skill_publications"
                    )
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_post_start_postgres_transport_loss_blocks_provider_before_send():
    """Start against real PG, then inject a genuinely unreachable TCP endpoint."""
    from sqlalchemy.engine import make_url

    dsn = _require_pg()
    engine, authority = await _authority(dsn)
    suffix = uuid.uuid4().hex
    identity = SimpleNamespace(user_id=f"owner-{suffix}", scopes=set(), permissions=[])
    broken_engine = None
    original_factory = authority._session_factory
    try:
        await authority.publish_user_context(
            _definition(f"sec-p1b-db-loss-{suffix}", "NEVER_SEND_WHEN_PG_UNREACHABLE"),
            publisher_id=identity.user_id,
        )
        unreachable = make_url(dsn).set(host="127.0.0.1", port=1)
        broken_engine = create_async_engine(
            unreachable, pool_pre_ping=True, connect_args={"timeout": 1}
        )
        authority._session_factory = async_sessionmaker(
            broken_engine, expire_on_commit=False
        )
        await _assert_provider_not_called_on_authority_fault(authority, identity)
    finally:
        authority._session_factory = original_factory
        if broken_engine is not None:
            await broken_engine.dispose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_reservation_survives_local_catalog_bind_failure():
    """Execute the actual generic register route; inject failure after SQL fence."""
    from fastapi import HTTPException

    from se.src.runtimes.capability.contracts.implementation import (
        CapabilityExecutionLocation,
    )
    from se.src.transport.gateway.api.v1.capability_router import (
        register_capability,
    )

    dsn = _require_pg()
    engine, authority = await _authority(dsn)
    suffix = uuid.uuid4().hex
    capability_id = f"sec-p1b-failed-local-bind-{suffix}"
    definition = CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="Injected local catalog failure after durable reservation",
        kind=CapabilityKind.TOOL,
        execution_kind="TOOL",
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
    )
    identity = SimpleNamespace(user_id=f"owner-{suffix}", scopes=set(), permissions=[])

    class _FailingCatalog:
        def register_definition(self, _definition):
            raise ValueError("injected local catalog bind failure")

    class _Runtime:
        catalog = _FailingCatalog()

        async def reserve_caller_namespace(self, value, *, identity):
            return await authority.reserve_user_namespace(
                value, publisher_id=identity.user_id
            )

    container = SimpleNamespace(capability_runtime=_Runtime())
    body = SimpleNamespace(
        location=CapabilityExecutionLocation.SERVER,
        owner_id=identity.user_id,
        kind=CapabilityKind.TOOL,
        definition=definition,
    )
    try:
        with pytest.raises(HTTPException) as exc:
            await register_capability(body, identity, container)
        assert exc.value.status_code == 422
        assert all(
            item.capability_id != capability_id
            for item in await authority.list_visible_definitions(identity)
        )
        restarted_engine, restarted = await _authority(dsn)
        try:
            with pytest.raises(PublicationConflict):
                await restarted.reserve_user_namespace(
                    definition, publisher_id=f"another-{suffix}"
                )
            with pytest.raises(PublicationConflict):
                await restarted.publish_user_context(
                    _definition(capability_id, "INJECTED_CROSS_ORIGIN_TAKEOVER"),
                    publisher_id=f"another-{suffix}",
                )
        finally:
            await restarted_engine.dispose()
    finally:
        await engine.dispose()
