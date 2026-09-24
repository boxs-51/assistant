from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentTaskBranchRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
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
from se.src.runtimes.agent.gc_dry_run import (
    AgentGcDryRunService,
    GcDryRunClassification,
)


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._ctx.__aexit__(exc_type, exc, tb)


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _budget(task_id: str, *, closed: bool) -> TaskBudgetRecord:
    return TaskBudgetRecord(
        task_id=task_id,
        state="CLOSED" if closed else "OPEN",
        max_total_executions=8,
        max_active_executions=4,
        max_active_branches=4,
        max_parallel_agents=4,
        max_total_tool_calls=100,
        max_total_inference_calls=100,
        max_total_tokens=None,
        max_total_cost_usd=None,
        max_delegation_depth=8,
        policy_version="r11-f1b-test",
        policy_fingerprint="a" * 64,
        used_executions=1,
        active_executions=0 if closed else 1,
        active_branches=0,
        active_parallel_agents=0 if closed else 1,
        used_tool_calls=0,
        used_inference_calls=0,
        used_tokens=0,
        used_cost_usd=0,
        closed_at=datetime.now(timezone.utc) if closed else None,
    )


async def _seed_active_waiting(sessions):
    async with sessions() as session:
        task_id = "task-active"
        execution_id = "exec-active"
        checkpoint_id = "cp-active"
        invocation_id = "inv-active"
        session.add(
            AgentTaskRecord(
                id=task_id,
                session_id="session-active",
                created_by="user-1",
                assigned_agent_id="agent-1",
                status="RUNNING",
                input={},
            )
        )
        session.add(_budget(task_id, closed=False))
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id="session-active",
                agent_id="agent-1",
                task_id=task_id,
                correlation_id="corr-active",
                state="WAITING",
                wait_reason="CONNECTION",
                current_checkpoint_id=checkpoint_id,
                revision=3,
                request={},
            )
        )
        session.add(
            AgentExecutionCheckpointRecord(
                checkpoint_id=checkpoint_id,
                execution_id=execution_id,
                execution_revision=3,
                session_id="session-active",
                task_id=task_id,
                iteration=1,
                wait_reason="CONNECTION",
                transcript_snapshot=[],
                metadata_json={},
            )
        )
        session.add(
            AgentCheckpointPendingInvocationRecord(
                checkpoint_id=checkpoint_id,
                ordinal=0,
                invocation_id=invocation_id,
                invocation_revision=1,
                tool_call_id="call-active",
                capability_id="tool.remote",
                idempotency="IDEMPOTENT",
                observed_remote_outcome_state="IN_FLIGHT",
            )
        )
        session.add(
            CapabilityInvocationRecord(
                invocation_id=invocation_id,
                capability_id="tool.remote",
                kind="TOOL",
                execution_mode="REMOTE",
                idempotency="IDEMPOTENT",
                remote_outcome_state="IN_FLIGHT",
                state="RUNNING",
                execution_id=execution_id,
                tool_call_id="call-active",
                arguments={},
            )
        )
        await session.commit()
    return task_id


async def _seed_terminal_candidate(sessions, *, unsafe_invocation: bool = False):
    async with sessions() as session:
        task_id = "task-terminal-unsafe" if unsafe_invocation else "task-terminal"
        execution_id = f"{task_id}:exec"
        checkpoint_id = f"{task_id}:cp"
        invocation_id = f"{task_id}:inv"
        chunk_id = f"{task_id}:chunk"
        payload_ref = f"{task_id}:payload"
        transcript_ref = f"{task_id}:transcript"

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
        session.add(_budget(task_id, closed=True))
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
                remote_outcome_state=(
                    "OUTCOME_UNKNOWN"
                    if unsafe_invocation
                    else "TERMINAL_COMMITTED"
                ),
                state="RUNNING" if unsafe_invocation else "COMPLETED",
                execution_id=execution_id,
                tool_call_id=f"{task_id}:call",
                arguments={},
                completed_at=(
                    None
                    if unsafe_invocation
                    else datetime.now(timezone.utc)
                ),
            )
        )
        await session.commit()
    return task_id


