"""Add immutable R8-E FORK runtime seed evidence.

Revision ID: 14d_r8_fork_runtime_seed
Revises: 14c_r8_fork_admission
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "14d_r8_fork_runtime_seed"
down_revision: Union[str, None] = "14c_r8_fork_admission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_task_fork_admissions",
        sa.Column("runtime_seed_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agent_task_fork_admissions",
        sa.Column(
            "runtime_seed_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "agent_task_fork_admissions",
        "runtime_seed_fingerprint",
    )
    op.drop_column(
        "agent_task_fork_admissions",
        "runtime_seed_json",
    )
