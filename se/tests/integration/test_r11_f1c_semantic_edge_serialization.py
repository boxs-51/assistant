from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
)
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
    InvocationSerializationConflictError,
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)
from se.src.runtimes.agent.task_budget import (
    TaskBudgetConflictError,
    TaskBudgetService,
)
from se.src.runtimes.agent.waiting_checkpoint import (
    WaitingCheckpointConflictError,
    stage_waiting_checkpoint,
)


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
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


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=4,
        max_parallel_agents=4,
        max_total_tool_calls=32,
        max_total_inference_calls=32,
        max_delegation_depth=4,
        max_total_tokens=10000,
        max_total_cost_usd="10",
    )


def _task_values(task_id: str, *, parent_task_id: str | None = None):
    return {
        "id": task_id,
        "session_id": "session-r11-f1c-edge-race",
        "created_by": "user-r11-f1c",
        "assigned_agent_id": "agent-r11-f1c",
        "revision": 0,
        "parent_task_id": parent_task_id,
        "status": "ASSIGNED",
        "wait_reasons": [],
        "input": {"prompt": task_id},
    }


async def _setup(tmp_path):
    database = tmp_path / "r11_f1c_semantic_edge_serialization.sqlite"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database.as_posix()}",
        connect_args={"timeout": 5},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    factory = lambda: _Uow(sessions)
    service = TaskBudgetService(
        factory,
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r11-f1c-edge-race-test"),
    )
    store = DurableAgentStore(factory)
    return engine, sessions, service, store


async def _seed_execution(
    sessions,
    *,
    execution_id: str,
    task_id: str,
    branch_id: str,
    state: str = "RUNNING",
    revision: int = 0,
    current_checkpoint_id: str | None = None,
):
    async with sessions() as session:
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"session-{execution_id}",
                agent_id=f"agent-{execution_id}",
                task_id=task_id,
                branch_id=branch_id,
                correlation_id=f"corr-{execution_id}",
                state=state,
                revision=revision,
                current_checkpoint_id=current_checkpoint_id,
                request={},
                result={"ok": True} if state == "COMPLETED" else None,
                completed_at=(
                    datetime.now(timezone.utc)
                    if state == "COMPLETED"
                    else None
                ),
            )
        )
        await session.commit()


