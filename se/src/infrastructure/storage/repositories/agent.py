from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from ..interfaces.repository import BaseRepository
from ..models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentMessageRecord,
    AgentResumeClaimRecord,
    AgentSessionMemberRecord,
    AgentSessionRecord,
    AgentTaskRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)


class AgentRepository(BaseRepository):
    """Transactional persistence boundary for multi-agent state."""

    def __init__(self, session):
        self.session = session

    async def create_session(self, session_id: str, owner_user_id: str, agent_ids: List[str]):
        session = AgentSessionRecord(id=session_id, owner_user_id=owner_user_id)
        self.session.add(session)
        for agent_id in agent_ids:
            self.session.add(AgentSessionMemberRecord(session_id=session_id, agent_id=agent_id))
        await self.session.flush()
        return session

    async def get_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        result = await self.session.execute(
            select(AgentSessionRecord).where(AgentSessionRecord.id == session_id)
        )
        return result.scalar_one_or_none()

    async def add_member(self, session_id: str, agent_id: str):
        self.session.add(AgentSessionMemberRecord(session_id=session_id, agent_id=agent_id))
        await self.session.flush()

    async def list_members(self, session_id: str) -> List[str]:
        result = await self.session.execute(
            select(AgentSessionMemberRecord.agent_id).where(
                AgentSessionMemberRecord.session_id == session_id
            )
        )
        return list(result.scalars().all())

    async def save_message(self, values: Dict[str, Any]):
        record = AgentMessageRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_messages(self, session_id: str) -> List[AgentMessageRecord]:
        result = await self.session.execute(
            select(AgentMessageRecord)
            .where(AgentMessageRecord.session_id == session_id)
            .order_by(AgentMessageRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def save_task(self, values: Dict[str, Any]):
        record = AgentTaskRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task(self, task_id: str) -> Optional[AgentTaskRecord]:
        result = await self.session.execute(
            select(AgentTaskRecord).where(AgentTaskRecord.id == task_id)
        )
        return result.scalar_one_or_none()

    async def compare_and_set_task(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentTaskRecord)
            .where(
                AgentTaskRecord.id == task_id,
                AgentTaskRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task(task_id)

    async def save_task_budget(self, values: Dict[str, Any]):
        record = TaskBudgetRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_budget(self, task_id: str):
        result = await self.session.execute(
            select(TaskBudgetRecord).where(
                TaskBudgetRecord.task_id == task_id
            )
        )
        return result.scalar_one_or_none()

    async def compare_and_set_task_budget(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(TaskBudgetRecord)
            .where(
                TaskBudgetRecord.task_id == task_id,
                TaskBudgetRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task_budget(task_id)

    async def save_task_budget_reservation(
        self,
        values: Dict[str, Any],
    ):
        record = TaskBudgetReservationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_budget_reservation(
        self,
        task_id: str,
        kind: str,
        reservation_key: str,
    ):
        result = await self.session.execute(
            select(TaskBudgetReservationRecord).where(
                TaskBudgetReservationRecord.task_id == task_id,
                TaskBudgetReservationRecord.kind == kind,
                TaskBudgetReservationRecord.reservation_key
                == reservation_key,
            )
        )
        return result.scalar_one_or_none()

    async def has_execution_for_task(self, task_id: str) -> bool:
        result = await self.session.execute(
            select(AgentExecutionRecord.id)
            .where(AgentExecutionRecord.task_id == task_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def save_execution(self, values: Dict[str, Any]):
        record = AgentExecutionRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_execution(self, execution_id: str):
        result = await self.session.execute(
            select(AgentExecutionRecord).where(AgentExecutionRecord.id == execution_id)
        )
        return result.scalar_one_or_none()

    async def get_iteration(self, iteration_id: str):
        result = await self.session.execute(
            select(AgentIterationRecord).where(AgentIterationRecord.id == iteration_id)
        )
        return result.scalar_one_or_none()

    async def save_iteration(self, values: Dict[str, Any]):
        record = AgentIterationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_iterations(self, execution_id: str):
        result = await self.session.execute(
            select(AgentIterationRecord)
            .where(AgentIterationRecord.execution_id == execution_id)
            .order_by(AgentIterationRecord.iteration.asc())
        )
        return list(result.scalars().all())

    async def save_tool_call(self, values: Dict[str, Any]):
        record = AgentToolCallRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def save_tool_result(self, values: Dict[str, Any]):
        record = AgentToolResultRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_tool_calls(self, execution_id: str, iteration_id: str | None = None):
        query = select(AgentToolCallRecord).where(AgentToolCallRecord.execution_id == execution_id)
        if iteration_id is not None:
            query = query.where(AgentToolCallRecord.iteration_id == iteration_id)
        query = query.order_by(AgentToolCallRecord.created_at.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_tool_result(self, execution_id: str, tool_call_id: str):
        result = await self.session.execute(
            select(AgentToolResultRecord).where(
                AgentToolResultRecord.execution_id == execution_id,
                AgentToolResultRecord.tool_call_id == tool_call_id
            )
        )
        return result.scalar_one_or_none()

    async def get_tool_call(self, execution_id: str, tool_call_id: str):
        result = await self.session.execute(
            select(AgentToolCallRecord).where(
                AgentToolCallRecord.execution_id == execution_id,
                AgentToolCallRecord.tool_call_id == tool_call_id
            )
        )
        return result.scalar_one_or_none()

    async def update_iteration(self, iteration_id: str, values: Dict[str, Any]):
        record = await self.get_iteration(iteration_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_tool_call(self, tool_call_id: str, values: Dict[str, Any]):
        record = await self.get_tool_call(tool_call_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_execution(self, execution_id: str, values: Dict[str, Any]):
        record = await self.get_execution(execution_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_task(self, task_id: str, values: Dict[str, Any]):
        record = await self.get_task(task_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def compare_and_set_execution(
        self,
        execution_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        """Atomically mutate one execution when its revision still matches."""
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def save_execution_checkpoint(self, values: Dict[str, Any]):
        record = AgentExecutionCheckpointRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_execution_checkpoint(self, checkpoint_id: str):
        result = await self.session.execute(
            select(AgentExecutionCheckpointRecord).where(
                AgentExecutionCheckpointRecord.checkpoint_id == checkpoint_id
            )
        )
        return result.scalar_one_or_none()

    async def list_execution_checkpoints(self, execution_id: str):
        result = await self.session.execute(
            select(AgentExecutionCheckpointRecord)
            .where(AgentExecutionCheckpointRecord.execution_id == execution_id)
            .order_by(AgentExecutionCheckpointRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def save_checkpoint_pending_invocation(
        self,
        values: Dict[str, Any],
    ):
        record = AgentCheckpointPendingInvocationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_checkpoint_pending_invocations(self, checkpoint_id: str):
        result = await self.session.execute(
            select(AgentCheckpointPendingInvocationRecord)
            .where(
                AgentCheckpointPendingInvocationRecord.checkpoint_id
                == checkpoint_id
            )
            .order_by(AgentCheckpointPendingInvocationRecord.ordinal.asc())
        )
        return list(result.scalars().all())

    async def save_resume_claim(self, values: Dict[str, Any]):
        record = AgentResumeClaimRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_resume_claim(self, claim_id: str):
        result = await self.session.execute(
            select(AgentResumeClaimRecord).where(
                AgentResumeClaimRecord.claim_id == claim_id
            )
        )
        return result.scalar_one_or_none()

    async def get_resume_claim_by_request_id(self, resume_request_id: str):
        result = await self.session.execute(
            select(AgentResumeClaimRecord).where(
                AgentResumeClaimRecord.resume_request_id == resume_request_id
            )
        )
        return result.scalar_one_or_none()

    async def compare_and_set_resume_claim(
        self,
        claim_id: str,
        expected_revision: int,
        expected_state: str,
        values: Dict[str, Any],
    ):
        """CAS one ResumeClaim without opening a second transaction.

        R7-D composes this primitive with AgentExecution and TaskBudget CAS in
        the same UnitOfWork.  R7-A intentionally does not expose a high-level
        DurableAgentStore method that would commit the claim independently.
        """

        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentResumeClaimRecord)
            .where(
                AgentResumeClaimRecord.claim_id == claim_id,
                AgentResumeClaimRecord.revision == expected_revision,
                AgentResumeClaimRecord.state == expected_state,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_resume_claim(claim_id)
