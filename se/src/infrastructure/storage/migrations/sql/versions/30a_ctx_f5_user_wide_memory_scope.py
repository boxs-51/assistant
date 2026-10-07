"""Add nullable USER_WIDE Memory storage classification.

Revision ID: 30a_ctx_f5_user_wide_memory_scope
Revises: 29a_crt1_capability_invocation_target
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "30a_ctx_f5_user_wide_memory_scope"
down_revision: Union[str, None] = "29a_crt1_capability_invocation_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_memory_records_scope"
_EXPRESSION = "memory_scope IS NULL OR memory_scope = 'USER_WIDE'"


def upgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.add_column(
            sa.Column("memory_scope", sa.String(length=32), nullable=True)
        )
        batch_op.create_check_constraint(_CONSTRAINT, _EXPRESSION)


def downgrade() -> None:
    with op.batch_alter_table("memory_records") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.drop_column("memory_scope")