async def _seed_iteration(sessions, execution_id: str, iteration_id: str):
    async with sessions() as session:
        session.add(
            AgentIterationRecord(
                id=iteration_id,
                execution_id=execution_id,
                iteration=1,
                state="COMPLETED",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _invocation_values(
    invocation_id: str,
    *,
    execution_id: str,
    tool_call_id: str,
):
    return {
        "invocation_id": invocation_id,
        "capability_id": "tool.remote",
        "kind": "TOOL",
        "execution_mode": "REMOTE",
        "idempotency": "IDEMPOTENT",
        "state": "COMPLETED",
        "execution_id": execution_id,
        "tool_call_id": tool_call_id,
        "arguments": {},
        "revision": 1,
        "completed_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_r11_f1c_child_creation_requires_existing_parent(tmp_path):
    engine, sessions, service, _store = await _setup(tmp_path)
    try:
        with pytest.raises(TaskBudgetConflictError, match="Parent AgentTask not found"):
            await service.create_task_with_budget(
                _task_values(
                    "child-missing-parent",
                    parent_task_id="missing-parent",
                )
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_task("child-missing-parent") is None
            assert await repo.get_task_budget("child-missing-parent") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_sqlite_task_gc_fence_prevents_dangling_child_commit(
    tmp_path,
):
    engine, sessions, service, _store = await _setup(tmp_path)
    gc_uow = _Uow(sessions)
    child_create = None
    exited = False
    try:
        await service.create_task_with_budget(_task_values("parent-task"))

        await gc_uow.__aenter__()
        parent = await gc_uow.agents.lock_task_gc_serialization_fence(
            "parent-task"
        )
        assert parent is not None

        child_create = asyncio.create_task(
            service.create_task_with_budget(
                _task_values(
                    "child-task",
                    parent_task_id="parent-task",
                )
            )
        )
        await asyncio.sleep(0.1)
        assert not child_create.done()

        await gc_uow.session.delete(parent)
        await gc_uow.session.flush()
        await gc_uow.commit()
        await gc_uow.__aexit__(None, None, None)
        exited = True

        with pytest.raises(TaskBudgetConflictError, match="Parent AgentTask not found"):
            await child_create

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_task("parent-task") is None
            assert await repo.get_task("child-task") is None
            assert await repo.get_task_budget("child-task") is None
    finally:
        if not exited and gc_uow._ctx is not None and gc_uow.session is not None:
            try:
                if gc_uow.session.in_transaction():
                    await gc_uow.rollback()
                await gc_uow.__aexit__(None, None, None)
            except Exception:
                pass
        if child_create is not None and not child_create.done():
            child_create.cancel()
            await asyncio.gather(child_create, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_cross_task_checkpoint_parent_fails_before_write(tmp_path):
    engine, sessions, _service, _store = await _setup(tmp_path)
    try:
        await _seed_execution(
            sessions,
            execution_id="exec-parent",
            task_id="task-parent",
            branch_id="branch-parent",
            state="COMPLETED",
            revision=1,
        )
        async with sessions() as session:
            session.add(
                AgentExecutionCheckpointRecord(
                    checkpoint_id="cp-parent",
                    execution_id="exec-parent",
                    execution_revision=1,
                    session_id="session-exec-parent",
                    task_id="task-parent",
                    branch_id="branch-parent",
                    parent_checkpoint_id=None,
                    iteration=1,
                    wait_reason="BUDGET",
                    transcript_snapshot=[],
                    metadata_json={},
                )
            )
            await session.commit()

        await _seed_execution(
            sessions,
            execution_id="exec-child",
            task_id="task-child",
            branch_id="branch-child",
            state="RUNNING",
            revision=0,
            current_checkpoint_id="cp-parent",
        )

        async with _Uow(sessions) as uow:
            execution = await uow.agents.get_execution("exec-child")
            with pytest.raises(
                WaitingCheckpointConflictError,
                match="another execution/task/branch lineage",
            ):
                await stage_waiting_checkpoint(
                    uow,
                    execution=execution,
                    source_revision=0,
                    transition_values={"wait_reason": "BUDGET"},
                    checkpoint_values={
                        "checkpoint_id": "cp-child",
                        "execution_id": "exec-child",
                        "execution_revision": 1,
                        "session_id": "session-exec-child",
                        "task_id": "task-child",
                        "branch_id": "branch-child",
                        "parent_checkpoint_id": "cp-parent",
                        "iteration": 1,
                        "wait_reason": "BUDGET",
                        "transcript_snapshot": [],
                        "metadata_json": {},
                    },
                    pending_invocations=[],
                )
            await uow.rollback()

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_execution_checkpoint("cp-child") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_fresh_tool_call_rejects_existing_invocation_id(tmp_path):
    engine, sessions, _service, store = await _setup(tmp_path)
    try:
        await _seed_execution(
            sessions,
            execution_id="exec-writer",
            task_id="task-writer",
            branch_id="branch-writer",
        )
        await _seed_iteration(sessions, "exec-writer", "iter-writer")
        async with sessions() as session:
            session.add(
                CapabilityInvocationRecord(
                    **_invocation_values(
                        "inv-collision",
                        execution_id="candidate-exec",
                        tool_call_id="candidate-call",
                    )
                )
            )
            await session.commit()

        with pytest.raises(
            ExecutionConflictError,
            match="invocation_id is already bound",
        ):
            await store.save_tool_call(
                {
                    "execution_id": "exec-writer",
                    "iteration_id": "iter-writer",
                    "invocation_id": "inv-collision",
                    "tool_call_id": "writer-call",
                    "capability_id": "tool.remote",
                    "arguments": {},
                    "status": "PENDING",
                }
            )

        async with sessions() as session:
            repo = AgentRepository(session)
            assert await repo.get_tool_call("exec-writer", "writer-call") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_sqlite_invocation_fence_blocks_writer_across_gc(tmp_path):
    engine, sessions, _service, store = await _setup(tmp_path)
    gc_uow = _Uow(sessions)
    writer = None
    exited = False
    try:
        await _seed_execution(
            sessions,
            execution_id="exec-writer",
            task_id="task-writer",
            branch_id="branch-writer",
        )
        await _seed_iteration(sessions, "exec-writer", "iter-writer")
        async with sessions() as session:
            session.add(
                CapabilityInvocationRecord(
                    **_invocation_values(
                        "inv-race",
                        execution_id="candidate-exec",
                        tool_call_id="candidate-call",
                    )
                )
            )
            await session.commit()

        await gc_uow.__aenter__()
        candidate = (
            await gc_uow.capability_invocations
            .lock_invocation_gc_serialization_fence("inv-race")
        )
        assert candidate is not None

        writer = asyncio.create_task(
            store.save_tool_call(
                {
                    "execution_id": "exec-writer",
                    "iteration_id": "iter-writer",
                    "invocation_id": "inv-race",
                    "tool_call_id": "writer-call",
                    "capability_id": "tool.remote",
                    "arguments": {},
                    "status": "PENDING",
                }
            )
        )
        await asyncio.sleep(0.1)
        assert not writer.done()

        await gc_uow.session.delete(candidate)
        await gc_uow.session.flush()
        await gc_uow.commit()
        await gc_uow.__aexit__(None, None, None)
        exited = True

        durable_call = await writer
        assert durable_call.invocation_id == "inv-race"

        async with sessions() as session:
            session.add(
                CapabilityInvocationRecord(
                    **_invocation_values(
                        "inv-race",
                        execution_id="exec-writer",
                        tool_call_id="writer-call",
                    )
                )
            )
            await session.commit()

        async with sessions() as session:
            repo = AgentRepository(session)
            call = await repo.get_tool_call("exec-writer", "writer-call")
            invocation = await session.get(
                CapabilityInvocationRecord,
                "inv-race",
            )
            assert call is not None
            assert invocation is not None
            assert invocation.execution_id == call.execution_id
            assert invocation.tool_call_id == call.tool_call_id
    finally:
        if not exited and gc_uow._ctx is not None and gc_uow.session is not None:
            try:
                if gc_uow.session.in_transaction():
                    await gc_uow.rollback()
                await gc_uow.__aexit__(None, None, None)
            except Exception:
                pass
        if writer is not None and not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        await engine.dispose()



def _domain_invocation(
    invocation_id: str,
    *,
    execution_id: str,
    tool_call_id: str,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id="tool.remote",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        state=CapabilityInvocationState.CREATED,
        execution_id=execution_id,
        tool_call_id=tool_call_id,
        arguments={},
    )


@pytest.mark.asyncio
async def test_r11_f1c_absent_key_blocks_conflicting_r6_create_until_agent_owner_commits(
    tmp_path,
):
    engine, sessions, _service, _store = await _setup(tmp_path)
    owner_uow = _Uow(sessions)
    r6_create = None
    exited = False
    try:
        await _seed_execution(
            sessions,
            execution_id="exec-absent-agent-owner",
            task_id="task-absent-agent-owner",
            branch_id="branch-absent-agent-owner",
        )
        await _seed_iteration(
            sessions,
            "exec-absent-agent-owner",
            "iter-absent-agent-owner",
        )

        await owner_uow.__aenter__()
        assert (
            await owner_uow.capability_invocations
            .lock_invocation_gc_serialization_fence("inv-absent-shared")
            is None
        )

        r6_store = SqlCapabilityInvocationStore(lambda: _Uow(sessions))
        r6_create = asyncio.create_task(
            r6_store.create(
                _domain_invocation(
                    "inv-absent-shared",
                    execution_id="unrelated-r6-exec",
                    tool_call_id="unrelated-r6-call",
                )
            )
        )
        await asyncio.sleep(0.1)
        assert not r6_create.done()

        await owner_uow.agents.save_tool_call(
            {
                "execution_id": "exec-absent-agent-owner",
                "iteration_id": "iter-absent-agent-owner",
                "invocation_id": "inv-absent-shared",
                "tool_call_id": "agent-owner-call",
                "capability_id": "tool.remote",
                "arguments": {},
                "status": "PENDING",
            }
        )
        await owner_uow.commit()
        await owner_uow.__aexit__(None, None, None)
        exited = True

        with pytest.raises(
            InvocationSerializationConflictError,
            match="conflicts with durable AgentToolCall ownership",
        ):
            await r6_create

        async with sessions() as session:
            repo = AgentRepository(session)
            assert (
                await repo.get_tool_call(
                    "exec-absent-agent-owner",
                    "agent-owner-call",
                )
                is not None
            )
            assert (
                await session.get(
                    CapabilityInvocationRecord,
                    "inv-absent-shared",
                )
                is None
            )
    finally:
        if (
            not exited
            and owner_uow._ctx is not None
            and owner_uow.session is not None
        ):
            try:
                if owner_uow.session.in_transaction():
                    await owner_uow.rollback()
                await owner_uow.__aexit__(None, None, None)
            except Exception:
                pass
        if r6_create is not None and not r6_create.done():
            r6_create.cancel()
            await asyncio.gather(r6_create, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_absent_key_blocks_agent_writer_until_r6_owner_commits(
    tmp_path,
):
    engine, sessions, _service, store = await _setup(tmp_path)
    owner_uow = _Uow(sessions)
    writer = None
    exited = False
    try:
        await _seed_execution(
            sessions,
            execution_id="exec-absent-agent-writer",
            task_id="task-absent-agent-writer",
            branch_id="branch-absent-agent-writer",
        )
        await _seed_iteration(
            sessions,
            "exec-absent-agent-writer",
            "iter-absent-agent-writer",
        )

        await owner_uow.__aenter__()
        assert (
            await owner_uow.capability_invocations
            .lock_invocation_gc_serialization_fence("inv-r6-wins-absent")
            is None
        )

        writer = asyncio.create_task(
            store.save_tool_call(
                {
                    "execution_id": "exec-absent-agent-writer",
                    "iteration_id": "iter-absent-agent-writer",
                    "invocation_id": "inv-r6-wins-absent",
                    "tool_call_id": "agent-writer-call",
                    "capability_id": "tool.remote",
                    "arguments": {},
                    "status": "PENDING",
                }
            )
        )
        await asyncio.sleep(0.1)
        assert not writer.done()

        owner_uow.session.add(
            CapabilityInvocationRecord(
                **_invocation_values(
                    "inv-r6-wins-absent",
                    execution_id="unrelated-r6-owner",
                    tool_call_id="unrelated-r6-call",
                )
            )
        )
        await owner_uow.commit()
        await owner_uow.__aexit__(None, None, None)
        exited = True

        with pytest.raises(
            ExecutionConflictError,
            match="invocation_id is already bound",
        ):
            await writer

        async with sessions() as session:
            repo = AgentRepository(session)
            assert (
                await repo.get_tool_call(
                    "exec-absent-agent-writer",
                    "agent-writer-call",
                )
                is None
            )
            assert (
                await session.get(
                    CapabilityInvocationRecord,
                    "inv-r6-wins-absent",
                )
                is not None
            )
    finally:
        if (
            not exited
            and owner_uow._ctx is not None
            and owner_uow.session is not None
        ):
            try:
                if owner_uow.session.in_transaction():
                    await owner_uow.rollback()
                await owner_uow.__aexit__(None, None, None)
            except Exception:
                pass
        if writer is not None and not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        await engine.dispose()
