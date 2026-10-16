import structlog
from typing import Optional, List
import hashlib
import secrets

from .....domain.schemas.auth import APIKeyCreateSchema, APIKeyResponseSchema, APIKeyInfoSchema
from .....domain.schemas.identity import Identity
from .....transport.gateway.authentication import api_key as APIKeyHelper
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
        """An admin key has no effective permission without a bound DB role ID."""
        try:
            parts = token.split("_", 2)
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
            organization = stored_key_record.application.organization
            if organization is None:
                raise AuthenticationError("Admin key has no owning organization")
            # The linked organization and its owner are persisted, trusted
            # context. A globally shared display name 'admin' is not a grant.
            permissions, resolved_org = (
                await uow.permissions.get_permissions_for_member_role_ids(
                    user_id=organization.owner_id,
                    organization_id=organization.id,
                    allowed_role_names={"admin"},
                )
            )
            if resolved_org != organization.id:
                permissions = set()
            organization_id = organization.id
            app_id = stored_key_record.application_id
            key_id = stored_key_record.id

        return Identity(
            auth_type="admin_key",
            api_key_id=key_id,
            organization_id=organization_id,
            application_id=app_id,
            plan="enterprise",
            roles=["admin"],
            permissions=sorted(permissions),
            scopes={"*"},
        )

    async def _verify_standard_api_key(self, token: str) -> Identity:
        """Standard app credentials never aggregate same-name tenant roles."""
        try:
            parts = token.split("_", 2)
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
                raise AuthenticationError("API key has no owning organization")

            # The API key lacks a persisted user-role assignment. Use only an
            # explicit 'member' binding of the organization's persisted owner;
            # do NOT inherit an admin grant or any globally named member role.
            permissions, resolved_org = (
                await uow.permissions.get_permissions_for_member_role_ids(
                    user_id=organization.owner_id,
                    organization_id=organization.id,
                    allowed_role_names={"member"},
                )
            )
            if resolved_org != organization.id:
                permissions = set()
            organization_id = organization.id
            application_id = stored_key_record.application_id
            key_id = stored_key_record.id
            plan = organization.plan

        return Identity(
            auth_type="api_key",
            api_key_id=key_id,
            organization_id=organization_id,
            application_id=application_id,
            plan=plan,
            roles=["member"],
            permissions=sorted(permissions),
            scopes={"model:read", "chat:create"},
        )
