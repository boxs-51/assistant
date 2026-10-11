from __future__ import annotations

import asyncio
import multiprocessing
import os
import subprocess
import sys
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
    CapabilityEffect,
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

        # The original publisher A also exits: the successor must use only PG.
        assert _request_spawned_worker(process_a, channel_a, "stop")["ok"]
        process_a.join(timeout=10)
        assert process_a.exitcode == 0, "Original publisher did not exit cleanly"
        process_a2, channel_a2 = _start_spawned_publication_worker(context, dsn)
        workers.append((process_a2, channel_a2))

        process_c, channel_c = _start_spawned_publication_worker(context, dsn)
        workers.append((process_c, channel_c))
        assert process_c.pid != process_a2.pid
        restarted_view = _request_spawned_worker(
            process_c, channel_c, "visible",
            capability_id=capability_id, user_id=owner_id,
        )
        assert restarted_view["ids"] == [capability_id]
        assert restarted_view["instructions"] == [instruction]

        assert _request_spawned_worker(
            process_a2, channel_a2, "revoke",
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

        # Same principal/current content must replay across separate PG engines
        # regardless of effect-set insertion order; changed content must not.
        effect_id = f"sec-p1a-effect-set-{suffix}"
        with_effects_a = _definition(effect_id, f"EFFECT_SET_{suffix}").model_copy(
            update={"effects": {CapabilityEffect.WRITE, CapabilityEffect.READ}}
        )
        with_effects_b = _definition(effect_id, f"EFFECT_SET_{suffix}").model_copy(
            update={"effects": {CapabilityEffect.READ, CapabilityEffect.WRITE}}
        )
        first_effect_row = await worker_a.publish_user_context(
            with_effects_a, publisher_id=publisher_id
        )
        replay_effect_row = await worker_b.publish_user_context(
            with_effects_b, publisher_id=publisher_id
        )
        assert first_effect_row.revision == replay_effect_row.revision == 1
        with pytest.raises(PublicationStaleRevision):
            await worker_b.publish_user_context(
                with_effects_b.model_copy(
                    update={"metadata": {
                        **with_effects_b.metadata, "instruction": "CHANGED_WITHOUT_CAS"
                    }}
                ), publisher_id=publisher_id,
            )

        # Distinct Python hash seeds exercise genuine cross-process set ordering.
        seed_script = (
            "from se.src.runtimes.capability.contracts.definition import "
            "CapabilityDefinition, CapabilityEffect; "
            "from se.src.infrastructure.storage.repositories.capability_publications "
            "import _canonical_definition, _digest; "
            "definition = CapabilityDefinition(id='seed-check', name='seed-check', "
            "description='seed-check', effects={CapabilityEffect.READ, "
            "CapabilityEffect.WRITE, CapabilityEffect.EXECUTE, "
            "CapabilityEffect.EXTERNAL_SIDE_EFFECT, CapabilityEffect.PRIVILEGED}); "
            "print(_digest(_canonical_definition(definition)))"
        )
        seed_digests = {
            subprocess.check_output(
                [sys.executable, "-c", seed_script],
                env={**os.environ, "PYTHONHASHSEED": str(seed)},
                text=True, timeout=30,
            ).strip()
            for seed in (1, 2, 19, 73)
        }
        assert len(seed_digests) == 1

        # Different authenticated USER origins compete for one immutable ID.
        # Independent engines and simultaneous start force real PG unique-row
        # arbitration; submission order is reversed on the second round.
        for reverse in (False, True):
            race_id = f"sec-p1a-user-race-{suffix}-{int(reverse)}"
            owners = (f"owner-a-{suffix}", f"owner-b-{suffix}")
            instructions = (f"WIN_A_{suffix}", f"WIN_B_{suffix}")
            contenders = [
                (worker_a, owners[0], instructions[0]),
                (worker_b, owners[1], instructions[1]),
            ]
            if reverse:
                contenders.reverse()
            start = asyncio.Event()
            entered = 0

            async def contender(worker, owner_id, instruction):
                nonlocal entered
                entered += 1
                if entered == 2:
                    start.set()
                await asyncio.wait_for(start.wait(), timeout=10)
                return await worker.publish_user_context(
                    _definition(race_id, instruction), publisher_id=owner_id
                )

            outcomes = await asyncio.wait_for(
                asyncio.gather(
                    *(contender(*item) for item in contenders),
                    return_exceptions=True,
                ),
                timeout=30,
            )
            winners = [
                item for item, result in zip(contenders, outcomes)
                if not isinstance(result, BaseException)
            ]
            losers = [result for result in outcomes if isinstance(result, BaseException)]
            assert len(winners) == 1 and len(losers) == 1, outcomes
            assert isinstance(losers[0], PublicationConflict), type(losers[0]).__name__
            winner_worker, winner_id, winner_instruction = winners[0]
            async with engine_a.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT publisher_type, publisher_id, origin_class, purpose, "
                            "revision, state, instruction FROM "
                            "skill_publications.capability_publications "
                            "WHERE capability_id = :capability_id"
                        ),
                        {"capability_id": race_id},
                    )
                ).one()
            assert tuple(row) == (
                "USER", winner_id, "CALLER_HTTP", "DIRECT_CONTEXT",
                1, "ACTIVE", winner_instruction,
            )
            for identity in owners:
                visible = await worker_b.list_visible_definitions(
                    SimpleNamespace(user_id=identity, scopes=set(), permissions=[])
                )
                matches = [item for item in visible if item.capability_id == race_id]
                assert len(matches) == int(identity == winner_id)
                if matches:
                    assert matches[0].metadata["instruction"] == winner_instruction
            loser_id = next(owner for owner in owners if owner != winner_id)
            with pytest.raises(PublicationConflict):
                await winner_worker.publish_user_context(
                    _definition(race_id, "MUST_NOT_REPLACE_WINNER"),
                    publisher_id=loser_id,
                )
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

        # Opposing trusted SYSTEM and caller USER originate from separate real
        # PG engines. Neither origin may replace whichever won the unique ID.
        for reverse in (False, True):
            race_id = f"sec-p1a-system-user-race-{suffix}-{int(reverse)}"
            system_definition = definition.model_copy(
                update={"id": race_id, "name": race_id}
            )
            user_instruction = f"UNTRUSTED_{suffix}_{int(reverse)}"
            user_definition = _definition(race_id, user_instruction)
            user_id = f"race-caller-{suffix}"
            start = asyncio.Event()
            entered = 0

            async def system_contender():
                nonlocal entered
                entered += 1
                if entered == 2:
                    start.set()
                await asyncio.wait_for(start.wait(), timeout=10)
                return await worker_a.reserve_system_namespace(system_definition)

            async def user_contender():
                nonlocal entered
                entered += 1
                if entered == 2:
                    start.set()
                await asyncio.wait_for(start.wait(), timeout=10)
                return await worker_b.publish_user_context(
                    user_definition, publisher_id=user_id
                )

            calls = (user_contender, system_contender) if reverse else (
                system_contender, user_contender
            )
            outcomes = await asyncio.wait_for(
                asyncio.gather(*(call() for call in calls), return_exceptions=True),
                timeout=30,
            )
            winners = [result for result in outcomes if not isinstance(result, BaseException)]
            losers = [result for result in outcomes if isinstance(result, BaseException)]
            assert len(winners) == 1 and len(losers) == 1, outcomes
            assert isinstance(losers[0], PublicationConflict), type(losers[0]).__name__
            async with engine_a.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT publisher_type, publisher_id, origin_class, purpose, "
                            "revision, state, instruction FROM "
                            "skill_publications.capability_publications "
                            "WHERE capability_id = :capability_id"
                        ),
                        {"capability_id": race_id},
                    )
                ).one()
            assert row.revision == 1 and row.state == "ACTIVE"
            if row.publisher_type == "SYSTEM":
                assert row.origin_class == "SYSTEM_BOOTSTRAP"
                assert row.purpose == "NAMESPACE_RESERVATION"
                assert row.instruction is None
                with pytest.raises(PublicationConflict):
                    await worker_b.publish_user_context(
                        user_definition, publisher_id=user_id
                    )
                with pytest.raises(PublicationConflict):
                    await worker_b.reserve_user_namespace(
                        user_definition, publisher_id=user_id
                    )
                assert not any(
                    item.capability_id == race_id
                    for item in await worker_b.list_visible_definitions(any_identity)
                )
            else:
                assert row.publisher_type == "USER" and row.publisher_id == user_id
                assert row.origin_class == "CALLER_HTTP"
                assert row.purpose == "DIRECT_CONTEXT"
                assert row.instruction == user_instruction
                with pytest.raises(PublicationConflict):
                    await worker_a.reserve_system_namespace(system_definition)
                assert all(
                    item.capability_id != race_id
                    for item in await worker_a.list_visible_definitions(any_identity)
                )
                caller_identity = SimpleNamespace(
                    user_id=user_id, scopes=set(), permissions=[]
                )
                assert any(
                    item.capability_id == race_id
                    for item in await worker_b.list_visible_definitions(caller_identity)
                )
        reservation = await worker_a.reserve_system_namespace(definition)
        assert reservation.revision == 1
        assert all(
            item.capability_id != capability_id
            for item in await worker_b.list_visible_definitions(any_identity)
        )

        with pytest.raises(PublicationConflict):
            await worker_b.publish_user_context(
                _definition(capability_id, "CALLER_TAKEOVER"),
                publisher_id=f"attacker-{suffix}",
            )

        # Explicit expected-revision CAS: a stale trusted materialization
        # cannot promote or mutate the canonical reservation.
        with pytest.raises(PublicationStaleRevision):
            await worker_a.publish_system_direct_context(
                definition, expected_revision=reservation.revision - 1
            )
        with pytest.raises(TypeError):
            await worker_a.publish_system_direct_context(definition)
        async with engine_b.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT purpose, revision, visibility, instruction "
                        "FROM skill_publications.capability_publications "
                        "WHERE capability_id = :capability_id"
                    ),
                    {"capability_id": capability_id},
                )
            ).one()
        assert tuple(row) == ("NAMESPACE_RESERVATION", 1, "NONE", None)
        published_record = await worker_a.publish_system_direct_context(
            definition, expected_revision=reservation.revision
        )
        assert published_record.revision == reservation.revision + 1
        with pytest.raises(PublicationStaleRevision):
            await worker_b.publish_system_direct_context(
                definition, expected_revision=reservation.revision
            )
        same_origin = await worker_b.publish_system_direct_context(
            definition, expected_revision=published_record.revision
        )
        assert same_origin.revision == published_record.revision
        changed_definition = definition.model_copy(
            update={"metadata": {
                **definition.metadata, "instruction": "MUST_NOT_REPLACE_SERVER_PUBLIC"
            }}
        )
        with pytest.raises(PublicationStaleRevision):
            await worker_b.publish_system_direct_context(
                changed_definition, expected_revision=same_origin.revision
            )
        visible = await worker_b.list_visible_definitions(any_identity)
        published = next(item for item in visible if item.capability_id == capability_id)
        assert published.metadata["instruction"] == f"SERVER_PUBLIC_{suffix}"
        async with engine_b.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT purpose, revision, visibility, instruction "
                        "FROM skill_publications.capability_publications "
                        "WHERE capability_id = :capability_id"
                    ),
                    {"capability_id": capability_id},
                )
            ).one()
        assert tuple(row) == (
            "DIRECT_CONTEXT", 2, "SERVER_PUBLIC", f"SERVER_PUBLIC_{suffix}"
        )

        # Stage-A P1-L0/L1: actual manifest discovery and authorized AGENT
        # skill.load may materialize a private body, but MUST NOT mint PUBLIC.
        from pathlib import Path
        from se.src.application.policy.authorization import AuthorizationService
        from se.src.domain.schemas.identity import Identity
        from se.src.runtimes.capability.catalog import CapabilityCatalog
        from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
        from se.src.runtimes.capability.local_support_loader import (
            LocalSupportLoader, SkillLoadDriver,
        )
        from se.src.runtimes.capability.runtime import CapabilityRuntime

        authorization = AuthorizationService()
        runtime_a = CapabilityRuntime(
            catalog=CapabilityCatalog(), authorization=authorization,
            publication_authority=worker_a,
        )
        loader = LocalSupportLoader(
            SimpleNamespace(
                capability_runtime=runtime_a,
                authorization_service=authorization,
            ),
            Path(__file__).resolve().parents[3] / "skills" / "v1",
            Path(__file__).resolve().parents[3] / "agents" / "__p1a_no_agents__",
        )
        discovered = set(loader.discover()["skills"])
        builtin_ids = {"skill-command-safety", "skill-web-research"}
        assert builtin_ids.issubset(discovered), (
            "Expected installed trusted manifest Skills were not discovered"
        )
        agent_identity = Identity(auth_type="jwt", user_id=f"agent-{suffix}")
        context = CapabilityExecutionContext.create(
            identity=agent_identity, execution_id=f"agent-exec-{suffix}",
            caller_agent_execution_id=f"agent-exec-{suffix}",
        )
        load_driver = SkillLoadDriver(
            runtime_a.catalog.get_definition("skill.load"), loader
        )
        private_agent_instructions = []
        for builtin_id in sorted(builtin_ids):
            discovered_definition = runtime_a.catalog.get_definition(builtin_id)
            assert discovered_definition.metadata["lazy"] is True
            assert discovered_definition.metadata["loaded"] is False
            assert not discovered_definition.metadata.get("instruction")
            namespace = await worker_a.reserve_system_namespace(
                discovered_definition
            )
            assert (namespace.purpose, namespace.visibility, namespace.instruction) == (
                "NAMESPACE_RESERVATION", "NONE", None
            )
            assert await worker_b.get_visible_definition(
                builtin_id, agent_identity
            ) is None
            loaded = await load_driver.execute(
                context, {"skill_id": builtin_id}
            )
            assert loaded["instruction"].strip()
            private_agent_instructions.append(loaded["instruction"])
            assert loader.is_loaded(builtin_id)
            # A local AGENT load does not change the durable SQL record.
            async with engine_b.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT purpose, visibility, instruction, revision "
                            "FROM skill_publications.capability_publications "
                            "WHERE capability_id = :capability_id"
                        ),
                        {"capability_id": builtin_id},
                    )
                ).one()
            assert tuple(row) == ("NAMESPACE_RESERVATION", "NONE", None, 1)
            assert await worker_b.get_visible_definition(
                builtin_id, agent_identity
            ) is None
        # A fresh SQL engine simulates cold reader/restart (not an OS crash).
        cold_engine, cold_authority = await _authority(dsn)
        try:
            cold_runtime = CapabilityRuntime(
                catalog=runtime_a.catalog, authorization=authorization,
                publication_authority=cold_authority,
            )
            assert not builtin_ids.intersection(
                item.capability_id
                for item in await cold_runtime.get_direct_context_skills(
                    agent_identity
                )
            )
        finally:
            await cold_engine.dispose()

        # Stage-A P1-POS: explicit SERVER publisher seam, never the load path.
        # SQL listing is origin/visibility only; actual DIRECT applies ACL.
        protected_id = f"sec-p1a-scoped-system-{suffix}"
        protected = definition.model_copy(
            update={
                "id": protected_id, "name": protected_id,
                "require_auth": True,
                "required_scopes": ["skill.public.read"],
                "metadata": {
                    **definition.metadata,
                    "instruction": f"SCOPED_PUBLIC_{suffix}",
                    "loaded": True,
                    "required_permissions": ["skill.public.access"],
                },
            }
        )
        protected_namespace = await worker_a.reserve_system_namespace(protected)
        with pytest.raises(PublicationConflict):
            await worker_a.publish_system_direct_context(
                protected.model_copy(
                    update={"metadata": {**protected.metadata, "instruction": ""}}
                ),
                expected_revision=protected_namespace.revision,
            )
        protected_published = await worker_a.publish_system_direct_context(
            protected, expected_revision=protected_namespace.revision
        )
        assert protected_published.revision == protected_namespace.revision + 1
        allowed = Identity(
            auth_type="jwt", user_id=f"allowed-{suffix}",
            scopes={"skill.public.read"}, permissions=["skill.public.access"],
        )
        denied = Identity(auth_type="jwt", user_id=f"denied-{suffix}")
        direct_runtime = CapabilityRuntime(
            catalog=runtime_a.catalog, authorization=authorization,
            publication_authority=worker_b,
        )
        allowed_skills = await direct_runtime.get_direct_context_skills(allowed)
        actual = next(
            item for item in allowed_skills
            if item.capability_id == protected_id
        )
        assert actual.metadata["instruction"] == f"SCOPED_PUBLIC_{suffix}"
        assert actual.require_auth and actual.required_scopes == ["skill.public.read"]
        assert actual.metadata["required_permissions"] == ["skill.public.access"]
        assert protected_id not in {
            item.capability_id
            for item in await direct_runtime.get_direct_context_skills(denied)
        }
        # SQL record-level public visibility does not override DIRECT ACL.
        assert await worker_b.get_visible_definition(protected_id, denied) is not None

        # PR440 P1A Stage-A compatibility contract (choice A):
        # Legacy no-SQL Gateway GET exposes authorized lazy *descriptors*;
        # SQL Gateway GET exposes only explicitly published DIRECT_CONTEXT.
        # AGENT may discover authorized descriptors, but neither discovery
        # nor skill.load grants a public SQL/DIRECT instruction publication.
        # Drive the actual GET routes, not repository-only methods.
        import httpx
        from fastapi import FastAPI
        from se.src.runtimes.capability.policy import CapabilityAccessProfile
        from se.src.runtimes.agent.contracts.inference import (
            InferenceMessage, InferenceResponse,
        )
        from se.src.runtimes.chat.direct import DirectChatRuntime
        from se.src.transport.gateway.api.v1 import capability_router
        from se.src.transport.gateway.authentication.dependency import (
            get_current_identity,
        )
        from se.src.transport.gateway.dependencies import get_container

        async def _gateway_snapshot(active_runtime, viewer, ids):
            app = FastAPI()
            app.include_router(capability_router.router)
            app.dependency_overrides[get_container] = lambda: SimpleNamespace(
                capability_runtime=active_runtime,
            )
            app.dependency_overrides[get_current_identity] = lambda: viewer
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                listed = await client.get(
                    "/v1/capabilities/", params={"kind": "SKILL"}
                )
                assert listed.status_code == 200, listed.text
                details = {}
                for capability_id in ids:
                    response = await client.get(
                        f"/v1/capabilities/{capability_id}"
                    )
                    assert response.status_code in (200, 404), response.text
                    details[capability_id] = response
                return listed.json(), details

        legacy_runtime = CapabilityRuntime(
            catalog=runtime_a.catalog, authorization=authorization,
        )
        legacy_list, legacy_details = await _gateway_snapshot(
            legacy_runtime, agent_identity, sorted(builtin_ids)
        )
        legacy_by_id = {
            item["capability_id"]: item for item in legacy_list
        }
        for builtin_id in builtin_ids:
            assert builtin_id in legacy_by_id
            assert legacy_details[builtin_id].status_code == 200
            descriptor = legacy_details[builtin_id].json()["definition"]
            assert descriptor["metadata"]["lazy"] is True
            assert descriptor["metadata"]["loaded"] is False
            assert not descriptor["metadata"].get("instruction")

        sql_list, sql_details = await _gateway_snapshot(
            direct_runtime, agent_identity, sorted(builtin_ids)
        )
        assert not builtin_ids.intersection(
            item["capability_id"] for item in sql_list
        )
        assert all(
            sql_details[builtin_id].status_code == 404
            for builtin_id in builtin_ids
        )
        agent_visible = {
            item.capability_id for item in
            await runtime_a.get_available_capabilities(
                agent_identity, CapabilityAccessProfile.AGENT_POLICY
            )
        }
        assert builtin_ids.issubset(agent_visible)
        assert not builtin_ids.intersection(
            item.capability_id
            for item in await direct_runtime.get_direct_context_skills(
                agent_identity
            )
        )

        # Explicitly restricted server descriptor remains AGENT-only for
        # authorized principals, without a PG publication or DIRECT bypass.
        restricted_id = f"sec-p1a-agent-only-{suffix}"
        original = runtime_a.catalog.get_definition(
            "skill-command-safety"
        )
        runtime_a.catalog.register_definition(original.model_copy(update={
            "id": restricted_id,
            "name": restricted_id,
            "require_auth": True,
            "required_scopes": ["skill.public.read"],
            "metadata": {
                **original.metadata,
                "required_permissions": ["skill.public.access"],
            },
        }))
        assert restricted_id in {
            item.capability_id for item in
            await runtime_a.get_available_capabilities(
                allowed, CapabilityAccessProfile.AGENT_POLICY
            )
        }
        assert restricted_id not in {
            item.capability_id for item in
            await runtime_a.get_available_capabilities(
                denied, CapabilityAccessProfile.AGENT_POLICY
            )
        }

        permitted_list, permitted_details = await _gateway_snapshot(
            direct_runtime, allowed, [protected_id, restricted_id]
        )
        assert protected_id in {
            item["capability_id"] for item in permitted_list
        }
        assert permitted_details[protected_id].status_code == 200
        assert permitted_details[protected_id].json()["definition"][
            "metadata"
        ]["instruction"] == f"SCOPED_PUBLIC_{suffix}"
        assert permitted_details[restricted_id].status_code == 404

        forbidden_list, forbidden_details = await _gateway_snapshot(
            direct_runtime, denied, [protected_id, restricted_id]
        )
        assert protected_id not in {
            item["capability_id"] for item in forbidden_list
        }
        assert forbidden_details[protected_id].status_code == 404
        assert forbidden_details[restricted_id].status_code == 404

        class _CaptureProvider:
            def __init__(self):
                self.requests = []

            async def complete(self, request):
                self.requests.append(request)
                return InferenceResponse(
                    request_id=request.request_id,
                    execution_id=request.execution_id,
                    iteration=request.iteration,
                    message=InferenceMessage(role="assistant", content="ok"),
                    provider="offline", model="offline",
                )

        provider = _CaptureProvider()
        chat = DirectChatRuntime(
            inference=provider, capability_runtime=direct_runtime,
        )
        for viewer, expected_public in (
            (allowed, True), (denied, False), (agent_identity, False)
        ):
            await chat.execute(
                messages=(), identity=viewer,
                session_id=f"sec-p1a-gateway-{suffix}", model="offline",
            )
            system_text = provider.requests[-1].messages[0].content
            assert (f"SCOPED_PUBLIC_{suffix}" in system_text) is expected_public
            assert all(
                instruction not in system_text
                for instruction in private_agent_instructions
            )
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
