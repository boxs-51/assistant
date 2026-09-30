from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserBudgetPolicyRecord(Base):
    __tablename__ = "user_budget_policies"

    policy_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    window_duration_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)

    max_compute_atomic: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_inference_calls: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_input_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_output_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_total_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_tool_calls_total: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    default_per_tool_limit: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    tool_limits_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    max_cost_usd_atomic: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "policy_id",
            name="uq_user_budget_policy_owner_id",
        ),
        UniqueConstraint(
            "owner_user_id",
            "policy_version",
            name="uq_user_budget_policy_owner_version",
        ),
        UniqueConstraint(
            "owner_user_id",
            "policy_id",
            "policy_version",
            "policy_fingerprint",
            name="uq_user_budget_policy_exact_identity",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_policy_revision_nonnegative",
        ),
        CheckConstraint(
            "window_duration_seconds > 0",
            name="ck_user_budget_policy_window_duration_positive",
        ),
        CheckConstraint(
            "(max_compute_atomic IS NULL OR max_compute_atomic > 0) "
            "AND (max_inference_calls IS NULL OR max_inference_calls > 0) "
            "AND (max_input_tokens IS NULL OR max_input_tokens > 0) "
            "AND (max_output_tokens IS NULL OR max_output_tokens > 0) "
            "AND (max_total_tokens IS NULL OR max_total_tokens > 0) "
            "AND (max_tool_calls_total IS NULL OR max_tool_calls_total > 0) "
            "AND (default_per_tool_limit IS NULL OR default_per_tool_limit > 0) "
            "AND (max_cost_usd_atomic IS NULL OR max_cost_usd_atomic > 0)",
            name="ck_user_budget_policy_optional_limits_positive",
        ),
        CheckConstraint(
            "window_duration_seconds <= 9223372036854775807 "
            "AND (max_compute_atomic IS NULL OR max_compute_atomic <= 9223372036854775807) "
            "AND (max_inference_calls IS NULL OR max_inference_calls <= 9223372036854775807) "
            "AND (max_input_tokens IS NULL OR max_input_tokens <= 9223372036854775807) "
            "AND (max_output_tokens IS NULL OR max_output_tokens <= 9223372036854775807) "
            "AND (max_total_tokens IS NULL OR max_total_tokens <= 9223372036854775807) "
            "AND (max_tool_calls_total IS NULL OR max_tool_calls_total <= 9223372036854775807) "
            "AND (default_per_tool_limit IS NULL OR default_per_tool_limit <= 9223372036854775807) "
            "AND (max_cost_usd_atomic IS NULL OR max_cost_usd_atomic <= 9223372036854775807)",
            name="ck_user_budget_policy_bigint_bounds",
        ),
    )


class UserBudgetWindowRecord(Base):
    __tablename__ = "user_budget_windows"

    owner_user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )

    governing_policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    governing_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    governing_policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    compute_used_atomic: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    compute_reserved_atomic: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    inference_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    inference_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    input_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    output_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    total_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    tokens_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    tool_calls_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    tool_calls_reserved: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cost_used_atomic: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cost_reserved_atomic: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "owner_user_id",
                "governing_policy_id",
                "governing_policy_version",
                "governing_policy_fingerprint",
            ],
            [
                "user_budget_policies.owner_user_id",
                "user_budget_policies.policy_id",
                "user_budget_policies.policy_version",
                "user_budget_policies.policy_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_user_budget_window_exact_policy",
        ),
        CheckConstraint("epoch > 0", name="ck_user_budget_window_epoch_positive"),
        CheckConstraint(
            "state IN ('ACTIVE', 'CLOSED')",
            name="ck_user_budget_window_state",
        ),
        CheckConstraint(
            "expires_at > started_at",
            name="ck_user_budget_window_time_order",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_window_revision_nonnegative",
        ),
        CheckConstraint(
            "compute_used_atomic >= 0 "
            "AND compute_reserved_atomic >= 0 "
            "AND inference_used >= 0 "
            "AND inference_reserved >= 0 "
            "AND input_tokens_used >= 0 "
            "AND output_tokens_used >= 0 "
            "AND total_tokens_used >= 0 "
            "AND tokens_reserved >= 0 "
            "AND tool_calls_used >= 0 "
            "AND tool_calls_reserved >= 0 "
            "AND cost_used_atomic >= 0 "
            "AND cost_reserved_atomic >= 0",
            name="ck_user_budget_window_counters_nonnegative",
        ),
        CheckConstraint(
            "epoch <= 9223372036854775807 "
            "AND compute_used_atomic <= 9223372036854775807 "
            "AND compute_reserved_atomic <= 9223372036854775807 "
            "AND inference_used <= 9223372036854775807 "
            "AND inference_reserved <= 9223372036854775807 "
            "AND input_tokens_used <= 9223372036854775807 "
            "AND output_tokens_used <= 9223372036854775807 "
            "AND total_tokens_used <= 9223372036854775807 "
            "AND tokens_reserved <= 9223372036854775807 "
            "AND tool_calls_used <= 9223372036854775807 "
            "AND tool_calls_reserved <= 9223372036854775807 "
            "AND cost_used_atomic <= 9223372036854775807 "
            "AND cost_reserved_atomic <= 9223372036854775807",
            name="ck_user_budget_window_bigint_bounds",
        ),
        CheckConstraint(
            "(state = 'ACTIVE' AND closed_at IS NULL) "
            "OR (state = 'CLOSED' AND closed_at IS NOT NULL)",
            name="ck_user_budget_window_closed_at_state",
        ),
        Index(
            "uq_user_budget_windows_one_active_owner",
            "owner_user_id",
            unique=True,
            sqlite_where=text("state = 'ACTIVE'"),
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )


class UserBudgetAccountRecord(Base):
    __tablename__ = "user_budget_accounts"

    owner_user_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_policy_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    active_window_epoch: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    next_window_epoch: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "next_policy_id"],
            ["user_budget_policies.owner_user_id", "user_budget_policies.policy_id"],
            ondelete="RESTRICT",
            name="fk_user_budget_account_next_policy",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "active_window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_budget_account_active_window",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_account_revision_nonnegative",
        ),
        CheckConstraint(
            "next_window_epoch >= 1",
            name="ck_user_budget_account_next_epoch_positive",
        ),
        CheckConstraint(
            "active_window_epoch IS NULL OR next_window_epoch > active_window_epoch",
            name="ck_user_budget_account_epoch_monotonic",
        ),
        CheckConstraint(
            "next_window_epoch <= 9223372036854775807 "
            "AND (active_window_epoch IS NULL OR active_window_epoch <= 9223372036854775807)",
            name="ck_user_budget_account_bigint_bounds",
        ),
    )


class UserToolBudgetUsageRecord(Base):
    __tablename__ = "user_tool_budget_usage"

    owner_user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    used_calls: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_calls: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_tool_budget_usage_window",
        ),
        CheckConstraint(
            "used_calls >= 0 AND reserved_calls >= 0",
            name="ck_user_tool_budget_usage_nonnegative",
        ),
        CheckConstraint(
            "window_epoch <= 9223372036854775807 "
            "AND used_calls <= 9223372036854775807 "
            "AND reserved_calls <= 9223372036854775807",
            name="ck_user_tool_budget_usage_bigint_bounds",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_user_tool_budget_usage_revision_nonnegative",
        ),
    )


class UserBudgetReservationRecord(Base):
    __tablename__ = "user_budget_reservations"

    reservation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    window_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reserved_amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_amount_atomic: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="RESERVED", server_default="RESERVED"
    )
    attribution_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_budget_reservation_window",
        ),
        UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_user_budget_reservation_owner_idempotency",
        ),
        UniqueConstraint(
            "owner_user_id",
            "reservation_id",
            "window_epoch",
            "resource_kind",
            "idempotency_key",
            "payload_fingerprint",
            name="uq_user_budget_reservation_exact_bridge_ref",
        ),
        CheckConstraint(
            "resource_kind IN ("
            "'INFERENCE_CALL', 'INPUT_TOKEN', 'OUTPUT_TOKEN', 'TOTAL_TOKEN', "
            "'TOOL_CALL', 'COMPUTE_UNIT', 'COST_USD'"
            ")",
            name="ck_user_budget_reservation_resource_kind",
        ),
        CheckConstraint(
            "state IN ('RESERVED', 'SETTLED', 'RELEASED', 'OUTCOME_UNKNOWN')",
            name="ck_user_budget_reservation_state",
        ),
        CheckConstraint(
            "(state = 'SETTLED' AND settled_amount_atomic IS NOT NULL AND settled_at IS NOT NULL) "
            "OR (state != 'SETTLED' AND settled_amount_atomic IS NULL AND settled_at IS NULL)",
            name="ck_user_budget_reservation_settlement_shape",
        ),
        CheckConstraint(
            "reserved_amount_atomic > 0 "
            "AND (settled_amount_atomic IS NULL OR settled_amount_atomic >= 0)",
            name="ck_user_budget_reservation_amounts",
        ),
        CheckConstraint(
            "window_epoch <= 9223372036854775807 "
            "AND reserved_amount_atomic <= 9223372036854775807 "
            "AND (settled_amount_atomic IS NULL OR settled_amount_atomic <= 9223372036854775807)",
            name="ck_user_budget_reservation_bigint_bounds",
        ),
        CheckConstraint(
            "(resource_kind = 'TOOL_CALL' AND capability_id IS NOT NULL) "
            "OR (resource_kind != 'TOOL_CALL' AND capability_id IS NULL)",
            name="ck_user_budget_reservation_capability_scope",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_reservation_revision_nonnegative",
        ),
    )


