"""Freeze AE-R11 checkpoint transcript rollout legality.

Revision ID: 17a_r11_checkpoint_cutover
Revises: 16a_r11_transcript_representation
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "17a_r11_checkpoint_cutover"
down_revision: Union[str, None] = "16a_r11_transcript_representation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_agent_execution_checkpoints_transcript_ref_pair"
_EXPRESSION = (
    "(transcript_ref IS NULL AND transcript_version IS NULL) OR "
    "(transcript_ref IS NOT NULL AND transcript_version IS NOT NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("agent_execution_checkpoints") as batch_op:
        batch_op.create_check_constraint(_CONSTRAINT, _EXPRESSION)


def downgrade() -> None:
    with op.batch_alter_table("agent_execution_checkpoints") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
