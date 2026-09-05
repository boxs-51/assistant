from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from ..interfaces.repository import BaseRepository
from ..models.sql.agent import (
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentMessageRecord,
    AgentSessionMemberRecord,
    AgentSessionRecord,
    AgentTaskRecord,
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