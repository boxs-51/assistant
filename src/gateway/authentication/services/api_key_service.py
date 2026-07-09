import structlog
from typing import Optional, List

from ...schemas.auth import APIKeyCreateSchema, APIKeyResponseSchema, APIKeyInfoSchema
from ...schemas.identity import Identity
from ...storage.repositories.api_keys import APIKeyRepository
from ...storage.repositories.applications import ApplicationRepository
from ...storage.repositories.organizations import OrganizationRepository
from .. import api_key as APIKeyHelper

logger = structlog.get_logger(__name__)

class APIKeyService:
    def __init__(
        self,
        api_key_repo: APIKeyRepository,
        app_repo: ApplicationRepository,
        org_repo: OrganizationRepository
    ):
        self.api_key_repo = api_key_repo
        self.app_repo = app_repo
        self.org_repo = org_repo

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

        # 1. Tìm hoặc tạo Organization
        orgs = await self.org_repo.get_by_owner_id(user_id)
        if not orgs:
            # Logic này có thể được chuyển vào AuthenticationService.register_user
            logger.info("No organization found for user, creating a default one.", user_id=user_id)
            # Giả sử user có email, cần lấy user để có email
            # Tạm thời đặt tên mặc định
            organization = await self.org_repo.create(name=f"Default Organization", owner_id=user_id)
        else:
            organization = orgs[0]

        # 2. Tìm hoặc tạo Application
        # Tạm thời, mỗi key sẽ tạo một application mới với tên được cung cấp
        application = await self.app_repo.create(name=schema.name, organization_id=organization.id)

        # 3. Tạo API Key
        full_key, prefix, hashed_body = APIKeyHelper.generate_api_key()
        new_key_record = await self.api_key_repo.create(
            application_id=application.id,
            prefix=prefix,
            hashed_key=hashed_body
        )

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
        user_id = identity.user_id
        if not user_id:
            raise ValueError("Cannot list API keys without a user context.")

        # 1. Tìm organization của người dùng
        orgs = await self.org_repo.get_by_owner_id(user_id)
        if not orgs:
            return []
        
        # Giả sử người dùng chỉ thuộc 1 org trong ngữ cảnh này
        organization = orgs[0]

        # 2. Tìm tất cả application trong organization đó
        apps = await self.app_repo.get_by_organization_id(organization.id)
        app_ids = [app.id for app in apps]

        # 3. Lấy tất cả các key thuộc các application đó
        keys = await self.api_key_repo.get_by_application_ids(app_ids)

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
        user_id = identity.user_id
        key_to_revoke = await self.api_key_repo.get_by_id_and_owner(key_id, user_id)
        if not key_to_revoke:
            return False # Không tìm thấy key hoặc không có quyền
        
        return await self.api_key_repo.revoke(key_id)