# authentication/manager.py
from fastapi import Request
import structlog
from typing import List

from .exceptions import AuthenticationError, InvalidCredentialsError
from ..schemas.identity import Identity
from .authenticators.base import AuthenticatorInterface

logger = structlog.get_logger(__name__)

class AuthenticationManager:
    """
    Lớp điều phối chiến lược xác thực (Strategy Pattern).
    Nó duyệt qua một danh sách các 'authenticators' và sử dụng cái đầu tiên
    có thể xử lý định dạng token được cung cấp.
    """
    def __init__(self, authenticators: List[AuthenticatorInterface]):
        self.authenticators = authenticators

    async def authenticate(self, request: Request) -> Identity:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise InvalidCredentialsError("Missing or malformed Authorization header")

        token = auth_header.split(" ", 1)[1].strip()

        for authenticator in self.authenticators:
            if authenticator.can_handle(token):
                return await authenticator.authenticate(token)

        raise InvalidCredentialsError("No authenticator available for the provided token format.")