from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
)
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint


ROOT = Path(__file__).resolve().parents[3]
CRT1_REVISION = "29a_crt1_capability_invocation_target"
PREVIOUS_REVISION = "28a_tbo1_task_policy_representation"


def _config(database: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    return config


@pytest.mark.asyncio
async def test_target_round_trips_through_sql_create_read_and_cas(tmp_path):
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": str(tmp_path / "crt1-target.sqlite3")},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SqlCapabilityInvocationStore(
        lambda: SqlAlchemyUnitOfWork(driver)
    )
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        resource_ref="client://client-a/report.txt",
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    fingerprint = capability_request_fingerprint(
        capability_id="crt.persist",
        capability_version="1.0",
        arguments={"path": "report.txt"},
        target=target,
    )
    item = CapabilityInvocation(
        invocation_id="inv-crt-persist",
        capability_id="crt.persist",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint=fingerprint,
        target=target,
        owner_user_id="user-crt",
        origin_client_id="client-a",
        state=CapabilityInvocationState.CREATED,
        arguments={"path": "report.txt"},
    )
    try:
        await store.create(item)
        loaded = await store.get(item.invocation_id)
        assert loaded is not None
        assert loaded.target == target
        assert loaded.request_fingerprint == fingerprint

        loaded.state = CapabilityInvocationState.DISPATCHING
        loaded.revision = 1
        assert await store.compare_and_set(loaded, expected_revision=0) is True
        reloaded = await store.get(item.invocation_id)
        assert reloaded is not None
        assert reloaded.target == target
        assert reloaded.revision == 1

        mutated_target = target.model_copy(
            update={"stable_client_id": "client-b"}
        )
        mutated = reloaded.model_copy(
            update={
                "target": mutated_target,
                "request_fingerprint": capability_request_fingerprint(
                    capability_id=reloaded.capability_id,
                    capability_version=reloaded.capability_version,
                    arguments=reloaded.arguments,
                    target=mutated_target,
                ),
                "revision": 2,
            }
        )
        assert await store.compare_and_set(
            mutated,
            expected_revision=1,
        ) is False
        unchanged = await store.get(item.invocation_id)
        assert unchanged is not None
        assert unchanged.target == target
        assert unchanged.revision == 1
    finally:
        await driver.disconnect()


def test_crt1_migration_is_single_linear_nullable_no_backfill_and_reversible(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "crt1-migration.sqlite3"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    script = ScriptDirectory.from_config(config)
    assert (
        len(script.get_heads()) == 1
        and (
            mh0_chain := tuple(
                script.walk_revisions(base="base", head=script.get_heads()[0])
            )
        )
        and mh0_chain[-1].down_revision is None
        and all(
            mh0_chain[i].down_revision == mh0_chain[i + 1].revision
            for i in range(len(mh0_chain) - 1)
        )
        and "30a_ctx_f5_user_wide_memory_scope" in {
            item.revision for item in mh0_chain
        }
    )
    assert script.get_revision(CRT1_REVISION).down_revision == PREVIOUS_REVISION

    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        before = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(capability_invocations)"
            )
        }
        assert "target_json" not in before

    command.upgrade(config, CRT1_REVISION)
    with sqlite3.connect(database) as connection:
        after = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(capability_invocations)"
            )
        }
        assert "target_json" in after
        rows = connection.execute(
            "SELECT target_json FROM capability_invocations"
        ).fetchall()
        assert rows == []

    command.downgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        downgraded = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(capability_invocations)"
            )
        }
        assert "target_json" not in downgraded
