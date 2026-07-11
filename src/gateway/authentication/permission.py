from typing import Dict, List, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from ..storage.models.sql.user_data.permission import Role

class PermissionHelper:
    """
    Lớp helper quản lý việc ánh xạ từ Role sang Permission bằng cách truy vấn DB.
    Nó nhận một session từ Unit of Work để thực hiện truy vấn.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_permissions_for_role_names(self, role_names: List[str]) -> Set[str]:
        """Tổng hợp tất cả các quyền từ một danh sách các vai trò."""
        if not role_names:
            return set()
        stmt = select(Role).where(Role.name.in_(role_names)).options(selectinload(Role.permissions))
        result = await self.session.execute(stmt)
        roles = result.scalars().unique().all()
        permissions = set()
        for role in roles:
            for perm in role.permissions:
                permissions.add(perm.name)
        return permissions