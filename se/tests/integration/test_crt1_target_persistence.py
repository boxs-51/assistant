from __future__ import annotations

from pathlib import Path

import pytest
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
from se.src.runtimes.capability.contracts.invocation import CapabilityInvocation
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


def test_crt1_migration_is_single_linear_nullable_target_child(tmp_path: Path):
    script = ScriptDirectory.from_config(_config(tmp_path / "unused.sqlite"))
    assert script.get_heads() == [CRT1_REVISION]
    assert script.get_revision(CRT1_REVISION).down_revision == PREVIOUS_REVISION


@pytest.mark.asyncio
async def test_crt1_sql_target_round_trip_and_immutability():
    driver = SQLiteDriver(
        DriverConfig(enabled=True, required=True, options={"path": ":memory:"})
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    store = SqlCapabilityInvocationStore(lambda: SqlAlchemyUnitOfWork(driver))
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        resource_ref="desktop:primary",
        stable_client_id="client-a",
        fallback_policy=FallbackPolicy.NONE,
    )
    fingerprint = capability_request_fingerprint(
        capability_id="desktop.echo",
        capability_version="1.0",
        arguments={"value": "x"},
        target=target,
    )
    item = CapabilityInvocation(
        invocation_id="inv-crt1-sql",
        capability_id="desktop.echo",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint=fingerprint,
        target=target,
        arguments={"value": "x"},
    )
    await store.create(item)

    loaded = await store.get(item.invocation_id)
    assert loaded is not None
    assert loaded.target == target
    assert loaded.request_fingerprint == fingerprint

    mutated_target = target.model_copy(
        update={"stable_client_id": "client-b"}
    )
    changed = loaded.model_copy(
        update={
            "target": mutated_target,
            "request_fingerprint": capability_request_fingerprint(
                capability_id=loaded.capability_id,
                capability_version=loaded.capability_version,
                arguments=loaded.arguments,
                target=mutated_target,
            ),
            "revision": loaded.revision + 1,
        }
    )
    assert await store.compare_and_set(
        changed,
        expected_revision=loaded.revision,
    ) is False

    await driver.disconnect()
