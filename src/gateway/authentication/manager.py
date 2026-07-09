import hashlib
from fastapi import Request, HTTPException, status
import structlog
from typing import Dict, Any

from ..storage.repositories.api_keys import APIKeyRepository
from ..storage.repositories.users import UserRepository
from ..storage.repositories.sessions import SessionRepository
from .exceptions import AuthenticationError, InvalidCredentialsError
from ..schemas.identity import Identity
from .jwt import JwtHelper
from .permission import get_permissions_for_roles
import secrets

logger = structlog.get_logger(__name__)

class AuthenticationManager:
    def __init__(self, user_repo: UserRepository,
                  api_key_repo: APIKeyRepository, 
                  session_repo: SessionRepository,
                  config: Dict[str, Any]):
        self.config = config
        self.user_repo = user_repo
        self.api_key_repo = api_key_repo
        self.session_repo = session_repo
        self.jwt = JwtHelper(config)

    async def authenticate(self, request: Request) -> Identity:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise InvalidCredentialsError("Missing or malformed Authorization header")

        token = auth_header.split(" ", 1)[1]

        # Ưu tiên kiểm tra API Key trước vì nó phổ biến hơn cho machine-to-machine
        if token.startswith("sk_"):
            return await self._verify_api_key(token)

        # Nếu không phải API Key, thử xác thực bằng JWT
        return await self._verify_jwt(token)

    async def _verify_api_key(self, token: str) -> Identity:
        try:
            prefix, key_body = token.split('_', 1)
        except ValueError:
            raise InvalidCredentialsError("Invalid API key format")

        stored_key_record = await self.api_key_repo.get_by_prefix(prefix)

        if not stored_key_record or stored_key_record.status != "active":
            raise InvalidCredentialsError("Invalid or revoked API key")

        incoming_key_hash = hashlib.sha256(key_body.encode()).hexdigest()
        if not secrets.compare_digest(incoming_key_hash, stored_key_record.hashed_key):
            raise InvalidCredentialsError("Invalid API key")

        # Lấy thông tin từ organization liên quan
        organization = stored_key_record.application.organization
        if not organization:
             raise AuthenticationError(f"Data integrity error: API key {stored_key_record.id} has no associated organization.")

        roles = ["member"]
        permissions = get_permissions_for_roles(roles)

        return Identity(
            auth_type="api_key",
            api_key_id=stored_key_record.id,
            organization_id=organization.id,
            application_id=stored_key_record.application_id,
            plan=organization.plan,
            roles=roles,
            permissions=list(permissions)
        )

    async def _verify_jwt(self, token: str) -> Identity:
        payload = self.jwt.decode_token(token)
        if not payload or payload.get("type") != "access":
            raise InvalidCredentialsError("Invalid or expired JWT")

        user_id = payload.get("sub")
        if not user_id:
            raise InvalidCredentialsError("Invalid JWT payload")

        # TỐI ƯU HÓA: Lấy thông tin trực tiếp từ payload của token, không cần truy vấn DB.
        roles = payload.get("roles", ["member"])
        org_id = payload.get("org_id")
        plan = payload.get("plan", "free")

        permissions = get_permissions_for_roles(roles)

        return Identity(
            auth_type="jwt",
            user_id=user_id,
            organization_id=org_id,
            plan=plan,
            roles=roles,
            permissions=list(permissions)
        )
