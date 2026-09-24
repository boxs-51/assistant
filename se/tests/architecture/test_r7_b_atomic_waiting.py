from __future__ import annotations

from datetime import timezone
import hashlib

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import TaskBudgetLimits, TaskBudgetPolicy
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import CapabilityInvocationRecord
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import CapabilityInvocationRepository
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.task_budget import TaskBudgetService


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(self.session)
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
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=2,
        max_total_tool_calls=8,
        max_total_inference_calls=8,
        max_total_tokens=1000,
        max_total_cost_usd="10",
        max_delegation_depth=4,
    )


def _root_branch_id(task_id: str) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:40]
    return f"r8_root_{digest}"


def _checkpoint(execution_id: str, session_id: str, *, task_id: str | None, revision: int):
    return {
        "checkpoint_id": f"{execution_id}:checkpoint:{revision}",
        "execution_id": execution_id,
        "execution_revision": revision,
        "session_id": session_id,
        "task_id": task_id,
        "branch_id": (
            _root_branch_id(task_id)
            if task_id is not None
            else None
        ),
        "iteration": 3,
        "wait_reason": "CONNECTION",
        "remaining_active_budget_seconds": 20.0,
        "wait_expires_at": None,
        "origin_client_id": "client-r7-b",
        "origin_connection_id": "conn-k1",
        "transcript_snapshot": [{"role": "assistant", "content": ""}],
        "metadata_json": {"phase": "r7-b"},
    }


def _pending():
    return [{
        "ordinal": 2,
        "invocation_id": "inv-r7-b",
        "tool_call_id": "call-r7-b",
        "capability_id": "tool.remote",
    }]


