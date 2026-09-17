from typing import Callable

import structlog

from .base import AuthenticatorInterface
from ..exceptions import InvalidCredentialsError
from ..services.token_service import TokenService
from .....domain.schemas.identity import Identity
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork

logger = structlog.get_logger(__name__)


class JWTAuthenticator(AuthenticatorInterface):
    def __init__(
        self,
        token_service: TokenService,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    ) -> None:
        self.token_service = token_service
        self.uow_factory = uow_factory

    def can_handle(self, token: str) -> bool:
        return not (
            (token.startswith("sk_") or token.startswith("ak_"))
            and token.count("_") >= 2
        )

    async def authenticate(self, token: str) -> Identity:
        logger.debug("Attempting JWT authentication via authenticator strategy.")
        base_identity = self.token_service.verify_access_token(token)

        async with self.uow_factory() as uow:
            user = await uow.users.get_by_id(base_identity.user_id)
            if user is None:
                raise InvalidCredentialsError("Token subject does not exist.")
            if base_identity.auth_type == "guest":
                if user.status != "guest":
                    raise InvalidCredentialsError(
                        "Guest principal is no longer active."
                    )
                return base_identity.model_copy(
                    update={"permissions": ["read:public"]}
                )
            permissions = await uow.permissions.get_permissions_for_role_names(
                base_identity.roles
            )

        return base_identity.model_copy(update={"permissions": list(permissions)})
