from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentTaskRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
    TaskBudgetRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent.checkpoint_transcript import (
    materialize_checkpoint_transcript_in_uow,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
    LeaseAuthorityConflictError,
)
from se.src.runtimes.agent.task_budget import (
    TaskBudgetConflictError,
    TaskBudgetService,
)


class _Uow:
    def __init__(self, sessions, repository_cls=AgentRepository):
        self._sessions = sessions
        self._repository_cls = repository_cls
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = self._repository_cls(self.session)
        self.capability_invocations = CapabilityInvocationRepository(
            self.session
        )
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


async def _setup(tmp_path, name: str, *, repository_cls=AgentRepository):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions, repository_cls)
    return (
        engine,
        sessions,
        factory,
        DurableAgentStore(factory),
        TaskBudgetService(factory),
    )


def _invocation(
    invocation_id: str,
    tool_call_id: str,
    *,
    capability_id: str = "tool.remote",
    remote_state: str = "OUTCOME_UNKNOWN",
):
    return CapabilityInvocationRecord(
        invocation_id=invocation_id,
        capability_id=capability_id,
        capability_version="1.0",
        kind="TOOL",
        execution_mode="ONE_SHOT",
        idempotency="DEDUPLICATED",
        request_fingerprint=("a" * 63) + tool_call_id[-1],
        owner_user_id="user-r12-e",
        origin_client_id="client-r12-e",
        remote_outcome_state=remote_state,
        state="RUNNING",
        session_id="session-r12-e",
        execution_id="exec-r12-e",
        tool_call_id=tool_call_id,
        connection_id="conn-r12-e",
        arguments={"call": tool_call_id},
        revision=1,
    )


async def _seed_non_task(
    factory,
    *,
    execution_id: str = "exec-r12-e",
    expiry: datetime,
    revision: int = 5,
    owner: str = "worker-old",
    generation: int = 3,
    with_batch: bool = False,
):
    async with factory() as uow:
        execution = AgentExecutionRecord(
            id=execution_id,
            session_id="session-r12-e",
            agent_id="agent-r12-e",
            correlation_id=f"corr-{execution_id}",
            state="RUNNING",
            revision=revision,
            owner_instance_id=owner,
            lease_expires_at=expiry,
            lease_generation=generation,
            remaining_active_budget_seconds=41.5,
            bound_client_id="client-r12-e",
            bound_connection_id="conn-r12-e",
            request={"prompt": "keep"},
            transcript=[
                {"role": "user", "content": "safe-prefix"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-b",
                            "name": "tool.remote",
                            "arguments": {"call": "call-b"},
                        },
                        {
                            "id": "call-a",
                            "name": "tool.remote",
                            "arguments": {"call": "call-a"},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-b",
                    "name": "tool.remote",
                    "content": {"poison": True},
                },
            ]
            if with_batch
            else [{"role": "user", "content": "safe-prefix"}],
        )
        uow.session.add(execution)
        iteration = AgentIterationRecord(
            id=f"{execution_id}:iteration:1",
            execution_id=execution_id,
            iteration=1,
            state="WAITING_TOOL" if with_batch else "RUNNING",
            tool_call_ids=["call-b", "call-a"] if with_batch else [],
        )
        uow.session.add(iteration)
        if with_batch:
            # Insert in the opposite order to prove SQL/created_at order is
            # lookup-only.  AgentIteration.tool_call_ids remains authoritative.
            for call_id in ("call-a", "call-b"):
                uow.session.add(
                    AgentToolCallRecord(
                        id=f"row-{call_id}",
                        execution_id=execution_id,
                        iteration_id=iteration.id,
                        invocation_id=f"inv-{call_id}",
                        tool_call_id=call_id,
                        capability_id="tool.remote",
                        arguments={"call": call_id},
                        status="PENDING",
                    )
                )
                invocation = _invocation(
                    f"inv-{call_id}",
                    call_id,
                )
                invocation.execution_id = execution_id
                uow.session.add(invocation)

            # call-a is committed and therefore must not become a pending
            # recovery snapshot; call-b remains unresolved.
            uow.session.add(
                AgentToolResultRecord(
                    id="result-call-a",
                    execution_id=execution_id,
                    iteration_id=iteration.id,
                    tool_call_id="call-a",
                    invocation_id="inv-call-a",
                    capability_id="tool.remote",
                    success=True,
                    output={"ok": "a"},
                    commit_state="COMMITTED",
                )
            )
        await uow.commit()


