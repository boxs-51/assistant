import structlog

from .base import AuthenticatorInterface
from ..services.api_key_service import APIKeyService
from .....domain.schemas.identity import Identity

logger = structlog.get_logger(__name__)

class APIKeyAuthenticator(AuthenticatorInterface):
    def __init__(self, api_key_service: APIKeyService):
        self.api_key_service = api_key_service

    def can_handle(self, token: str) -> bool:
        """Chỉ xử lý các token có prefix là 'sk_' hoặc 'ak_'."""
        return (token.startswith("sk_") or token.startswith("ak_")) and token.count("_") >= 2

    async def authenticate(self, token: str) -> Identity:
        """Ủy quyền cho APIKeyService để xác thực key."""
        logger.debug("Attempting API Key authentication via authenticator strategy.")
        return await self.api_key_service.verify_key(token)