async def _insert_invocation(session, execution_id: str):
    session.add(
        CapabilityInvocationRecord(
            invocation_id="inv-r7-b",
            capability_id="tool.remote",
            capability_version="7.1",
            kind="TOOL",
            execution_mode="ONE_SHOT",
            idempotency="DEDUPLICATED",
            request_fingerprint="a" * 64,
            owner_user_id="user-r7-b",
            origin_client_id="client-r7-b",
            remote_outcome_state="OUTCOME_UNKNOWN",
            state="RUNNING",
            session_id="session-r7-b",
            execution_id=execution_id,
            tool_call_id="call-r7-b",
            connection_id="conn-k1",
            arguments={},
            revision=5,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_r7_b_task_waiting_commits_budget_checkpoint_snapshot_and_execution_atomically(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'r7b-task.sqlite').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = TaskBudgetService(
        lambda: _Uow(sessions),
        default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r7-b"),
    )
    try:
        await service.create_task_with_budget({
            "id": "task-r7-b", "session_id": "session-r7-b",
            "created_by": "user-r7-b", "assigned_agent_id": "agent-r7-b",
            "revision": 0, "status": "RUNNING", "wait_reasons": [], "input": {},
        })
        await service.start_task_scoped_execution(
            "task-r7-b", execution_id="exec-r7-b",
            execution_values={
                "id": "exec-r7-b", "session_id": "session-r7-b",
                "agent_id": "agent-r7-b", "task_id": "task-r7-b",
                "correlation_id": "corr-r7-b", "state": "RUNNING",
                "revision": 1, "request": {},
            },
        )
        async with _Uow(sessions) as uow:
            await _insert_invocation(uow.session, "exec-r7-b")
            await uow.commit()
        revision = await service.finish_task_scoped_execution(
            "task-r7-b", execution_id="exec-r7-b", source_revision=1,
            transition_values={
                "state": "WAITING", "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 20.0,
                "wait_expires_at": None, "completed_at": None,
            },
            delegated=False,
            checkpoint_values=_checkpoint(
                "exec-r7-b", "session-r7-b", task_id="task-r7-b", revision=2
            ),
            pending_invocations=_pending(),
        )
        assert revision == 2
        async with _Uow(sessions) as uow:
            execution = await uow.agents.get_execution("exec-r7-b")
            budget = await uow.agents.get_task_budget("task-r7-b")
            checkpoint = await uow.agents.get_execution_checkpoint("exec-r7-b:checkpoint:2")
            pending = await uow.agents.list_checkpoint_pending_invocations(checkpoint.checkpoint_id)
            assert execution.state == "WAITING"
            assert execution.revision == checkpoint.execution_revision == 2
            assert execution.current_checkpoint_id == checkpoint.checkpoint_id
            assert execution.bound_client_id == "client-r7-b"
            assert execution.bound_connection_id is None
            assert budget.active_executions == 0
            row = pending[0]
            assert row.ordinal == 2
            assert row.invocation_revision == 5
            assert row.capability_version == "7.1"
            assert row.request_fingerprint == "a" * 64
            assert row.idempotency == "DEDUPLICATED"
            assert row.observed_remote_outcome_state == "OUTCOME_UNKNOWN"
            assert row.origin_client_id == "client-r7-b"
            assert row.origin_connection_id == "conn-k1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_b_execution_update_failure_rolls_back_checkpoint_and_budget_release(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'r7b-rollback.sqlite').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = TaskBudgetService(
        lambda: _Uow(sessions), default_limits=_limits(),
        default_policy=TaskBudgetPolicy(version="r7-b"),
    )
    try:
        await service.create_task_with_budget({
            "id": "task-r7-b", "session_id": "session-r7-b",
            "created_by": "user-r7-b", "assigned_agent_id": "agent-r7-b",
            "revision": 0, "status": "RUNNING", "wait_reasons": [], "input": {},
        })
        await service.start_task_scoped_execution(
            "task-r7-b", execution_id="exec-r7-b",
            execution_values={
                "id": "exec-r7-b", "session_id": "session-r7-b",
                "agent_id": "agent-r7-b", "task_id": "task-r7-b",
                "correlation_id": "corr-r7-b", "state": "RUNNING",
                "revision": 1, "request": {},
            },
        )
        async with _Uow(sessions) as uow:
            await _insert_invocation(uow.session, "exec-r7-b")
            await uow.commit()
        async with engine.begin() as connection:
            await connection.execute(text("""
                CREATE TRIGGER reject_r7b_waiting
                BEFORE UPDATE ON agent_executions
                WHEN NEW.state = 'WAITING'
                BEGIN SELECT RAISE(ABORT, 'reject waiting'); END
            """))
        with pytest.raises(Exception):
            await service.finish_task_scoped_execution(
                "task-r7-b", execution_id="exec-r7-b", source_revision=1,
                transition_values={
                    "state": "WAITING", "wait_reason": "CONNECTION",
                    "remaining_active_budget_seconds": 20.0,
                },
                delegated=False,
                checkpoint_values=_checkpoint(
                    "exec-r7-b", "session-r7-b", task_id="task-r7-b", revision=2
                ),
                pending_invocations=_pending(),
            )
        async with _Uow(sessions) as uow:
            execution = await uow.agents.get_execution("exec-r7-b")
            budget = await uow.agents.get_task_budget("task-r7-b")
            checkpoint = await uow.agents.get_execution_checkpoint("exec-r7-b:checkpoint:2")
            assert execution.state == "RUNNING"
            assert execution.revision == 1
            assert execution.current_checkpoint_id is None
            assert budget.active_executions == 1
            assert checkpoint is None
            assert int(
                await uow.session.scalar(
                    select(func.count()).select_from(AgentTranscriptRepresentationRecord)
                ) or 0
            ) == 0
            assert int(
                await uow.session.scalar(
                    select(func.count()).select_from(AgentTranscriptPayloadNodeRecord)
                ) or 0
            ) == 0
            assert int(
                await uow.session.scalar(
                    select(func.count()).select_from(AgentTranscriptChunkRecord)
                ) or 0
            ) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_b_non_task_waiting_uses_same_checkpoint_execution_transaction(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'r7b-nontask.sqlite').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = DurableAgentStore(lambda: _Uow(sessions))
    try:
        async with _Uow(sessions) as uow:
            uow.session.add(AgentExecutionRecord(
                id="exec-r7-b", session_id="session-r7-b",
                agent_id="agent-r7-b", correlation_id="corr-r7-b",
                state="RUNNING", revision=1, request={},
            ))
            await _insert_invocation(uow.session, "exec-r7-b")
            await uow.commit()
        await store.commit_waiting_checkpoint(
            "exec-r7-b", 1,
            {
                "state": "WAITING", "wait_reason": "CONNECTION",
                "remaining_active_budget_seconds": 20.0,
            },
            checkpoint_values=_checkpoint(
                "exec-r7-b", "session-r7-b", task_id=None, revision=2
            ),
            pending_invocations=_pending(),
        )
        execution = await store.load_execution("exec-r7-b")
        assert execution.state == "WAITING"
        assert execution.revision == 2
        assert execution.current_checkpoint_id == "exec-r7-b:checkpoint:2"
    finally:
        await engine.dispose()
