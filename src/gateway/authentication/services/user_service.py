import structlog

from ...schemas.auth import UserMeSchema
from ...schemas.identity import Identity
from ..exceptions import InvalidCredentialsError
from ...storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable

logger = structlog.get_logger(__name__)

class UserService:
    def __init__(self, uow_factory: Callable[[], SqlAlchemyUnitOfWork]):
        self.uow_factory = uow_factory

    async def get_current_user_info(self, identity: Identity) -> UserMeSchema:
        async with self.uow_factory() as uow:
            if not identity.user_id:
                raise InvalidCredentialsError("Cannot get user info without a valid user session (JWT).")

            user = await uow.users.get_by_id(identity.user_id)
            if not user:
                raise InvalidCredentialsError("User not found.")

            return UserMeSchema(id=user.id, email=user.email, name=user.name, roles=identity.roles)