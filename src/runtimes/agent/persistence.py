from typing import Any, Dict, List, Optional

from ...infrastructure.storage.repositories.agent import AgentRepository


class DurableAgentStore:
    """Adapter that persists multi-agent records through the existing UoW."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def create_session(self, session_id: str, owner_user_id: str, agent_ids: List[str]):
        async with self.uow_factory() as uow:
            record = await uow.agents.create_session(session_id, owner_user_id, agent_ids)
            await uow.commit()
            return record

    async def save_message(self, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.save_message(values)
            await uow.commit()
            return record

    async def save_task(self, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.save_task(values)
            await uow.commit()
            return record

    async def save_execution(self, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.save_execution(values)
            await uow.commit()
            return record

    async def update_execution(self, execution_id: str, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.update_execution(execution_id, values)
            await uow.commit()
            return record

    async def update_task(self, task_id: str, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.update_task(task_id, values)
            await uow.commit()
            return record
