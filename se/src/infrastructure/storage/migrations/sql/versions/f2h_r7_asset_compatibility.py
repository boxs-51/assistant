"""F2-H R7 compatibility hardening for Central Asset Storage.

Revision ID: f2h_r7_asset_compat
Revises: f1a_central_asset_storage

Adds the durable AgentToolResult asset locator and honest declared/detected
MIME representation. This remains expand-only with no runtime message cutover.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2h_r7_asset_compat"
down_revision: Union[str, None] = "f1a_central_asset_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REFERENCE_CHECK = (
    "("
    "reference_type = 'MESSAGE_CONTENT' "
    "AND message_id IS NOT NULL "
    "AND session_id IS NULL "
    "AND project_id IS NULL "
    "AND agent_tool_result_id IS NULL "
    "AND content_part_index IS NOT NULL "
    "AND content_part_index >= 0"
    ") OR ("
    "reference_type = 'SESSION_RESOURCE' "
    "AND message_id IS NULL "
    "AND session_id IS NOT NULL "
    "AND project_id IS NULL "
    "AND agent_tool_result_id IS NULL "
    "AND content_part_index IS NULL"
    ") OR ("
    "reference_type = 'PROJECT_RESOURCE' "
    "AND message_id IS NULL "
    "AND session_id IS NULL "
    "AND project_id IS NOT NULL "
    "AND agent_tool_result_id IS NULL "
    "AND content_part_index IS NULL"
    ") OR ("
    "reference_type = 'AGENT_TOOL_RESULT' "
    "AND message_id IS NULL "
    "AND session_id IS NULL "
    "AND project_id IS NULL "
    "AND agent_tool_result_id IS NOT NULL "
    "AND content_part_index IS NULL"
    ")"
)

_LEGACY_REFERENCE_CHECK = (
    "("
    "reference_type = 'MESSAGE_CONTENT' "
    "AND message_id IS NOT NULL "
    "AND session_id IS NULL "
    "AND project_id IS NULL "
    "AND content_part_index IS NOT NULL "
    "AND content_part_index >= 0"
    ") OR ("
    "reference_type = 'SESSION_RESOURCE' "
    "AND message_id IS NULL "
    "AND session_id IS NOT NULL "
    "AND project_id IS NULL "
    "AND content_part_index IS NULL"
    ") OR ("
    "reference_type = 'PROJECT_RESOURCE' "
    "AND message_id IS NULL "
    "AND session_id IS NULL "
    "AND project_id IS NOT NULL "
    "AND content_part_index IS NULL"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("file_blobs") as batch:
        batch.add_column(
            sa.Column("declared_mime_type", sa.String(length=255), nullable=True)
        )

    with op.batch_alter_table("file_references") as batch:
        batch.drop_constraint(
            "ck_file_references_exact_locator",
            type_="check",
        )
        batch.add_column(
            sa.Column("agent_tool_result_id", sa.String(length=255), nullable=True)
        )
        batch.create_foreign_key(
            "fk_file_references_agent_tool_result",
            "agent_tool_results",
            ["agent_tool_result_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_file_references_exact_locator",
            _REFERENCE_CHECK,
        )
        batch.create_unique_constraint(
            "uq_file_references_tool_result_file",
            ["agent_tool_result_id", "file_id"],
        )
        batch.create_index(
            "ix_file_references_agent_tool_result_id",
            ["agent_tool_result_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("file_references") as batch:
        batch.drop_index("ix_file_references_agent_tool_result_id")
        batch.drop_constraint(
            "uq_file_references_tool_result_file",
            type_="unique",
        )
        batch.drop_constraint(
            "ck_file_references_exact_locator",
            type_="check",
        )
        batch.drop_constraint(
            "fk_file_references_agent_tool_result",
            type_="foreignkey",
        )
        batch.drop_column("agent_tool_result_id")
        batch.create_check_constraint(
            "ck_file_references_exact_locator",
            _LEGACY_REFERENCE_CHECK,
        )

    with op.batch_alter_table("file_blobs") as batch:
        batch.drop_column("declared_mime_type")
