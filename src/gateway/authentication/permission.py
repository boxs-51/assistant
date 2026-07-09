from typing import Dict, List, Set

# Đây là nơi định nghĩa tập trung các quyền hạn trong hệ thống.
# Ví dụ: "resource:action" -> "model:read", "file:delete", "admin:read"

# Ánh xạ từ Vai trò (Role) sang một tập hợp các Quyền (Permissions)
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "admin": {
        "admin:read",
        "admin:write",
        "file:read",
        "file:write",
        "file:delete",
    },
    "member": {
        "file:read",
        "file:write",
    },
}

def get_permissions_for_roles(roles: List[str]) -> Set[str]:
    """Tổng hợp tất cả các quyền từ một danh sách các vai trò."""
    permissions = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))
    return permissions