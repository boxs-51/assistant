import structlog

from .base import AuthenticatorInterface
from ..services.token_service import TokenService
from ...schemas.identity import Identity
from ...storage.core.unit_of_work import SqlAlchemyUnitOfWork
from typing import Callable

logger = structlog.get_logger(__name__)

class JWTAuthenticator(AuthenticatorInterface):
    def __init__(self, token_service: TokenService, uow_factory: Callable[[], SqlAlchemyUnitOfWork]):
        self.token_service = token_service
        self.uow_factory = uow_factory

    def can_handle(self, token: str) -> bool:
        """Mặc định xử lý bất kỳ token nào không phải là API key."""
        return not ((token.startswith("sk_") or token.startswith("ak_")) and token.count("_") >= 2)

    async def authenticate(self, token: str) -> Identity:
        """Ủy quyền cho TokenService để xác thực JWT."""
        logger.debug("Attempting JWT authentication via authenticator strategy.")
        # 1. Xác thực JWT và lấy Identity cơ bản (chưa có permissions)
        base_identity = self.token_service.verify_access_token(token)

        # 2. Làm giàu Identity với permissions từ DB
        async with self.uow_factory() as uow:
            permissions = await uow.permissions.get_permissions_for_role_names(base_identity.roles)
        
        # 3. Tạo Identity cuối cùng với đầy đủ thông tin
        # Sử dụng model_copy(update=...) để tạo một bản sao mới vì Identity là immutable
        final_identity = base_identity.model_copy(update={"permissions": list(permissions)})

        return final_identity