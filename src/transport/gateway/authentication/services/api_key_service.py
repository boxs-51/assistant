import structlog
from typing import Optional, List
import hashlib
import secrets

from .....schemas.auth import APIKeyCreateSchema, APIKeyResponseSchema, APIKeyInfoSchema
from .....schemas.identity import Identity
from .....gateway.authentication import api_key as APIKeyHelper
from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..exceptions import InvalidCredentialsError, AuthenticationError
from typing import Callable

logger = structlog.get_logger(__name__)

class APIKeyService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork]
    ):
        self.uow_factory = uow_factory

    async def create_api_key(self, schema: APIKeyCreateSchema, identity: Identity) -> APIKeyResponseSchema:
        """
        Tạo một API key mới cho người dùng.
        - Tự động tìm hoặc tạo Organization.
        - Tự động tìm hoặc tạo Application.
        - Tạo API key và liên kết.
        """
        user_id = identity.user_id
        if not user_id:
            raise ValueError("Cannot create API key without a user context.")

        async with self.uow_factory() as uow:
            # 1. Tìm hoặc tạo Organization
            orgs = await uow.organizations.get_by_owner_id(user_id)
            if not orgs:
                logger.info("No organization found for user, creating a default one.", user_id=user_id)
                user = await uow.users.get_by_id(user_id)
                org_name = f"{user.name or user.email}'s Organization"
                organization = await uow.organizations.create(name=org_name, owner_id=user_id)
            else:
                organization = orgs[0]

            # 2. Tìm hoặc tạo Application
            application = await uow.applications.create(name=schema.name, organization_id=organization.id)

            # 3. Tạo API Key
            full_key, prefix, hashed_body = APIKeyHelper.generate_api_key()
            new_key_record = await uow.api_keys.create(
                application_id=application.id,
                prefix=prefix,
                hashed_key=hashed_body
            )
            
            await uow.commit()

            logger.info("API Key created successfully", key_id=new_key_record.id, user_id=user_id)

            return APIKeyResponseSchema(
                id=new_key_record.id,
                full_key=full_key,
                prefix=new_key_record.prefix,
                created_at=new_key_record.created_at.isoformat()
            )

    async def list_api_keys(self, identity: Identity) -> List[APIKeyInfoSchema]:
        """
        Liệt kê tất cả các API key thuộc về người dùng.
        """
        async with self.uow_factory() as uow:
            user_id = identity.user_id
            if not user_id:
                raise ValueError("Cannot list API keys without a user context.")

            # 1. Tìm organization của người dùng
            orgs = await uow.organizations.get_by_owner_id(user_id)
            if not orgs:
                return []
            
            organization = orgs[0]

            # 2. Tìm tất cả application trong organization đó
            apps = await uow.applications.get_by_organization_id(organization.id)
            app_ids = [app.id for app in apps]

            # 3. Lấy tất cả các key thuộc các application đó
            keys = await uow.api_keys.get_by_application_ids(app_ids)

            # 4. Map sang DTO để trả về
            return [
                APIKeyInfoSchema(
                    id=key.id,
                    name=key.application.name,
                    prefix=key.prefix,
                    created_at=key.created_at.isoformat(),
                    status=key.status
                ) for key in keys
            ]

    async def revoke_api_key(self, key_id: str, identity: Identity) -> bool:
        """Thu hồi một API key và xác thực quyền sở hữu."""
        async with self.uow_factory() as uow:
            user_id = identity.user_id
            key_to_revoke = await uow.api_keys.get_by_id_and_owner(key_id, user_id)
            if not key_to_revoke:
                return False # Không tìm thấy key hoặc không có quyền
            
            success = await uow.api_keys.revoke(key_id)
            if success:
                await uow.commit()
            return success

    async def verify_key(self, token: str) -> Identity:
        """
        Xác thực một API key (cả admin và user) và trả về một đối tượng Identity.
        """
        if token.startswith("ak_"):
            return await self._verify_admin_api_key(token)
        elif token.startswith("sk_"):
            return await self._verify_standard_api_key(token)
        else:
            raise InvalidCredentialsError("Key format is not recognized.")

    async def _verify_admin_api_key(self, token: str) -> Identity:
        """Xác thực Admin API Key (ak_[hex]_[body])"""
        try:
            parts = token.split('_', 2)
            prefix = f"{parts[0]}_{parts[1]}"
            key_body = parts[2]
        except (ValueError, IndexError):
            raise InvalidCredentialsError("Invalid Admin key format")

        async with self.uow_factory() as uow:
            stored_key_record = await uow.api_keys.get_by_prefix(prefix)
            if not stored_key_record or stored_key_record.status != "active":
                raise InvalidCredentialsError("Invalid or revoked Admin key")

            incoming_key_hash = hashlib.sha256(key_body.encode()).hexdigest()
            if not secrets.compare_digest(incoming_key_hash, stored_key_record.hashed_key):
                raise InvalidCredentialsError("Invalid Admin key")

        # Admin Key được gán thẳng quyền admin hệ thống
        roles = ["admin"]
        permissions = await uow.permissions.get_permissions_for_role_names(roles) # Lấy helper từ uow

        return Identity(
            auth_type="admin_key",
            api_key_id=stored_key_record.id,
            organization_id="admin_org", # ID đặc biệt cho admin
            application_id=stored_key_record.application_id,
            plan="enterprise",
            roles=roles,
            permissions=list(permissions),
            scopes={"*"} # Admin key có toàn bộ quyền
        )

    async def _verify_standard_api_key(self, token: str) -> Identity:
        """Xác thực User API Key thông thường (sk_[hex]_[body])"""
        try:
            parts = token.split('_', 2)
            prefix = f"{parts[0]}_{parts[1]}"
            key_body = parts[2]
        except (ValueError, IndexError):
            raise InvalidCredentialsError("Invalid API key format")

        async with self.uow_factory() as uow:
            stored_key_record = await uow.api_keys.get_by_prefix(prefix)
            if not stored_key_record or stored_key_record.status != "active":
                raise InvalidCredentialsError("Invalid or revoked API key")

            incoming_key_hash = hashlib.sha256(key_body.encode()).hexdigest()
            if not secrets.compare_digest(incoming_key_hash, stored_key_record.hashed_key):
                raise InvalidCredentialsError("Invalid API key")

            organization = stored_key_record.application.organization
            if not organization:
                 raise AuthenticationError(f"Data integrity error: API key {stored_key_record.id} has no associated organization.")

        # Key người dùng thông thường có vai trò 'member'
        roles = ["member"] # Trong tương lai, vai trò có thể được lưu cùng API key
        scopes = {"model:read", "chat:create"} # Gán scope mặc định cho API key người dùng
        permissions = await uow.permissions.get_permissions_for_role_names(roles) # Lấy helper từ uow

        return Identity(
            auth_type="api_key",
            api_key_id=stored_key_record.id,
            organization_id=organization.id,
            application_id=stored_key_record.application_id,
            plan=organization.plan,
            roles=roles,
            permissions=list(permissions),
            scopes=scopes
        )