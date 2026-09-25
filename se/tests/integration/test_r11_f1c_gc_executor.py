from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentTaskBranchRecord,
    AgentTaskRecord,
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
    TaskBudgetRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.runtimes.agent import gc_executor
from se.src.runtimes.agent.gc_dry_run import AgentGcDryRunService
from se.src.runtimes.agent.gc_executor import (
    AgentGcCollectionError,
    AgentGcExecutor,
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
            if exc_type is not None and self.session.in_transaction():
                await self.session.rollback()
        finally:
            return await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _budget(task_id: str) -> TaskBudgetRecord:
    return TaskBudgetRecord(
        task_id=task_id,
        state="CLOSED",
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=4,
        max_parallel_agents=4,
        max_total_tool_calls=100,
        max_total_inference_calls=100,
        max_total_tokens=None,
        max_total_cost_usd=None,
        max_delegation_depth=8,
        policy_version="r11-f1c-test",
        policy_fingerprint="a" * 64,
        used_executions=1,
        active_executions=0,
        active_branches=0,
        active_parallel_agents=0,
        used_tool_calls=0,
        used_inference_calls=0,
        used_tokens=0,
        used_cost_usd=0,
        closed_at=datetime.now(timezone.utc),
    )


async def _seed_terminal_candidate(sessions, suffix: str = "main"):
    task_id = f"task-f1c-{suffix}"
    execution_id = f"{task_id}:exec"
    checkpoint_id = f"{task_id}:cp"
    invocation_id = f"{task_id}:inv"
    chunk_id = f"{task_id}:chunk"
    payload_ref = f"{task_id}:payload"
    transcript_ref = f"{task_id}:transcript"

    async with sessions() as session:
        session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"{task_id}:session",
                created_by="user-1",
                assigned_agent_id="agent-1",
                status="COMPLETED",
                input={},
                output={"ok": True},
            )
        )
        session.add(_budget(task_id))
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"{task_id}:session",
                agent_id="agent-1",
                task_id=task_id,
                correlation_id=f"{task_id}:corr",
                state="COMPLETED",
                current_checkpoint_id=checkpoint_id,
                revision=4,
                request={},
                result={"ok": True},
                completed_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            AgentTranscriptChunkRecord(
                chunk_id=chunk_id,
                payload=[{"role": "assistant", "content": "done"}],
                message_count=1,
                canonical_bytes=32,
            )
        )
        session.add(
            AgentTranscriptPayloadNodeRecord(
                payload_root_ref=payload_ref,
                parent_payload_root_ref=None,
                chunk_id=chunk_id,
                logical_message_count=1,
            )
        )
        session.add(
            AgentTranscriptRepresentationRecord(
                transcript_ref=transcript_ref,
                transcript_version=0,
                kind="FULL",
                parent_transcript_ref=None,
                parent_transcript_version=None,
                delta_depth=0,
                logical_message_count=1,
                logical_transcript_fingerprint="b" * 64,
                payload_root_ref=payload_ref,
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=checkpoint_id,
                execution_id=execution_id,
                execution_revision=4,
                session_id=f"{task_id}:session",
                task_id=task_id,
                iteration=1,
                wait_reason="BUDGET",
                transcript_snapshot=None,
                transcript_ref=transcript_ref,
                transcript_version=0,
                metadata_json={},
            )
        )
        session.add(
            CapabilityInvocationRecord(
                invocation_id=invocation_id,
                capability_id="tool.remote",
                kind="TOOL",
                execution_mode="REMOTE",
                idempotency="IDEMPOTENT",
                remote_outcome_state="TERMINAL_COMMITTED",
                state="COMPLETED",
                execution_id=execution_id,
                tool_call_id=f"{task_id}:call",
                arguments={},
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "checkpoint_id": checkpoint_id,
        "invocation_id": invocation_id,
        "chunk_id": chunk_id,
        "payload_ref": payload_ref,
        "transcript_ref": transcript_ref,
    }


async def _count(session, model) -> int:
    return int(
        (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_r11_f1c_collects_terminal_task_atomically_and_retains_transcript():
    engine, sessions = await _database()
    try:
        ids = await _seed_terminal_candidate(sessions)
        dry_run = AgentGcDryRunService(lambda: _Uow(sessions))
        plan = await dry_run.classify_task(
            ids["task_id"],
            policy_eligible_terminal=True,
        )

        result = await AgentGcExecutor(lambda: _Uow(sessions)).collect_task(
            ids["task_id"],
            expected_fingerprint=plan.fingerprint,
        )

        assert not result.already_collected
        assert result.total_deleted == 5
        assert dict(result.deleted_rows) == {
            "capability_invocation": 1,
            "checkpoint": 1,
            "execution": 1,
            "task": 1,
            "task_budget": 1,
        }

        async with sessions() as session:
            assert await session.get(AgentTaskRecord, ids["task_id"]) is None
            assert (
                await session.get(AgentExecutionRecord, ids["execution_id"])
                is None
            )
            assert (
                await session.get(
                    AgentExecutionCheckpointRecord,
                    ids["checkpoint_id"],
                )
                is None
            )
            assert (
                await session.get(
                    CapabilityInvocationRecord,
                    ids["invocation_id"],
                )
                is None
            )
            assert (
                await session.get(
                    AgentTranscriptRepresentationRecord,
                    (ids["transcript_ref"], 0),
                )
                is not None
            )
            assert (
                await session.get(
                    AgentTranscriptPayloadNodeRecord,
                    ids["payload_ref"],
                )
                is not None
            )
            assert (
                await session.get(
                    AgentTranscriptChunkRecord,
                    ids["chunk_id"],
                )
                is not None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_stale_dry_run_fingerprint_fails_closed_without_deletion():
    engine, sessions = await _database()
    try:
        ids = await _seed_terminal_candidate(sessions, "stale")
        dry_run = AgentGcDryRunService(lambda: _Uow(sessions))
        plan = await dry_run.classify_task(
            ids["task_id"],
            policy_eligible_terminal=True,
        )

        async with sessions() as session:
            session.add(
                AgentTaskBranchRecord(
                    branch_id=f"{ids['task_id']}:branch",
                    task_id=ids["task_id"],
                    parent_branch_id=None,
                    base_execution_id=None,
                    base_checkpoint_id=None,
                    current_execution_id=None,
                    resolution_state="DISCARDED",
                    created_by="user-1",
                )
            )
            await session.commit()

        with pytest.raises(
            AgentGcCollectionError,
            match="dry-run fingerprint is stale",
        ):
            await AgentGcExecutor(lambda: _Uow(sessions)).collect_task(
                ids["task_id"],
                expected_fingerprint=plan.fingerprint,
            )

        async with sessions() as session:
            assert (
                await session.get(AgentTaskRecord, ids["task_id"])
                is not None
            )
            assert await _count(session, AgentTaskBranchRecord) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_mid_delete_failure_rolls_back_zero_partial_collection(
    monkeypatch,
):
    engine, sessions = await _database()
    try:
        ids = await _seed_terminal_candidate(sessions, "rollback")
        dry_run = AgentGcDryRunService(lambda: _Uow(sessions))
        plan = await dry_run.classify_task(
            ids["task_id"],
            policy_eligible_terminal=True,
        )

        original = gc_executor._delete_exact
        calls = 0

        async def fail_after_first_delete(
            session,
            model,
            predicate,
            expected,
            label,
        ):
            nonlocal calls
            result = await original(
                session,
                model,
                predicate,
                expected,
                label,
            )
            if expected:
                calls += 1
                if calls == 2:
                    raise RuntimeError("injected-mid-delete-failure")
            return result

        monkeypatch.setattr(gc_executor, "_delete_exact", fail_after_first_delete)

        with pytest.raises(RuntimeError, match="injected-mid-delete-failure"):
            await AgentGcExecutor(lambda: _Uow(sessions)).collect_task(
                ids["task_id"],
                expected_fingerprint=plan.fingerprint,
            )

        async with sessions() as session:
            assert (
                await session.get(AgentTaskRecord, ids["task_id"])
                is not None
            )
            assert (
                await session.get(AgentExecutionRecord, ids["execution_id"])
                is not None
            )
            assert (
                await session.get(
                    AgentExecutionCheckpointRecord,
                    ids["checkpoint_id"],
                )
                is not None
            )
            assert (
                await session.get(
                    CapabilityInvocationRecord,
                    ids["invocation_id"],
                )
                is not None
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1c_repeat_collection_is_idempotent_after_success():
    engine, sessions = await _database()
    try:
        ids = await _seed_terminal_candidate(sessions, "repeat")
        executor = AgentGcExecutor(lambda: _Uow(sessions))

        first = await executor.collect_task(ids["task_id"])
        second = await executor.collect_task(ids["task_id"])

        assert not first.already_collected
        assert first.total_deleted == 5
        assert second.already_collected
        assert second.total_deleted == 0
    finally:
        await engine.dispose()
