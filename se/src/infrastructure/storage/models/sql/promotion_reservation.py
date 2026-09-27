from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import (
    CheckConstraint,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PromotionReservationRow(Base):
    """Trusted server-internal durable CTX-F5 promotion reservation state."""

    __tablename__ = "promotion_reservations"
    __table_args__ = (
        UniqueConstraint(
            "intent_digest",
            name="uq_promotion_reservations_intent_digest",
        ),
        UniqueConstraint(
            "source_context_source_id",
            "proof_receipt_id",
            "authority_state_token",
            "proof_scope",
            name="uq_promotion_reservations_proof_authority",
        ),
        CheckConstraint(
            "state IN ('ISSUED', 'CONSUMED', 'REVOKED')",
            name="ck_promotion_reservations_state",
        ),
        CheckConstraint(
            "proof_scope = 'MEMORY_PROMOTION'",
            name="ck_promotion_reservations_proof_scope",
        ),
    )

    promotion_authority_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    intent_canonical_bytes: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    source_context_source_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    proof_receipt_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    authority_state_token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    proof_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
