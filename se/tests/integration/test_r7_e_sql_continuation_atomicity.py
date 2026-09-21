from __future__ import annotations

import asyncio

import pytest

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
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.invocation import CapabilityInvocationLifecycle


def _invocation(invocation_id: str) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id="tool.r7e.sql",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint="f" * 64,
        owner_user_id="user-r7e",
        origin_client_id="client-r7e",
        remote_outcome_state=RemoteOutcomeState.OUTCOME_UNKNOWN,
        implementation_id="conn-k1:tool.r7e.sql",
        driver_kind="REMOTE_CLIENT",
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        execution_id="exec-r7e",
        tool_call_id="call-r7e",
        connection_id="conn-k1",
        attempt=1,
        max_attempts=1,
        arguments={"value": "x"},
    )


async def _sql_lifecycle(tmp_path):
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": str(tmp_path / "r7e-continuation.sqlite3")},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    uow_factory = lambda: SqlAlchemyUnitOfWork(driver)
    store = SqlCapabilityInvocationStore(uow_factory)
    return driver, store, CapabilityInvocationLifecycle(store)


async def _seed_attempt_one(store, invocation_id: str):
    await store.save_attempt(
        CapabilityInvocationAttempt(
            attempt_id=f"att-{invocation_id}-1",
            invocation_id=invocation_id,
            attempt_number=1,
            implementation_id="conn-k1:tool.r7e.sql",
            driver_kind="REMOTE_CLIENT",
            connection_id="conn-k1",
            state=CapabilityInvocationState.FAILED,
        )
    )


@pytest.mark.asyncio
async def test_r7_e_sql_two_continuations_have_one_cas_winner_and_no_orphan(tmp_path):
    driver, store, lifecycle = await _sql_lifecycle(tmp_path)
    try:
        original = _invocation("inv-r7e-race")
        await store.create(original)
        await _seed_attempt_one(store, original.invocation_id)

        first = await store.get(original.invocation_id)
        second = await store.get(original.invocation_id)
        assert first is not None and second is not None

        async def begin(snapshot):
            return await lifecycle.begin_continuation_attempt(
                snapshot,
                implementation_id="conn-k2:tool.r7e.sql",
                driver_kind="REMOTE_CLIENT",
                connection_id="conn-k2",
                continuation_mode="REPLAY_SAFE",
            )

        results = await asyncio.gather(
            begin(first),
            begin(second),
            return_exceptions=True,
        )

        winners = [item for item in results if isinstance(item, tuple)]
        losers = [item for item in results if isinstance(item, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], RuntimeError)

        persisted = await store.get(original.invocation_id)
        attempts = await store.list_attempts(original.invocation_id)
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.DISPATCHING
        assert persisted.revision == original.revision + 1
        assert persisted.attempt == 2
        assert persisted.max_attempts == 2
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert len({item.attempt_id for item in attempts}) == 2
        assert attempts[-1].metadata["continuation_mode"] == "REPLAY_SAFE"
        assert attempts[-1].metadata["source_revision"] == original.revision
    finally:
        await driver.disconnect()


@pytest.mark.asyncio
async def test_r7_e_sql_attempt_high_water_conflict_rolls_back_invocation_cas(tmp_path):
    driver, store, lifecycle = await _sql_lifecycle(tmp_path)
    try:
        original = _invocation("inv-r7e-high-water")
        await store.create(original)
        # Deliberately omit attempt #1 while the invocation says attempt=1.

        loaded = await store.get(original.invocation_id)
        assert loaded is not None
        with pytest.raises(RuntimeError, match="continuation rejected"):
            await lifecycle.begin_continuation_attempt(
                loaded,
                implementation_id="conn-k2:tool.r7e.sql",
                driver_kind="REMOTE_CLIENT",
                connection_id="conn-k2",
                continuation_mode="REPLAY_SAFE",
            )

        persisted = await store.get(original.invocation_id)
        attempts = await store.list_attempts(original.invocation_id)
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.WAITING
        assert persisted.wait_reason is CapabilityWaitReason.CONNECTION
        assert persisted.revision == original.revision
        assert persisted.attempt == 1
        assert attempts == []
    finally:
        await driver.disconnect()



