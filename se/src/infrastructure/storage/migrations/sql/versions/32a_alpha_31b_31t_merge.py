"""Join existing independent AIC 31b and terminal 31t history without data effects.

Revision ID: 32a_alpha_31b_31t_merge
Revises: 31b_aic0_pg_guard_forward, 31t_direct_terminal_permission

This is ONLY an Alembic topology join. Both installed branches keep their
original revision IDs, schema/role effects, trusted guards, and downgrade
policies. No role grants, stamping, DDL, DML or account migrations occur here.
"""

from typing import Sequence, Union

revision: str = "32a_alpha_31b_31t_merge"
down_revision: Union[str, tuple[str, str], None] = (
    "31b_aic0_pg_guard_forward",
    "31t_direct_terminal_permission",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Preserve both already-applied parents without creating new SQL effects."""
    pass


def downgrade() -> None:
    """Only undo Alembic's merge marker, not either parent's security policy."""
    pass
