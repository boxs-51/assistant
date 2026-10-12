"""Nonwired AIC facade. No route, worker lease or execution authority."""
from ..domain.schemas.agent_instance import AgentInstanceDenied
from ..domain.schemas.identity import Identity


class AgentInstanceService:
    def __init__(self, uow_factory):
        self._uow_factory = uow_factory

    @staticmethod
    def trusted_user_id(identity) -> str:
        # Deliberately conservative: only a server-authenticated identity with
        # an already validated durable user principal may enter this facade.
        # Identity has no is_authenticated flag. AIC P1A accepts only an
        # already-authenticated JWT principal; API keys require separately
        # signed unambiguous owner mapping, and admin/guest are not users.
        if (
            not isinstance(identity, Identity)
            or identity.auth_type != "jwt"
            or not isinstance(identity.user_id, str)
            or not identity.user_id.strip()
        ):
            raise AgentInstanceDenied("trusted JWT durable user principal required")
        return identity.user_id

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
