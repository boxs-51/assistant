from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentExecutionRecord,
    AgentTaskBranchRecord,
    AgentTaskRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.agent import AgentRepository


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _execution(execution_id: str, *, task_id: str, branch_id: str, state: str):
    return AgentExecutionRecord(
        id=execution_id,
        session_id="session-r9-a",
        agent_id="agent-r9",
        task_id=task_id,
        branch_id=branch_id,
        correlation_id="corr-r9",
        state=state,
        revision=3 if state != "RUNNING" else 1,
        request={},
    )


@pytest.mark.asyncio
async def test_r9_a_retry_admission_repository_round_trip_and_lookup():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            repo = AgentRepository(session)
            session.add(
                AgentTaskRecord(
                    id="task-r9-a",
                    session_id="session-r9-a",
                    created_by="user-r9",
                    assigned_agent_id="agent-r9",
                    status="RUNNING",
                    input={},
                )
            )
            session.add(
                AgentTaskBranchRecord(
                    branch_id="branch-r9-a",
                    task_id="task-r9-a",
                    resolution_state="OPEN",
                    revision=0,
                    created_by="user-r9",
                )
            )
            session.add(
                _execution(
                    "exec-r9-source",
                    task_id="task-r9-a",
                    branch_id="branch-r9-a",
                    state="FAILED",
                )
            )
            session.add(
                _execution(
                    "exec-r9-target",
                    task_id="task-r9-a",
                    branch_id="branch-r9-a",
                    state="RUNNING",
                )
            )
            await session.flush()

            receipt = await repo.save_task_retry_admission(
                {
                    "task_id": "task-r9-a",
                    "retry_request_id": "retry-request-r9-a",
                    "plan_fingerprint": "a" * 64,
                    "branch_id": "branch-r9-a",
                    "source_execution_id": "exec-r9-source",
                    "source_checkpoint_id": None,
                    "execution_id": "exec-r9-target",
                    "created_by": "user-r9",
                }
            )
            assert receipt.execution_id == "exec-r9-target"
            await session.commit()

        async with sessions() as session:
            repo = AgentRepository(session)
            by_request = await repo.get_task_retry_admission(
                "task-r9-a",
                "retry-request-r9-a",
            )
            by_execution = await repo.get_task_retry_admission_by_execution(
                "exec-r9-target"
            )
            locked = await repo.get_task_retry_admission_for_update(
                "task-r9-a",
                "retry-request-r9-a",
            )

            assert by_request is not None
            assert by_execution is not None
            assert locked is not None
            assert by_request.execution_id == "exec-r9-target"
            assert by_execution.retry_request_id == "retry-request-r9-a"
            assert locked.plan_fingerprint == "a" * 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_a_task_scoped_created_resume_claim_query_excludes_other_states_and_tasks():
    engine, sessions = await _database()
    try:
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        async with sessions() as session:
            repo = AgentRepository(session)

            for task_id, branch_id in (
                ("task-r9-a", "branch-r9-a"),
                ("task-r9-b", "branch-r9-b"),
            ):
                session.add(
                    AgentTaskRecord(
                        id=task_id,
                        session_id=f"session-{task_id}",
                        created_by="user-r9",
                        assigned_agent_id="agent-r9",
                        status="WAITING",
                        input={},
                    )
                )
                session.add(
                    AgentTaskBranchRecord(
                        branch_id=branch_id,
                        task_id=task_id,
                        resolution_state="OPEN",
                        revision=0,
                        created_by="user-r9",
                    )
                )

            session.add(
                _execution(
                    "exec-claim-a1",
                    task_id="task-r9-a",
                    branch_id="branch-r9-a",
                    state="WAITING",
                )
            )
            session.add(
                _execution(
                    "exec-claim-a2",
                    task_id="task-r9-a",
                    branch_id="branch-r9-a",
                    state="WAITING",
                )
            )
            session.add(
                _execution(
                    "exec-claim-b1",
                    task_id="task-r9-b",
                    branch_id="branch-r9-b",
                    state="WAITING",
                )
            )
            await session.flush()

            for claim_id, execution_id, state in (
                ("claim-a-created", "exec-claim-a1", "CREATED"),
                ("claim-a-rejected", "exec-claim-a2", "REJECTED"),
                ("claim-b-created", "exec-claim-b1", "CREATED"),
            ):
                values = {
                    "claim_id": claim_id,
                    "execution_id": execution_id,
                    "checkpoint_id": f"cp-{claim_id}",
                    "resume_request_id": f"request-{claim_id}",
                    "expected_execution_revision": 3,
                    "user_id": "user-r9",
                    "client_id": "client-r9",
                    "connection_id": "connection-r9",
                    "wait_reason": "CONNECTION",
                    "trigger_type": "CLIENT_RECONNECT",
                    "state": state,
                    "revision": 0,
                    "plan_fingerprint": "b" * 64,
                    "claim_expires_at": expires,
                    "metadata_json": {},
                }
                if state == "REJECTED":
                    values["rejected_at"] = datetime.now(timezone.utc)
                await repo.save_resume_claim(values)

            await session.commit()

        async with sessions() as session:
            repo = AgentRepository(session)
            claims = await repo.list_created_resume_claims_for_task_for_update(
                "task-r9-a"
            )
            assert [claim.claim_id for claim in claims] == [
                "claim-a-created"
            ]
    finally:
        await engine.dispose()