async def _counts(sessions):
    models = (
        AgentTaskRecord,
        TaskBudgetRecord,
        AgentExecutionRecord,
        AgentExecutionCheckpointRecord,
        AgentCheckpointPendingInvocationRecord,
        CapabilityInvocationRecord,
        AgentTranscriptRepresentationRecord,
        AgentTranscriptPayloadNodeRecord,
        AgentTranscriptChunkRecord,
    )
    async with sessions() as session:
        result = {}
        for model in models:
            result[model.__tablename__] = int(
                (
                    await session.execute(
                        select(func.count()).select_from(model)
                    )
                ).scalar_one()
            )
        return result


@pytest.mark.asyncio
async def test_r11_f1b_active_graph_is_retain_deterministic_and_read_only():
    engine, sessions = await _database()
    try:
        task_id = await _seed_active_waiting(sessions)
        service = AgentGcDryRunService(lambda: _Uow(sessions))
        before = await _counts(sessions)

        first = await service.classify_task(task_id)
        second = await service.classify_task(task_id)
        after = await _counts(sessions)

        assert not first.failed_closed
        assert not first.has_candidates
        assert first.items == second.items
        assert first.fingerprint == second.fingerprint
        assert before == after

        by_kind = {item.row_kind: item for item in first.items}
        assert (
            by_kind["checkpoint_pending_invocation"].classification
            is GcDryRunClassification.RETAIN
        )
        assert (
            by_kind["capability_invocation"].classification
            is GcDryRunClassification.RETAIN
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_terminal_policy_can_candidate_task_rows_but_retains_transcript():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        service = AgentGcDryRunService(lambda: _Uow(sessions))
        before = await _counts(sessions)

        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )
        after = await _counts(sessions)

        assert not report.failed_closed
        assert report.has_candidates
        assert before == after

        task_owned_kinds = {
            "task",
            "task_budget",
            "execution",
            "checkpoint",
            "capability_invocation",
        }
        for item in report.items:
            if item.row_kind in task_owned_kinds:
                assert (
                    item.classification
                    is GcDryRunClassification.CANDIDATE
                )

        transcript_items = [
            item
            for item in report.items
            if item.row_kind.startswith("transcript_")
        ]
        assert {item.row_kind for item in transcript_items} == {
            "transcript_representation",
            "transcript_payload",
            "transcript_chunk",
        }
        assert all(
            item.classification is GcDryRunClassification.RETAIN
            for item in transcript_items
        )
        assert all(
            item.reason_code == "TRANSCRIPT_REACHABILITY"
            for item in transcript_items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_unsafe_remote_side_effect_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(
            sessions,
            unsafe_invocation=True,
        )
        service = AgentGcDryRunService(lambda: _Uow(sessions))

        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert all(
            item.classification is GcDryRunClassification.FAIL_CLOSED
            for item in report.items
        )
        assert any(
            "unsafe_remote_outcome:OUTCOME_UNKNOWN"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_external_null_task_semantic_reference_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        async with sessions() as session:
            session.add(
                AgentExecutionRecord(
                    id="external-null-task-exec",
                    session_id="external-session",
                    agent_id="agent-external",
                    task_id=None,
                    parent_execution_id=f"{task_id}:exec",
                    correlation_id="external-corr",
                    state="COMPLETED",
                    request={},
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "external_execution_semantic_reference"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_pending_invocation_identity_mismatch_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_active_waiting(sessions)
        async with sessions() as session:
            invocation = await session.get(
                CapabilityInvocationRecord,
                "inv-active",
            )
            invocation.execution_id = "wrong-execution"
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(task_id)

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "invocation_execution_mismatch"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_cross_task_branch_lineage_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        candidate_execution_id = f"{task_id}:exec"
        candidate_checkpoint_id = f"{task_id}:cp"
        async with sessions() as session:
            session.add(
                AgentTaskBranchRecord(
                    branch_id="candidate-branch",
                    task_id=task_id,
                    parent_branch_id=None,
                    base_execution_id=None,
                    base_checkpoint_id=None,
                    current_execution_id=candidate_execution_id,
                    resolution_state="ADOPTED",
                    created_by="user-1",
                )
            )
            session.add(
                AgentTaskRecord(
                    id="external-task",
                    session_id="external-session",
                    created_by="user-2",
                    assigned_agent_id="agent-2",
                    status="COMPLETED",
                    input={},
                    output={"ok": True},
                )
            )
            await session.flush()
            session.add(
                AgentTaskBranchRecord(
                    branch_id="external-child-branch",
                    task_id="external-task",
                    parent_branch_id="candidate-branch",
                    base_execution_id=candidate_execution_id,
                    base_checkpoint_id=candidate_checkpoint_id,
                    current_execution_id=None,
                    resolution_state="DISCARDED",
                    created_by="user-2",
                )
            )
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "external_branch_lineage_reference"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


async def _seed_external_execution_graph(sessions, suffix: str):
    async with sessions() as session:
        task_id = f"external-task-{suffix}"
        execution_id = f"external-exec-{suffix}"
        iteration_id = f"external-iter-{suffix}"
        session.add(
            AgentTaskRecord(
                id=task_id,
                session_id=f"external-session-{suffix}",
                created_by="user-external",
                assigned_agent_id="agent-external",
                status="COMPLETED",
                input={},
                output={"ok": True},
            )
        )
        session.add(
            AgentExecutionRecord(
                id=execution_id,
                session_id=f"external-session-{suffix}",
                agent_id="agent-external",
                task_id=task_id,
                correlation_id=f"external-corr-{suffix}",
                state="COMPLETED",
                request={},
                result={"ok": True},
                completed_at=datetime.now(timezone.utc),
            )
        )
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
    return task_id, execution_id, iteration_id


@pytest.mark.asyncio
async def test_r11_f1b_external_tool_call_to_candidate_invocation_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        _, execution_id, iteration_id = await _seed_external_execution_graph(
            sessions,
            "tool-call",
        )
        async with sessions() as session:
            session.add(
                AgentToolCallRecord(
                    id="external-tool-call",
                    execution_id=execution_id,
                    iteration_id=iteration_id,
                    invocation_id=f"{task_id}:inv",
                    tool_call_id="external-call",
                    capability_id="tool.remote",
                    arguments={},
                    status="COMPLETED",
                )
            )
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "external_agent_tool_call_invocation_reference"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_external_tool_result_to_candidate_invocation_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        _, execution_id, iteration_id = await _seed_external_execution_graph(
            sessions,
            "tool-result",
        )
        async with sessions() as session:
            session.add(
                AgentToolResultRecord(
                    id="external-tool-result",
                    execution_id=execution_id,
                    iteration_id=iteration_id,
                    tool_call_id="external-result-call",
                    invocation_id=f"{task_id}:inv",
                    capability_id="tool.remote",
                    success=True,
                    output={"ok": True},
                    commit_state="COMMITTED",
                    attempt=1,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "external_agent_tool_result_invocation_reference"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_f1b_external_checkpoint_pending_to_candidate_invocation_fails_closed():
    engine, sessions = await _database()
    try:
        task_id = await _seed_terminal_candidate(sessions)
        external_task_id, execution_id, _ = await _seed_external_execution_graph(
            sessions,
            "pending",
        )
        checkpoint_id = "external-pending-checkpoint"
        async with sessions() as session:
            session.add(
                AgentExecutionCheckpointRecord(
                    checkpoint_id=checkpoint_id,
                    execution_id=execution_id,
                    execution_revision=0,
                    session_id="external-session-pending",
                    task_id=external_task_id,
                    iteration=1,
                    wait_reason="CONNECTION",
                    transcript_snapshot=[],
                    metadata_json={},
                )
            )
            session.add(
                AgentCheckpointPendingInvocationRecord(
                    checkpoint_id=checkpoint_id,
                    ordinal=0,
                    invocation_id=f"{task_id}:inv",
                    invocation_revision=1,
                    tool_call_id="external-pending-call",
                    capability_id="tool.remote",
                    idempotency="IDEMPOTENT",
                    observed_remote_outcome_state="TERMINAL_COMMITTED",
                )
            )
            await session.commit()

        service = AgentGcDryRunService(lambda: _Uow(sessions))
        report = await service.classify_task(
            task_id,
            policy_eligible_terminal=True,
        )

        assert report.failed_closed
        assert not report.has_candidates
        assert any(
            "external_checkpoint_pending_invocation_reference"
            in item.root_or_edge_source
            for item in report.items
        )
    finally:
        await engine.dispose()