async def _seed_task_scoped(factory, *, expiry: datetime):
    async with factory() as uow:
        uow.session.add(
            AgentTaskRecord(
                id="task-r12-e",
                session_id="session-r12-e",
                created_by="user-r12-e",
                assigned_agent_id="agent-r12-e",
                status="RUNNING",
                wait_reasons=[],
                input={},
            )
        )
        uow.session.add(
            TaskBudgetRecord(
                task_id="task-r12-e",
                revision=0,
                state="OPEN",
                max_total_executions=10,
                max_active_executions=4,
                max_active_branches=4,
                max_parallel_agents=4,
                max_total_tool_calls=100,
                max_total_inference_calls=100,
                max_total_tokens=100000,
                max_total_cost_usd=Decimal("100.00"),
                max_delegation_depth=8,
                policy_version="r12-e",
                policy_fingerprint="f" * 64,
                deny_recursive_agent_cycle=True,
                used_executions=7,
                active_executions=1,
                active_branches=1,
                active_parallel_agents=0,
                used_tool_calls=11,
                used_inference_calls=9,
                used_tokens=1234,
                used_cost_usd=Decimal("2.50000000"),
            )
        )
        uow.session.add(
            AgentExecutionRecord(
                id="exec-task-r12-e",
                session_id="session-r12-e",
                agent_id="agent-r12-e",
                task_id="task-r12-e",
                branch_id="branch-r12-e",
                correlation_id="corr-task-r12-e",
                state="RUNNING",
                revision=8,
                owner_instance_id="worker-task-old",
                lease_expires_at=expiry,
                lease_generation=4,
                remaining_active_budget_seconds=27.25,
                request={"prompt": "task"},
                transcript=[{"role": "user", "content": "task-prefix"}],
            )
        )
        uow.session.add(
            AgentIterationRecord(
                id="exec-task-r12-e:iteration:1",
                execution_id="exec-task-r12-e",
                iteration=1,
                state="RUNNING",
                tool_call_ids=[],
            )
        )
        await uow.commit()


