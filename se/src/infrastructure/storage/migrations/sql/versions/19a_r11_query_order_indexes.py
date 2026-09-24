"""AE-R11-E2 query-order indexes.

Revision ID: 19a_r11_query_order_indexes
Revises: 18a_cas_r0_assets

Adds only the two ordering indexes justified by the E2.1 runtime query-plan
evidence. Existing equality/resolution indexes remain intact because they serve
separate predicates and their write-cost/redundancy has not been independently
justified for removal.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "19a_r11_query_order_indexes"
down_revision: Union[str, None] = "18a_cas_r0_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_iterations_execution_iteration",
        "agent_iterations",
        ["execution_id", "iteration"],
        unique=False,
    )
    op.create_index(
        "ix_agent_task_branches_task_created_branch",
        "agent_task_branches",
        ["task_id", "created_at", "branch_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_task_branches_task_created_branch",
        table_name="agent_task_branches",
    )
    op.drop_index(
        "ix_agent_iterations_execution_iteration",
        table_name="agent_iterations",
    )
