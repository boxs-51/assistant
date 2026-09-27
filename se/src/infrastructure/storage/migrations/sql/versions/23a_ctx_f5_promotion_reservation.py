"""CTX-F5-3F-B durable promotion-reservation persistence foundation.

Revision ID: 23a_ctx_f5_promotion_reservation
Revises: 22a_r12_execution_lease_fence

Adds only trusted server-internal durable promotion-reservation persistence.
Trusted issuer/verifier wiring, atomic reservation-to-Memory admission runtime,
public/model-visible promotion APIs, source adapters, retrieval, and lifecycle
policy remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "23a_ctx_f5_promotion_reservation"
down_revision: Union[str, None] = "22a_r12_execution_lease_fence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_reservations",
        sa.Column(
            "promotion_authority_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("intent_digest", sa.String(length=64), nullable=False),
        sa.Column("intent_json", sa.JSON(), nullable=False),
        sa.Column("intent_canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column(
            "source_context_source_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("proof_receipt_id", sa.String(length=255), nullable=False),
        sa.Column(
            "authority_state_token",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("proof_scope", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "state IN ('ISSUED', 'CONSUMED', 'REVOKED')",
            name="ck_promotion_reservations_state",
        ),
        sa.CheckConstraint(
            "proof_scope = 'MEMORY_PROMOTION'",
            name="ck_promotion_reservations_proof_scope",
        ),
        sa.PrimaryKeyConstraint("promotion_authority_id"),
        sa.UniqueConstraint(
            "intent_digest",
            name="uq_promotion_reservations_intent_digest",
        ),
        sa.UniqueConstraint(
            "source_context_source_id",
            "proof_receipt_id",
            "authority_state_token",
            "proof_scope",
            name="uq_promotion_reservations_proof_authority",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    reservation_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM promotion_reservations")
    ).scalar_one()

    if reservation_count:
        raise RuntimeError(
            "Cannot downgrade CTX-F5-3F-B while durable promotion reservation "
            "authority rows exist."
        )

    op.drop_table("promotion_reservations")
