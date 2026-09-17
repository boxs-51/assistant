"""add conversation turn, sequence, and lifecycle timestamps

Revision ID: 5b_conversation_temporal_contract
Revises: 5a_phase5_9_execution_resume, 896c456631dd
"""
from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5b_conversation_temporal_contract"
down_revision: Union[str, tuple[str, str], None] = (
    "5a_phase5_9_execution_resume",
    "896c456631dd",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("next_message_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("messages", sa.Column("turn_id", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    metadata = sa.MetaData()
    sessions = sa.Table("sessions", metadata, autoload_with=bind)
    messages = sa.Table("messages", metadata, autoload_with=bind)
    counters: dict[str, int] = defaultdict(int)
    rows = bind.execute(
        sa.select(messages.c.id, messages.c.session_id, messages.c.timestamp)
        .order_by(messages.c.session_id, messages.c.timestamp, messages.c.id)
    ).fetchall()
    for row in rows:
        counters[row.session_id] += 1
        bind.execute(
            messages.update().where(messages.c.id == row.id).values(
                turn_id=f"legacy_{row.id}",
                sequence=counters[row.session_id],
                created_at=row.timestamp,
                completed_at=row.timestamp,
            )
        )
    for session_id, value in counters.items():
        bind.execute(
            sessions.update().where(sessions.c.id == session_id).values(next_message_sequence=value)
        )

    with op.batch_alter_table("messages") as batch:
        batch.alter_column("turn_id", existing_type=sa.String(length=255), nullable=False)
        batch.alter_column("sequence", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_unique_constraint("uq_messages_session_sequence", ["session_id", "sequence"])
        batch.create_index("ix_messages_turn_id", ["turn_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_index("ix_messages_turn_id")
        batch.drop_constraint("uq_messages_session_sequence", type_="unique")
        batch.drop_column("completed_at")
        batch.drop_column("created_at")
        batch.drop_column("sequence")
        batch.drop_column("turn_id")
    op.drop_column("sessions", "next_message_sequence")