@pytest.mark.asyncio
async def test_r12_e_iteration_zero_recovery_checkpoint_can_resume(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_iteration_zero.sqlite"
    )
    expiry = datetime(2026, 9, 29, 0, 30, tzinfo=timezone.utc)
    try:
        async with factory() as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r12-e-zero",
                    session_id="session-r12-e",
                    agent_id="agent-r12-e",
                    correlation_id="corr-r12-e-zero",
                    state="RUNNING",
                    revision=2,
                    owner_instance_id="worker-zero",
                    lease_expires_at=expiry,
                    lease_generation=1,
                    remaining_active_budget_seconds=19.0,
                    request={"prompt": "start"},
                    transcript=[],
                )
            )
            await uow.commit()

        recovered = await store.commit_recovery_waiting_checkpoint(
            "exec-r12-e-zero",
            observed_owner_instance_id="worker-zero",
            observed_lease_generation=1,
            observed_lease_expires_at=expiry,
            takeover_now_utc=expiry,
        )
        assert recovered.state == "WAITING"
        assert recovered.wait_reason == "RECOVERY"
        assert recovered.revision == 3
        assert recovered.current_checkpoint_id == "exec-r12-e-zero:checkpoint:3"

        resumed = await store.resume_execution("exec-r12-e-zero")
        assert resumed is not None
        assert resumed.iteration == 0
        assert resumed.resume_transcript == []
        assert resumed.resume_pending_tool_calls == []

        async with factory() as uow:
            checkpoint = await uow.agents.get_execution_checkpoint(
                "exec-r12-e-zero:checkpoint:3"
            )
            assert checkpoint is not None
            assert checkpoint.iteration == 0
            assert checkpoint.wait_reason == "RECOVERY"
            assert await uow.agents.list_iterations("exec-r12-e-zero") == []
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_half_persisted_tool_batch_fails_closed(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_half_persisted_batch.sqlite"
    )
    expiry = datetime(2026, 9, 29, 0, 45, tzinfo=timezone.utc)
    try:
        async with factory() as uow:
            uow.session.add(
                AgentExecutionRecord(
                    id="exec-r12-e-half",
                    session_id="session-r12-e",
                    agent_id="agent-r12-e",
                    correlation_id="corr-r12-e-half",
                    state="RUNNING",
                    revision=4,
                    owner_instance_id="worker-half",
                    lease_expires_at=expiry,
                    lease_generation=2,
                    remaining_active_budget_seconds=23.0,
                    request={"prompt": "half"},
                    transcript=[
                        {"role": "user", "content": "half"},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-half",
                                    "name": "tool.remote",
                                    "arguments": {"value": 1},
                                }
                            ],
                        },
                    ],
                )
            )
            # Mirrors the real crash window: the iteration was persisted in
            # PREPARING/THINKING, then inference transcript was persisted,
            # but tool_call_ids were not persisted yet.
            uow.session.add(
                AgentIterationRecord(
                    id="exec-r12-e-half:iteration:1",
                    execution_id="exec-r12-e-half",
                    iteration=1,
                    state="THINKING",
                    tool_call_ids=[],
                )
            )
            await uow.commit()

        with pytest.raises(
            ExecutionConflictError,
            match="SAFE_POINT_ACTIVE_BATCH_AUTHORITY_MISSING",
        ):
            await store.commit_recovery_waiting_checkpoint(
                "exec-r12-e-half",
                observed_owner_instance_id="worker-half",
                observed_lease_generation=2,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution("exec-r12-e-half")
            checkpoint = await uow.agents.get_execution_checkpoint(
                "exec-r12-e-half:checkpoint:5"
            )
            assert execution.state == "RUNNING"
            assert execution.revision == 4
            assert execution.lease_generation == 2
            assert execution.owner_instance_id == "worker-half"
            assert checkpoint is None
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_non_task_boundary_takeover_and_exact_replay(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_non_task.sqlite"
    )
    expiry = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        recovered = await store.commit_recovery_waiting_checkpoint(
            "exec-r12-e",
            observed_owner_instance_id="worker-old",
            observed_lease_generation=3,
            observed_lease_expires_at=expiry,
            takeover_now_utc=expiry,
        )
        assert recovered.state == "WAITING"
        assert recovered.wait_reason == "RECOVERY"
        assert recovered.revision == 6
        assert recovered.owner_instance_id is None
        assert recovered.lease_expires_at is None
        assert recovered.lease_generation == 4
        assert recovered.remaining_active_budget_seconds == 41.5
        assert (
            recovered.current_checkpoint_id
            == "exec-r12-e:checkpoint:6"
        )

        # Exact uncertain-commit replay returns the same winner and does not
        # advance revision/generation again.
        replayed = await store.commit_recovery_waiting_checkpoint(
            "exec-r12-e",
            observed_owner_instance_id="worker-old",
            observed_lease_generation=3,
            observed_lease_expires_at=expiry,
            takeover_now_utc=expiry,
        )
        assert replayed.revision == 6
        assert replayed.lease_generation == 4

        async with factory() as uow:
            checkpoint = await uow.agents.get_execution_checkpoint(
                "exec-r12-e:checkpoint:6"
            )
            assert checkpoint.execution_revision == 6
            assert checkpoint.wait_reason == "RECOVERY"
            assert checkpoint.remaining_active_budget_seconds == 41.5
            assert checkpoint.metadata_json["r12_recovery_fingerprint"]
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_release_reacquire_and_renew_invalidate_scan_receipt(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_stale_receipts.sqlite"
    )
    expiry = datetime(2026, 9, 29, 2, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(
            factory,
            execution_id="exec-release-r12-e",
            expiry=expiry,
        )
        await store.release_execution_lease(
            "exec-release-r12-e",
            owner_instance_id="worker-old",
            lease_generation=3,
        )
        await store.acquire_execution_lease(
            "exec-release-r12-e",
            owner_instance_id="worker-new",
            now_utc=expiry,
            lease_expires_at=expiry + timedelta(seconds=30),
        )
        with pytest.raises(LeaseAuthorityConflictError):
            await store.commit_recovery_waiting_checkpoint(
                "exec-release-r12-e",
                observed_owner_instance_id="worker-old",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry + timedelta(seconds=60),
            )

        await _seed_non_task(
            factory,
            execution_id="exec-renew-r12-e",
            expiry=expiry,
        )
        await store.renew_execution_lease(
            "exec-renew-r12-e",
            owner_instance_id="worker-old",
            lease_generation=3,
            now_utc=expiry - timedelta(seconds=1),
            new_lease_expires_at=expiry + timedelta(seconds=30),
        )
        with pytest.raises(LeaseAuthorityConflictError):
            await store.commit_recovery_waiting_checkpoint(
                "exec-renew-r12-e",
                observed_owner_instance_id="worker-old",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry + timedelta(seconds=60),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_pending_order_uses_iteration_array_and_excludes_committed(
    tmp_path,
):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_pending_order.sqlite"
    )
    expiry = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(
            factory,
            expiry=expiry,
            with_batch=True,
        )
        recovered = await store.commit_recovery_waiting_checkpoint(
            "exec-r12-e",
            observed_owner_instance_id="worker-old",
            observed_lease_generation=3,
            observed_lease_expires_at=expiry,
            takeover_now_utc=expiry,
        )
        checkpoint_id = recovered.current_checkpoint_id

        async with factory() as uow:
            pending = await uow.agents.list_checkpoint_pending_invocations(
                checkpoint_id
            )
            # call-b is ordinal zero in AgentIteration.tool_call_ids even
            # though its AgentToolCall row was inserted second.
            assert [
                (row.ordinal, row.tool_call_id)
                for row in pending
            ] == [(0, "call-b")]

            checkpoint = await uow.agents.get_execution_checkpoint(
                checkpoint_id
            )
            messages = await materialize_checkpoint_transcript_in_uow(
                uow,
                checkpoint,
            )
            dumped = [
                item.model_dump(mode="json")
                for item in messages
            ]
            assert "poison" not in repr(dumped)
            assert dumped[0]["content"] == "safe-prefix"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_competing_recoverers_advance_generation_once(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_competing.sqlite"
    )
    expiry = datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        async def attempt():
            try:
                return await store.commit_recovery_waiting_checkpoint(
                    "exec-r12-e",
                    observed_owner_instance_id="worker-old",
                    observed_lease_generation=3,
                    observed_lease_expires_at=expiry,
                    takeover_now_utc=expiry,
                )
            except LeaseAuthorityConflictError as exc:
                return exc

        results = await asyncio.gather(attempt(), attempt())
        loaded = await store.load_execution("exec-r12-e")
        assert loaded.state == "WAITING"
        assert loaded.revision == 6
        assert loaded.lease_generation == 4
        assert loaded.current_checkpoint_id == "exec-r12-e:checkpoint:6"
        # Both callers may observe the same idempotent durable winner, but
        # there is exactly one semantic revision/generation advance.
        winners = [
            item for item in results
            if not isinstance(item, LeaseAuthorityConflictError)
        ]
        assert winners
        assert all(item.revision == 6 for item in winners)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_task_budget_release_once_and_cumulative_usage_preserved(
    tmp_path,
):
    engine, sessions, factory, _, budget_service = await _setup(
        tmp_path, "r12_e_task_budget.sqlite"
    )
    expiry = datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)
    try:
        await _seed_task_scoped(factory, expiry=expiry)

        revision = await budget_service.recover_task_scoped_execution(
            "task-r12-e",
            execution_id="exec-task-r12-e",
            observed_owner_instance_id="worker-task-old",
            observed_lease_generation=4,
            observed_lease_expires_at=expiry,
            takeover_now_utc=expiry,
        )
        assert revision == 9

        async with factory() as uow:
            execution = await uow.agents.get_execution(
                "exec-task-r12-e"
            )
            budget = await uow.agents.get_task_budget("task-r12-e")
            reservation = await uow.agents.get_task_budget_reservation(
                "task-r12-e",
                "RELEASE_EXECUTION",
                "exec-task-r12-e:9",
            )
            assert execution.state == "WAITING"
            assert execution.wait_reason == "RECOVERY"
            assert execution.revision == 9
            assert execution.lease_generation == 5
            assert execution.remaining_active_budget_seconds == 27.25
            assert budget.active_executions == 0
            assert budget.used_executions == 7
            assert budget.used_tool_calls == 11
            assert budget.used_inference_calls == 9
            assert budget.used_tokens == 1234
            assert budget.used_cost_usd == Decimal("2.50000000")
            assert reservation is not None
            first_budget_revision = budget.revision
            await uow.commit()

        replay_revision = (
            await budget_service.recover_task_scoped_execution(
                "task-r12-e",
                execution_id="exec-task-r12-e",
                observed_owner_instance_id="worker-task-old",
                observed_lease_generation=4,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )
        )
        assert replay_revision == 9

        async with factory() as uow:
            budget = await uow.agents.get_task_budget("task-r12-e")
            execution = await uow.agents.get_execution(
                "exec-task-r12-e"
            )
            assert budget.active_executions == 0
            assert budget.revision == first_budget_revision
            assert execution.revision == 9
            assert execution.lease_generation == 5
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_task_receipt_mismatch_has_zero_budget_mutation(tmp_path):
    engine, sessions, factory, _, budget_service = await _setup(
        tmp_path, "r12_e_task_reject.sqlite"
    )
    expiry = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
    try:
        await _seed_task_scoped(factory, expiry=expiry)

        with pytest.raises(TaskBudgetConflictError):
            await budget_service.recover_task_scoped_execution(
                "task-r12-e",
                execution_id="exec-task-r12-e",
                observed_owner_instance_id="wrong-owner",
                observed_lease_generation=4,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution(
                "exec-task-r12-e"
            )
            budget = await uow.agents.get_task_budget("task-r12-e")
            assert execution.state == "RUNNING"
            assert execution.revision == 8
            assert execution.lease_generation == 4
            assert budget.active_executions == 1
            assert budget.revision == 0
            assert (
                await uow.agents.get_execution_checkpoint(
                    "exec-task-r12-e:checkpoint:9"
                )
                is None
            )
            await uow.commit()
    finally:
        await engine.dispose()


class _RejectRecoveryRepository(AgentRepository):
    async def compare_and_set_recovery_waiting_execution(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_r12_e_terminalization_before_final_recovery_cas_wins(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path, "r12_e_terminal_race.sqlite"
    )
    expiry = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        # This models a semantic lifecycle winner after stale observation.
        await store.compare_and_set_execution(
            "exec-r12-e",
            5,
            {
                "state": "COMPLETED",
                "wait_reason": None,
                "completed_at": expiry,
            },
        )

        with pytest.raises(LeaseAuthorityConflictError):
            await store.commit_recovery_waiting_checkpoint(
                "exec-r12-e",
                observed_owner_instance_id="worker-old",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )

        loaded = await store.load_execution("exec-r12-e")
        assert loaded.state == "COMPLETED"
        assert loaded.revision == 6
        assert loaded.lease_generation == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_repository_rejects_noncanonical_recovery_checkpoint_id(
    tmp_path,
):
    engine, sessions, factory, _, _ = await _setup(
        tmp_path, "r12_e_checkpoint_fence.sqlite"
    )
    expiry = datetime(2026, 9, 29, 7, 30, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        async with factory() as uow:
            with pytest.raises(
                ValueError,
                match="deterministic checkpoint identity",
            ):
                await uow.agents.compare_and_set_recovery_waiting_execution(
                    "exec-r12-e",
                    5,
                    observed_owner_instance_id="worker-old",
                    observed_lease_generation=3,
                    observed_lease_expires_at=expiry,
                    takeover_now_utc=expiry,
                    values={
                        "current_checkpoint_id": "exec-r12-e:checkpoint:wrong",
                    },
                )
            await uow.rollback()

        async with factory() as uow:
            loaded = await uow.agents.get_execution("exec-r12-e")
            assert loaded.state == "RUNNING"
            assert loaded.revision == 5
            assert loaded.lease_generation == 3
            assert loaded.current_checkpoint_id is None
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_semantic_revision_race_loses_exact_final_cas(tmp_path):
    engine, sessions, factory, _, _ = await _setup(
        tmp_path, "r12_e_revision_race.sqlite"
    )
    expiry = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        # Recovery has observed source revision 5. A user/lifecycle writer
        # advances that semantic revision while leaving the lease tuple intact.
        async with factory() as winner_uow:
            winner = await winner_uow.agents.compare_and_set_execution(
                "exec-r12-e",
                5,
                {"context_state": {"lifecycle": "user-won"}},
            )
            assert winner is not None
            await winner_uow.commit()

        async with factory() as recovery_uow:
            stale = (
                await recovery_uow.agents.compare_and_set_recovery_waiting_execution(
                    "exec-r12-e",
                    5,
                    observed_owner_instance_id="worker-old",
                    observed_lease_generation=3,
                    observed_lease_expires_at=expiry,
                    takeover_now_utc=expiry,
                    values={
                        "current_checkpoint_id": "exec-r12-e:checkpoint:6",
                    },
                )
            )
            assert stale is None
            await recovery_uow.rollback()

        async with factory() as uow:
            loaded = await uow.agents.get_execution("exec-r12-e")
            assert loaded.state == "RUNNING"
            assert loaded.revision == 6
            assert loaded.context_state == {"lifecycle": "user-won"}
            assert loaded.lease_generation == 3
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_non_task_final_cas_loss_rolls_back_staged_checkpoint(tmp_path):
    engine, sessions, factory, store, _ = await _setup(
        tmp_path,
        "r12_e_non_task_rollback.sqlite",
        repository_cls=_RejectRecoveryRepository,
    )
    expiry = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    try:
        await _seed_non_task(factory, expiry=expiry)

        with pytest.raises(LeaseAuthorityConflictError):
            await store.commit_recovery_waiting_checkpoint(
                "exec-r12-e",
                observed_owner_instance_id="worker-old",
                observed_lease_generation=3,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution("exec-r12-e")
            checkpoint = await uow.agents.get_execution_checkpoint(
                "exec-r12-e:checkpoint:6"
            )
            assert execution.state == "RUNNING"
            assert execution.revision == 5
            assert execution.lease_generation == 3
            assert checkpoint is None
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r12_e_task_final_cas_loss_rolls_back_budget_and_checkpoint(tmp_path):
    engine, sessions, factory, _, budget_service = await _setup(
        tmp_path,
        "r12_e_task_rollback.sqlite",
        repository_cls=_RejectRecoveryRepository,
    )
    expiry = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    try:
        await _seed_task_scoped(factory, expiry=expiry)

        with pytest.raises(TaskBudgetConflictError):
            await budget_service.recover_task_scoped_execution(
                "task-r12-e",
                execution_id="exec-task-r12-e",
                observed_owner_instance_id="worker-task-old",
                observed_lease_generation=4,
                observed_lease_expires_at=expiry,
                takeover_now_utc=expiry,
            )

        async with factory() as uow:
            execution = await uow.agents.get_execution("exec-task-r12-e")
            budget = await uow.agents.get_task_budget("task-r12-e")
            checkpoint = await uow.agents.get_execution_checkpoint(
                "exec-task-r12-e:checkpoint:9"
            )
            reservation = await uow.agents.get_task_budget_reservation(
                "task-r12-e",
                "RELEASE_EXECUTION",
                "exec-task-r12-e:9",
            )
            assert execution.state == "RUNNING"
            assert execution.revision == 8
            assert execution.lease_generation == 4
            assert budget.active_executions == 1
            assert budget.revision == 0
            assert checkpoint is None
            assert reservation is None
            await uow.commit()
    finally:
        await engine.dispose()
