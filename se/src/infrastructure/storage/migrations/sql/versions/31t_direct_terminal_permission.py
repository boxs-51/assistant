"""Create the terminal execution permission without silently widening role grants.

Revision ID: 31t_direct_terminal_permission
Revises: 30a_ctx_f5_user_wide_memory_scope

The permission is first-class database data. A separate explicitly confirmed,
role-ID-scoped provisioning command assigns it; admin:write is NOT an alias.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "31t_direct_terminal_permission"
down_revision: Union[str, None] = "30a_ctx_f5_user_wide_memory_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_ID = "direct-terminal-execute-v1"
_PERMISSION_NAME = "terminal:execute"
_DESCRIPTION = "Explicit authorization to run or launch host terminal commands"


def upgrade() -> None:
    # Portable SQLite/PostgreSQL INSERT that keeps an existing operator-created
    # permission (with any existing primary key) instead of replacing it.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO permissions (id, name, description) "
            "SELECT :permission_id, :permission_name, :description "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM permissions WHERE name = :permission_name"
            ")"
        ),
        {
            "permission_id": _PERMISSION_ID,
            "permission_name": _PERMISSION_NAME,
            "description": _DESCRIPTION,
        },
    )
    # INTENTIONALLY no role_permissions INSERT. That is a privileged change,
    # performed only by an operator naming and confirming one exact role ID.


def downgrade() -> None:
    # Never revoke an active role binding. Only remove our own unreferenced
    # provisioned row, without touching an older operator-created permission.
    op.get_bind().execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE id = :permission_id AND name = :permission_name "
            "AND NOT EXISTS ("
            "SELECT 1 FROM role_permissions WHERE permission_id = :permission_id"
            ")"
        ),
        {
            "permission_id": _PERMISSION_ID,
            "permission_name": _PERMISSION_NAME,
        },
    )
