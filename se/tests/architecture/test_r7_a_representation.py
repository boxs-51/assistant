from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.agent_execution import AgentExecution
from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.contracts.resume import (
    CheckpointPendingInvocation,
    DurableExecutionCheckpoint,
    ResumeClaim,
    ResumeClaimState,
    ToolResultCommitState,
)


def test_r7_a_execution_domain_has_normalized_pointer_and_durable_binding():
    execution = AgentExecution(
        execution_id="exec-r7-a",
        session_id="session-r7",
        agent_id="agent-r7",
        correlation_id="corr-r7",
        current_checkpoint_id="checkpoint-r7",
        bound_client_id="client-r7",
        bound_connection_id="connection-r7",
        created_at=1.0,
        updated_at=2.0,
    )

    assert execution.current_checkpoint_id == "checkpoint-r7"
    assert execution.bound_client_id == "client-r7"
    assert execution.bound_connection_id == "connection-r7"

    legacy = AgentExecution(
        execution_id="exec-r7-legacy",
        session_id="session-r7",
        agent_id="agent-r7",
        correlation_id="corr-r7-legacy",
        created_at=1.0,
        updated_at=2.0,
    )
    assert legacy.current_checkpoint_id is None
    assert legacy.bound_client_id is None
    assert legacy.bound_connection_id is None


def test_r7_a_contracts_are_immutable_and_have_no_accepted_claim_state():
    checkpoint = DurableExecutionCheckpoint(
        checkpoint_id="checkpoint-r7",
        execution_id="exec-r7",
        execution_revision=4,
        session_id="session-r7",
        iteration=2,
        wait_reason="CONNECTION",
        transcript_snapshot=({"role": "user", "content": "hi"},),
    )
    pending = CheckpointPendingInvocation(
        checkpoint_id=checkpoint.checkpoint_id,
        ordinal=1,
        invocation_id="inv-r7",
        invocation_revision=3,
        tool_call_id="call-r7",
        capability_id="tool.remote",
    )
    claim = ResumeClaim(
        claim_id="claim-r7",
        execution_id=checkpoint.execution_id,
        checkpoint_id=checkpoint.checkpoint_id,
        resume_request_id="resume-request-r7",
        expected_execution_revision=checkpoint.execution_revision,
        user_id="user-r7",
        client_id="client-r7",
        connection_id="connection-r7-k2",
        wait_reason="CONNECTION",
        trigger_type="CONNECTION_RECONNECT",
        plan_fingerprint="a" * 64,
        claim_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    assert pending.ordinal == 1
    assert claim.state is ResumeClaimState.CREATED
    assert "ACCEPTED" not in ResumeClaimState.__members__
    assert set(ToolResultCommitState) == {
        ToolResultCommitState.PROVISIONAL,
        ToolResultCommitState.COMMITTED,
    }
    with pytest.raises(FrozenInstanceError):
        checkpoint.execution_revision = 5


@pytest.mark.asyncio
async def test_r7_a_sql_repository_round_trips_multi_pending_and_claim_cas():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as session:
            repo = AgentRepository(session)
            session.add(
                AgentExecutionRecord(
                    id="exec-r7-repo",
                    session_id="session-r7",
                    agent_id="agent-r7",
                    correlation_id="corr-r7",
                    state="WAITING",
                    wait_reason="CONNECTION",
                    revision=7,
                    request={},
                )
            )
            await session.flush()

            checkpoint = await repo.save_execution_checkpoint(
                {
                    "checkpoint_id": "checkpoint-r7-repo",
                    "execution_id": "exec-r7-repo",
                    "execution_revision": 7,
                    "session_id": "session-r7",
                    "iteration": 3,
                    "wait_reason": "CONNECTION",
                    "transcript_snapshot": [],
                    "metadata_json": {},
                }
            )
            for ordinal in range(2):
                await repo.save_checkpoint_pending_invocation(
                    {
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "ordinal": ordinal,
                        "invocation_id": f"inv-{ordinal}",
                        "invocation_revision": ordinal + 10,
                        "tool_call_id": f"call-{ordinal}",
                        "capability_id": f"tool.{ordinal}",
                    }
                )

            expires = datetime.now(timezone.utc) + timedelta(seconds=30)
            claim = await repo.save_resume_claim(
                {
                    "claim_id": "claim-r7-repo",
                    "execution_id": "exec-r7-repo",
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "resume_request_id": "request-r7-repo",
                    "expected_execution_revision": 7,
                    "user_id": "user-r7",
                    "client_id": "client-r7",
                    "connection_id": "connection-k2",
                    "wait_reason": "CONNECTION",
                    "trigger_type": "CONNECTION_RECONNECT",
                    "state": "CREATED",
                    "revision": 0,
                    "plan_fingerprint": "b" * 64,
                    "claim_expires_at": expires,
                    "metadata_json": {},
                }
            )

            pending = await repo.list_checkpoint_pending_invocations(
                checkpoint.checkpoint_id
            )
            assert [item.ordinal for item in pending] == [0, 1]
            assert [item.invocation_id for item in pending] == ["inv-0", "inv-1"]
            assert (
                await repo.get_resume_claim_by_request_id("request-r7-repo")
            ).claim_id == claim.claim_id

            consumed_at = datetime.now(timezone.utc)
            consumed = await repo.compare_and_set_resume_claim(
                claim.claim_id,
                expected_revision=0,
                expected_state="CREATED",
                values={
                    "state": "CONSUMED",
                    "consumed_at": consumed_at,
                    "consumed_execution_revision": 8,
                },
            )
            assert consumed is not None
            assert consumed.state == "CONSUMED"
            assert consumed.revision == 1
            assert consumed.consumed_execution_revision == 8

            stale = await repo.compare_and_set_resume_claim(
                claim.claim_id,
                expected_revision=0,
                expected_state="CREATED",
                values={"state": "REJECTED", "rejected_at": consumed_at},
            )
            assert stale is None
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r7_a_new_tool_result_defaults_fail_safe_to_provisional():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as session:
            session.add(
                AgentExecutionRecord(
                    id="exec-r7-tool",
                    session_id="session-r7",
                    agent_id="agent-r7",
                    correlation_id="corr-r7-tool",
                    state="RUNNING",
                    request={},
                )
            )
            session.add(
                AgentIterationRecord(
                    id="iteration-r7",
                    execution_id="exec-r7-tool",
                    iteration=1,
                    state="WAITING_TOOL",
                )
            )
            session.add(
                AgentToolResultRecord(
                    id="result-r7",
                    execution_id="exec-r7-tool",
                    iteration_id="iteration-r7",
                    tool_call_id="call-r7",
                    invocation_id="inv-r7",
                    capability_id="tool.remote",
                    success=True,
                    output={"ok": True},
                )
            )
            await session.commit()

            result = await session.get(AgentToolResultRecord, "result-r7")
            assert result.commit_state == "PROVISIONAL"
    finally:
        await engine.dispose()