class UserBudgetTaskBindingRecord(Base):
    __tablename__ = "user_budget_task_bindings"

    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_api_key_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_application_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_organization_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolution_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "task_id", "owner_user_id", name="uq_user_budget_task_binding_task_owner"
        ),
    )


class UserBudgetDualAccountingReceiptRecord(Base):
    __tablename__ = "user_budget_dual_accounting_receipts"

    bridge_receipt_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task_budget_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    task_budget_reservation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mirror_dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    window_epoch: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ubq_reservation_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ubq_idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ubq_payload_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    amount_atomic: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    capability_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "owner_user_id"],
            ["user_budget_task_bindings.task_id", "user_budget_task_bindings.owner_user_id"],
            ondelete="RESTRICT",
            name="fk_ubq2_bridge_task_binding",
        ),
        ForeignKeyConstraint(
            [
                "owner_user_id", "ubq_reservation_id", "window_epoch",
                "mirror_dimension", "ubq_idempotency_key", "ubq_payload_fingerprint",
            ],
            [
                "user_budget_reservations.owner_user_id",
                "user_budget_reservations.reservation_id",
                "user_budget_reservations.window_epoch",
                "user_budget_reservations.resource_kind",
                "user_budget_reservations.idempotency_key",
                "user_budget_reservations.payload_fingerprint",
            ],
            ondelete="RESTRICT",
            name="fk_ubq2_bridge_exact_ubq_receipt",
        ),
        UniqueConstraint(
            "task_id", "task_budget_kind", "task_budget_reservation_key",
            "mirror_dimension", name="uq_ubq2_bridge_source_dimension",
        ),
        UniqueConstraint("ubq_reservation_id", name="uq_ubq2_bridge_ubq_reservation"),
        CheckConstraint(
            "task_budget_kind IN ('INFERENCE', 'USAGE', 'TOOL_CALL')",
            name="ck_ubq2_bridge_task_budget_kind",
        ),
        CheckConstraint(
            "mirror_dimension IN ('INFERENCE_CALL', 'TOTAL_TOKEN', 'COST_USD', 'TOOL_CALL', 'NO_CHARGE')",
            name="ck_ubq2_bridge_mirror_dimension",
        ),
        CheckConstraint(
            "amount_atomic >= 0 AND amount_atomic <= 9223372036854775807",
            name="ck_ubq2_bridge_amount",
        ),
        CheckConstraint(
            "(mirror_dimension = 'NO_CHARGE' AND amount_atomic = 0 "
            "AND window_epoch IS NULL AND ubq_reservation_id IS NULL "
            "AND ubq_idempotency_key IS NULL AND ubq_payload_fingerprint IS NULL "
            "AND capability_id IS NULL) OR "
            "(mirror_dimension != 'NO_CHARGE' AND amount_atomic > 0 "
            "AND window_epoch IS NOT NULL AND ubq_reservation_id IS NOT NULL "
            "AND ubq_idempotency_key IS NOT NULL AND ubq_payload_fingerprint IS NOT NULL)",
            name="ck_ubq2_bridge_charge_shape",
        ),
        CheckConstraint(
            "(task_budget_kind = 'INFERENCE' AND mirror_dimension = 'INFERENCE_CALL' "
            "AND amount_atomic = 1 AND capability_id IS NULL) OR "
            "(task_budget_kind = 'USAGE' AND mirror_dimension IN ('TOTAL_TOKEN', 'COST_USD', 'NO_CHARGE') "
            "AND capability_id IS NULL) OR "
            "(task_budget_kind = 'TOOL_CALL' AND mirror_dimension = 'TOOL_CALL' "
            "AND amount_atomic = 1 AND capability_id IS NOT NULL)",
            name="ck_ubq2_bridge_source_dimension_shape",
        ),
    )
