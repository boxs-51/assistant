"""TA-V1-R3 P0-SEND: durable first-send ownership, RED-first security oracles.

These tests never dispatch a process or contact a remote client.
"""
from __future__ import annotations

import asyncio

from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationAttempt,
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.invocation import InMemoryCapabilityInvocationStore


def _store():
    store = InMemoryCapabilityInvocationStore()
    invocation = CapabilityInvocation(
        invocation_id="ta-r3-inv",
        capability_id="safe.echo",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        state=CapabilityInvocationState.RUNNING,
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        attempt=1,
    )
    attempt = CapabilityInvocationAttempt(
        attempt_id="ta-r3-att-1",
        invocation_id=invocation.invocation_id,
        attempt_number=1,
        implementation_id="client:echo",
        driver_kind="REMOTE_CLIENT",
        connection_id="conn-1",
        state=CapabilityInvocationState.RUNNING,
    )
    store.items[invocation.invocation_id] = invocation
    store.attempts[attempt.attempt_id] = attempt
    return store, invocation, attempt


def test_send_claim_has_one_winner_and_is_durable_on_attempt():
    async def run():
        store, invocation, attempt = _store()
        winners = await asyncio.gather(*(
            store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1)
            for _ in range(8)
        ))
        assert winners.count(True) == 1
        assert winners.count(False) == 7
        assert store.attempts[attempt.attempt_id].metadata["ta_r3_send_claimed"] is True
        # A subsequent attempt update cannot erase the send-ownership record.
        await store.update_attempt(attempt)
        assert store.attempts[attempt.attempt_id].metadata["ta_r3_send_claimed"] is True
        assert not await store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1)
    asyncio.run(run())


def test_send_claim_rejects_wrong_attempt_or_missing_row():
    async def run():
        store, invocation, attempt = _store()
        assert not await store.claim_remote_send(invocation.invocation_id, "missing", 1)
        assert not await store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 2)
        assert not await store.claim_remote_send("different-invocation", attempt.attempt_id, 1)
        assert await store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1)
    asyncio.run(run())


def test_send_claim_rejects_terminal_attempt_and_invocation():
    async def run():
        store, invocation, attempt = _store()
        store.attempts[attempt.attempt_id].state = CapabilityInvocationState.COMPLETED
        assert not await store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1)
        store.attempts[attempt.attempt_id].state = CapabilityInvocationState.RUNNING
        store.items[invocation.invocation_id].state = CapabilityInvocationState.COMPLETED
        assert not await store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1)
    asyncio.run(run())


async def _sql_store(path):
    from se.src.infrastructure.config.schemas import DriverConfig
    from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
    from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
    from se.src.infrastructure.storage.models.sql.base import Base
    from se.src.infrastructure.storage.repositories.capability_invocations import (
        SqlCapabilityInvocationStore,
    )

    driver = SQLiteDriver(DriverConfig(
        enabled=True, required=True, options={"path": str(path)}
    ))
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return SqlCapabilityInvocationStore(lambda: SqlAlchemyUnitOfWork(driver)), driver


def test_sql_send_claim_is_one_winner_and_survives_attempt_update(tmp_path):
    async def run():
        store, driver = await _sql_store(tmp_path / "ta-r3-claim.db")
        _, invocation, attempt = _store()
        try:
            await store.create(invocation)
            await store.save_attempt(attempt)
            first, second = await asyncio.gather(
                store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1),
                store.claim_remote_send(invocation.invocation_id, attempt.attempt_id, 1),
            )
            assert (first, second) in ((True, False), (False, True))
            await store.update_attempt(attempt)
            records = await store.list_attempts(invocation.invocation_id)
            assert len(records) == 1
            assert records[0].metadata["ta_r3_send_claimed"] is True
            assert not await store.claim_remote_send(
                invocation.invocation_id, attempt.attempt_id, 1
            )
        finally:
            await driver.disconnect()
    asyncio.run(run())
