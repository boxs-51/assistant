from typing import Dict, List, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from ....infrastructure.storage.models.sql.user_data.permission import Role
from ....infrastructure.storage.models.sql.user_data.member import (
    Member, member_role_association,
)

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
    async def get_permissions_for_member_role_ids(
        self,
        *,
        user_id: str | None,
        organization_id: str | None,
        allowed_role_names: Set[str] | None = None,
    ) -> tuple[Set[str], str | None]:
        """Resolve permissions only through durable membership -> exact role ID.

        No caller-supplied JWT role *name* can serve as authorization evidence.
        The organization claim is a constraint on the DB membership, not a grant.
        For a token with no org claim, exactly one membership organization must
        be present; otherwise fail closed rather than unioning tenant privileges.
        A shared system-wide role ID is usable only when actually bound to the
        principal's membership in the requested organization.
        """
        if not user_id or (organization_id is not None and not organization_id):
            return set(), None

        stmt = (
            select(Member.organization_id, Role)
            .join(
                member_role_association,
                and_(
                    member_role_association.c.member_organization_id
                    == Member.organization_id,
                    member_role_association.c.member_user_id == Member.user_id,
                ),
            )
            .join(Role, Role.id == member_role_association.c.role_id)
            .where(Member.user_id == user_id)
            .options(selectinload(Role.permissions))
        )
        if organization_id is not None:
            stmt = stmt.where(Member.organization_id == organization_id)
        rows = (await self.session.execute(stmt)).all()
        organizations = {org_id for org_id, _role in rows}
        if len(organizations) != 1:
            return set(), None
        resolved_org = next(iter(organizations))
        permissions: Set[str] = set()
        for _org_id, role in rows:
            # Never accept a role from another tenant even if the join table
            # has been populated by an inconsistent/malicious external writer.
            if role.organization_id not in (None, resolved_org):
                continue
            if allowed_role_names is not None and role.name not in allowed_role_names:
                continue
            permissions.update(permission.name for permission in role.permissions)
        return permissions, resolved_org