@pytest.mark.asyncio
async def test_r7_e_sql_start_transition_updates_invocation_and_attempt_atomically(tmp_path):
    driver, store, lifecycle = await _sql_lifecycle(tmp_path)
    try:
        original = _invocation("inv-r7e-start")
        await store.create(original)
        await _seed_attempt_one(store, original.invocation_id)
        loaded = await store.get(original.invocation_id)
        assert loaded is not None

        dispatching, attempt = await lifecycle.begin_continuation_attempt(
            loaded,
            implementation_id="conn-k2:tool.r7e.sql",
            driver_kind="REMOTE_CLIENT",
            connection_id="conn-k2",
            continuation_mode="REPLAY_SAFE",
        )
        running, running_attempt = await lifecycle.start_continuation_attempt(
            dispatching,
            attempt,
        )

        persisted = await store.get(original.invocation_id)
        attempts = await store.list_attempts(original.invocation_id)
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.RUNNING
        assert persisted.revision == original.revision + 2
        assert running.state is CapabilityInvocationState.RUNNING
        assert running_attempt.state is CapabilityInvocationState.RUNNING
        assert attempts[-1].state is CapabilityInvocationState.RUNNING
    finally:
        await driver.disconnect()



@pytest.mark.asyncio
async def test_r7_e_sql_start_failure_rolls_back_invocation_running_cas(tmp_path):
    driver, store, lifecycle = await _sql_lifecycle(tmp_path)
    try:
        original = _invocation("inv-r7e-start-rollback")
        await store.create(original)
        await _seed_attempt_one(store, original.invocation_id)
        loaded = await store.get(original.invocation_id)
        assert loaded is not None

        dispatching, attempt = await lifecycle.begin_continuation_attempt(
            loaded,
            implementation_id="conn-k2:tool.r7e.sql",
            driver_kind="REMOTE_CLIENT",
            connection_id="conn-k2",
            continuation_mode="REPLAY_SAFE",
        )
        running = dispatching.model_copy(
            update={
                "state": CapabilityInvocationState.RUNNING,
                "revision": dispatching.revision + 1,
            }
        )
        missing_attempt = attempt.model_copy(
            update={
                "attempt_id": "att-does-not-exist",
                "state": CapabilityInvocationState.RUNNING,
            }
        )

        started = await store.start_continuation_attempt(
            running,
            dispatching.revision,
            missing_attempt,
        )
        assert started is False

        persisted = await store.get(original.invocation_id)
        attempts = await store.list_attempts(original.invocation_id)
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.DISPATCHING
        assert persisted.revision == dispatching.revision
        assert attempts[-1].attempt_id == attempt.attempt_id
        assert attempts[-1].state is CapabilityInvocationState.DISPATCHING
    finally:
        await driver.disconnect()



@pytest.mark.asyncio
async def test_r7_e_sql_begin_rejects_nonterminal_prior_attempt_atomically(tmp_path):
    driver, store, lifecycle = await _sql_lifecycle(tmp_path)
    try:
        original = _invocation("inv-r7e-prior-running")
        await store.create(original)
        await store.save_attempt(
            CapabilityInvocationAttempt(
                attempt_id="att-prior-running-1",
                invocation_id=original.invocation_id,
                attempt_number=1,
                implementation_id="conn-k1:tool.r7e.sql",
                driver_kind="REMOTE_CLIENT",
                connection_id="conn-k1",
                state=CapabilityInvocationState.RUNNING,
            )
        )
        loaded = await store.get(original.invocation_id)
        assert loaded is not None

        with pytest.raises(RuntimeError, match="continuation rejected"):
            await lifecycle.begin_continuation_attempt(
                loaded,
                implementation_id="conn-k2:tool.r7e.sql",
                driver_kind="REMOTE_CLIENT",
                connection_id="conn-k2",
                continuation_mode="REPLAY_SAFE",
            )

        persisted = await store.get(original.invocation_id)
        attempts = await store.list_attempts(original.invocation_id)
        assert persisted is not None
        assert persisted.state is CapabilityInvocationState.WAITING
        assert persisted.revision == original.revision
        assert [item.state for item in attempts] == [
            CapabilityInvocationState.RUNNING
        ]
    finally:
        await driver.disconnect()
