"""AE-R12-D2A stale-lease scanner access-path index.

Revision ID: 24a_r12_stale_lease_scan_index
Revises: 23a_ctx_f5_promotion_reservation

Adds only the canonical composite access path used by the bounded R12-D1
expired-owned RUNNING observation query. Scanner orchestration, scheduling,
takeover, recovery publication, and runtime activation remain out of scope.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "24a_r12_stale_lease_scan_index"
down_revision: Union[str, None] = "23a_ctx_f5_promotion_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_agent_executions_state_lease_expiry_id"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "agent_executions",
        ["state", "lease_expires_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        INDEX_NAME,
        table_name="agent_executions",
    )
