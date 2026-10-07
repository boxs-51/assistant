"""Add nullable CRT-1 semantic target representation.

Revision ID: 29a_crt1_capability_invocation_target
Revises: 28a_tbo1_task_policy_representation
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "29a_crt1_capability_invocation_target"
down_revision: Union[str, None] = "28a_tbo1_task_policy_representation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "capability_invocations",
        sa.Column("target_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("capability_invocations", "target_json")
