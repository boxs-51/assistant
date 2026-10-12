"""Nonwired AIC facade. No route, worker lease or execution authority."""
from ....domain.schemas.agent_instance import AgentInstanceDenied


class AgentInstanceService:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    @staticmethod
    def trusted_user_id(identity) -> str:
        # Deliberately conservative: only a server-authenticated identity with
        # an already validated durable user principal may enter this facade.
        if (identity is None or not getattr(identity, "is_authenticated", False)
                or getattr(identity, "is_guest", False)
                or not getattr(identity, "user_id", None)
                or getattr(identity, "auth_type", None) in ("guest", "admin_key", "api_key")):
            raise AgentInstanceDenied("durable authenticated user principal required")
        return str(identity.user_id)

    async def create(self, identity, definition_id: str, definition_version: str):
        owner = self.trusted_user_id(identity)
        async with self._uow_factory() as uow:
            key = await uow.agent_instances.create(owner, definition_id, definition_version)
            await uow.commit()
            return key

    async def resolve(self, identity, instance_id: str):
        owner = self.trusted_user_id(identity)
        async with self._uow_factory() as uow:
            instance = await uow.agent_instances.resolve(owner, instance_id)
            return dict(agent_instance_id=instance.agent_instance_id,
                        owner_user_id=instance.owner_user_id,
                        definition_id=instance.definition_id,
                        definition_version=instance.definition_version,
                        generation=instance.generation, state=instance.state)

    async def suspend_or_retire(self, identity, instance_id: str, generation: int, *, retire=False):
        owner = self.trusted_user_id(identity)
        async with self._uow_factory() as uow:
            await uow.agent_instances.transition(owner, instance_id, generation, retire=retire)
            await uow.commit()
