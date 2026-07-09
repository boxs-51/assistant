# authentication/manager.py
import hashlib
import secrets
from fastapi import Request
import structlog

from .exceptions import AuthenticationError, InvalidCredentialsError
from ..schemas.identity import Identity
from .jwt import JwtHelper
from .permission import get_permissions_for_roles
from ..storage.repositories.api_keys import APIKeyRepository
from ..storage.repositories.users import UserRepository
from ..storage.repositories.sessions import SessionRepository
from ..config import settings

logger = structlog.get_logger(__name__)

class AuthenticationManager:
    def __init__(self, user_repo: UserRepository,
                  api_key_repo: APIKeyRepository, 
                  session_repo: SessionRepository):
        self.config = settings
        self.user_repo = user_repo
        self.api_key_repo = api_key_repo
        self.session_repo = session_repo
        self.jwt = JwtHelper(self.config)

    async def authenticate(self, request: Request) -> Identity:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise InvalidCredentialsError("Missing or malformed Authorization header")

        token = auth_header.split(" ", 1)[1]

        # 1. Ưu tiên kiểm tra Admin API Key trước
        if token.startswith("ak_") and token.count("_") >= 2:
            return await self._verify_admin_api_key(token)

        # 2. Kiểm tra API Key thông thường của User
        if token.startswith("sk_") and token.count("_") >= 2:
            return await self._verify_api_key(token)

        # 3. Nếu không phải API Key, thử xác thực bằng JWT
        return await self._verify_jwt(token)

    async def _verify_admin_api_key(self, token: str) -> Identity:
        """Xác thực Admin API Key (ak_[hex]_[body])"""
        try:
            parts = token.split('_', 2)
            prefix = f"{parts[0]}_{parts[1]}"  # ak_xxxx
            key_body = parts[2]
        except ValueError:
            raise InvalidCredentialsError("Invalid Admin key format")

        stored_key_record = await self.api_key_repo.get_by_prefix(prefix)
        if not stored_key_record or stored_key_record.status != "active":
            raise InvalidCredentialsError("Invalid or revoked Admin key")

        incoming_key_hash = hashlib.sha256(key_body.encode()).hexdigest()
        if not secrets.compare_digest(incoming_key_hash, stored_key_record.hashed_key):
            raise InvalidCredentialsError("Invalid Admin key")

        # Admin Key được gán thẳng quyền root hệ thống mà không cần check gói Org
        roles = ["admin"]
        permissions = get_permissions_for_roles(roles)

        return Identity(
            auth_type="admin_key",
            api_key_id=stored_key_record.id,
            organization_id="admin_org",
            application_id=stored_key_record.application_id,
            plan="enterprise",
            roles=roles,
            permissions=list(permissions)
        )

    async def _verify_api_key(self, token: str) -> Identity:
        """Xác thực User API Key thông thường (sk_[hex]_[body])"""
        try:
            parts = token.split('_', 2)
            prefix = f"{parts[0]}_{parts[1]}"  # sk_xxxx
            key_body = parts[2]
        except ValueError:
            raise InvalidCredentialsError("Invalid API key format")

        stored_key_record = await self.api_key_repo.get_by_prefix(prefix)
        if not stored_key_record or stored_key_record.status != "active":
            raise InvalidCredentialsError("Invalid or revoked API key")

        incoming_key_hash = hashlib.sha256(key_body.encode()).hexdigest()
        if not secrets.compare_digest(incoming_key_hash, stored_key_record.hashed_key):
            raise InvalidCredentialsError("Invalid API key")

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