"""UBQ-1 durable user budget representation foundation.

Revision ID: 25a_ubq1_user_budget_foundation
Revises: 24a_r12_stale_lease_scan_index

Creates only durable user-owned resource-budget representation and persistence
constraints. Runtime admission/charging, TaskBudget migration, timeout behavior,
and Agent recovery integration remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "25a_ubq1_user_budget_foundation"
down_revision: Union[str, None] = "24a_r12_stale_lease_scan_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQLITE_TRIGGER_NAMES = (
    "trg_ubq_policy_owner_insert",
    "trg_ubq_policy_immutable_update",
    "trg_ubq_policy_history_delete",
    "trg_ubq_account_owner_insert",
    "trg_ubq_account_owner_immutable",
    "trg_ubq_account_refs_update",
    "trg_ubq_window_refs_insert",
    "trg_ubq_window_identity_immutable",
    "trg_ubq_window_history_delete",
    "trg_ubq_tool_usage_window_insert",
    "trg_ubq_tool_usage_identity_immutable",
    "trg_ubq_reservation_window_insert",
    "trg_ubq_reservation_identity_immutable",
    "trg_ubq_user_history_delete",
)


def _execute_sqlite_trigger(sql: str) -> None:
    op.execute(sa.text(sql))


def _create_sqlite_triggers() -> None:
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_policy_owner_insert
        BEFORE INSERT ON user_budget_policies
        WHEN NOT EXISTS (
            SELECT 1 FROM users WHERE id = NEW.owner_user_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_POLICY_OWNER');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_policy_immutable_update
        BEFORE UPDATE ON user_budget_policies
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_POLICY_IMMUTABLE');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_policy_history_delete
        BEFORE DELETE ON user_budget_policies
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_POLICY_HISTORY_RESTRICTED');
        END
        """
    )

    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_account_owner_insert
        BEFORE INSERT ON user_budget_accounts
        WHEN NOT EXISTS (
            SELECT 1 FROM users WHERE id = NEW.owner_user_id
        )
        OR (
            NEW.next_policy_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM user_budget_policies p
                WHERE p.owner_user_id = NEW.owner_user_id
                  AND p.policy_id = NEW.next_policy_id
            )
        )
        OR (
            NEW.active_window_epoch IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM user_budget_windows w
                WHERE w.owner_user_id = NEW.owner_user_id
                  AND w.epoch = NEW.active_window_epoch
                  AND w.state = 'ACTIVE'
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_ACCOUNT_REFERENCE');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_account_owner_immutable
        BEFORE UPDATE OF owner_user_id ON user_budget_accounts
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_ACCOUNT_OWNER_IMMUTABLE');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_account_refs_update
        BEFORE UPDATE OF next_policy_id, active_window_epoch, next_window_epoch
        ON user_budget_accounts
        WHEN (
            NEW.next_policy_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM user_budget_policies p
                WHERE p.owner_user_id = NEW.owner_user_id
                  AND p.policy_id = NEW.next_policy_id
            )
        )
        OR (
            NEW.active_window_epoch IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM user_budget_windows w
                WHERE w.owner_user_id = NEW.owner_user_id
                  AND w.epoch = NEW.active_window_epoch
                  AND w.state = 'ACTIVE'
            )
        )
        OR NEW.next_window_epoch < OLD.next_window_epoch
        OR NEW.next_window_epoch < 1
        OR (
            NEW.active_window_epoch IS NOT NULL
            AND NEW.next_window_epoch <= NEW.active_window_epoch
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_ACCOUNT_REFERENCE');
        END
        """
    )

    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_window_refs_insert
        BEFORE INSERT ON user_budget_windows
        WHEN NOT EXISTS (
            SELECT 1 FROM users WHERE id = NEW.owner_user_id
        )
        OR NOT EXISTS (
            SELECT 1
            FROM user_budget_policies p
            WHERE p.owner_user_id = NEW.owner_user_id
              AND p.policy_id = NEW.governing_policy_id
              AND p.policy_version = NEW.governing_policy_version
              AND p.policy_fingerprint = NEW.governing_policy_fingerprint
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_WINDOW_REFERENCE');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_window_identity_immutable
        BEFORE UPDATE OF
            owner_user_id,
            epoch,
            governing_policy_id,
            governing_policy_version,
            governing_policy_fingerprint,
            started_at,
            expires_at
        ON user_budget_windows
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_WINDOW_PROVENANCE_IMMUTABLE');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_window_history_delete
        BEFORE DELETE ON user_budget_windows
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_WINDOW_HISTORY_RESTRICTED');
        END
        """
    )

    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_tool_usage_window_insert
        BEFORE INSERT ON user_tool_budget_usage
        WHEN NOT EXISTS (
            SELECT 1
            FROM user_budget_windows w
            WHERE w.owner_user_id = NEW.owner_user_id
              AND w.epoch = NEW.window_epoch
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_TOOL_USAGE_WINDOW');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_tool_usage_identity_immutable
        BEFORE UPDATE OF owner_user_id, window_epoch, capability_id
        ON user_tool_budget_usage
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_TOOL_USAGE_IDENTITY_IMMUTABLE');
        END
        """
    )

    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_reservation_window_insert
        BEFORE INSERT ON user_budget_reservations
        WHEN NOT EXISTS (
            SELECT 1
            FROM user_budget_windows w
            WHERE w.owner_user_id = NEW.owner_user_id
              AND w.epoch = NEW.window_epoch
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_RESERVATION_WINDOW');
        END
        """
    )
    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_reservation_identity_immutable
        BEFORE UPDATE OF
            reservation_id,
            owner_user_id,
            window_epoch,
            idempotency_key,
            resource_kind,
            capability_id,
            attribution_json,
            payload_fingerprint
        ON user_budget_reservations
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_RESERVATION_PROVENANCE_IMMUTABLE');
        END
        """
    )

    _execute_sqlite_trigger(
        """
        CREATE TRIGGER trg_ubq_user_history_delete
        BEFORE DELETE ON users
        WHEN EXISTS (
            SELECT 1 FROM user_budget_policies p
            WHERE p.owner_user_id = OLD.id
        )
        OR EXISTS (
            SELECT 1 FROM user_budget_accounts a
            WHERE a.owner_user_id = OLD.id
        )
        OR EXISTS (
            SELECT 1 FROM user_budget_windows w
            WHERE w.owner_user_id = OLD.id
        )
        OR EXISTS (
            SELECT 1 FROM user_tool_budget_usage u
            WHERE u.owner_user_id = OLD.id
        )
        OR EXISTS (
            SELECT 1 FROM user_budget_reservations r
            WHERE r.owner_user_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'UBQ_INTEGRITY_USER_HISTORY_RESTRICTED');
        END
        """
    )


def _drop_sqlite_triggers() -> None:
    for name in _SQLITE_TRIGGER_NAMES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))


def upgrade() -> None:
    op.create_table(
        "user_budget_policies",
        sa.Column("policy_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_duration_seconds", sa.BigInteger(), nullable=False),
        sa.Column("max_compute_atomic", sa.BigInteger(), nullable=True),
        sa.Column("max_inference_calls", sa.BigInteger(), nullable=True),
        sa.Column("max_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_tool_calls_total", sa.BigInteger(), nullable=True),
        sa.Column("default_per_tool_limit", sa.BigInteger(), nullable=True),
        sa.Column("tool_limits_json", sa.JSON(), nullable=False),
        sa.Column("max_cost_usd_atomic", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "policy_id",
            name="uq_user_budget_policy_owner_id",
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "policy_version",
            name="uq_user_budget_policy_owner_version",
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "policy_id",
            "policy_version",
            "policy_fingerprint",
            name="uq_user_budget_policy_exact_identity",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_policy_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "window_duration_seconds > 0",
            name="ck_user_budget_policy_window_duration_positive",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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

    op.create_table(
        "user_budget_windows",
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("governing_policy_id", sa.String(length=255), nullable=False),
        sa.Column("governing_policy_version", sa.String(length=64), nullable=False),
        sa.Column("governing_policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compute_used_atomic", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("compute_reserved_atomic", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inference_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inference_reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tool_calls_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tool_calls_reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_used_atomic", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_reserved_atomic", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("owner_user_id", "epoch"),
        sa.CheckConstraint(
            "epoch > 0",
            name="ck_user_budget_window_epoch_positive",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'CLOSED')",
            name="ck_user_budget_window_state",
        ),
        sa.CheckConstraint(
            "expires_at > started_at",
            name="ck_user_budget_window_time_order",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_window_revision_nonnegative",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "(state = 'ACTIVE' AND closed_at IS NULL) "
            "OR (state = 'CLOSED' AND closed_at IS NOT NULL)",
            name="ck_user_budget_window_closed_at_state",
        ),
    )
    op.create_index(
        "uq_user_budget_windows_one_active_owner",
        "user_budget_windows",
        ["owner_user_id"],
        unique=True,
        sqlite_where=sa.text("state = 'ACTIVE'"),
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )

    op.create_table(
        "user_budget_accounts",
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_policy_id", sa.String(length=255), nullable=True),
        sa.Column("active_window_epoch", sa.BigInteger(), nullable=True),
        sa.Column("next_window_epoch", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "next_policy_id"],
            ["user_budget_policies.owner_user_id", "user_budget_policies.policy_id"],
            ondelete="RESTRICT",
            name="fk_user_budget_account_next_policy",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "active_window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_budget_account_active_window",
        ),
        sa.PrimaryKeyConstraint("owner_user_id"),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_account_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "next_window_epoch >= 1",
            name="ck_user_budget_account_next_epoch_positive",
        ),
        sa.CheckConstraint(
            "active_window_epoch IS NULL OR next_window_epoch > active_window_epoch",
            name="ck_user_budget_account_epoch_monotonic",
        ),
        sa.CheckConstraint(
            "next_window_epoch <= 9223372036854775807 "
            "AND (active_window_epoch IS NULL OR active_window_epoch <= 9223372036854775807)",
            name="ck_user_budget_account_bigint_bounds",
        ),
    )

    op.create_table(
        "user_tool_budget_usage",
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("window_epoch", sa.BigInteger(), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=False),
        sa.Column("used_calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_tool_budget_usage_window",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", "window_epoch", "capability_id"),
        sa.CheckConstraint(
            "used_calls >= 0 AND reserved_calls >= 0",
            name="ck_user_tool_budget_usage_nonnegative",
        ),
        sa.CheckConstraint(
            "window_epoch <= 9223372036854775807 "
            "AND used_calls <= 9223372036854775807 "
            "AND reserved_calls <= 9223372036854775807",
            name="ck_user_tool_budget_usage_bigint_bounds",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_user_tool_budget_usage_revision_nonnegative",
        ),
    )

    op.create_table(
        "user_budget_reservations",
        sa.Column("reservation_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("window_epoch", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=True),
        sa.Column("reserved_amount_atomic", sa.BigInteger(), nullable=False),
        sa.Column("settled_amount_atomic", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="RESERVED"),
        sa.Column("attribution_json", sa.JSON(), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "window_epoch"],
            ["user_budget_windows.owner_user_id", "user_budget_windows.epoch"],
            ondelete="RESTRICT",
            name="fk_user_budget_reservation_window",
        ),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_user_budget_reservation_owner_idempotency",
        ),
        sa.CheckConstraint(
            "resource_kind IN ("
            "'INFERENCE_CALL', 'INPUT_TOKEN', 'OUTPUT_TOKEN', 'TOTAL_TOKEN', "
            "'TOOL_CALL', 'COMPUTE_UNIT', 'COST_USD'"
            ")",
            name="ck_user_budget_reservation_resource_kind",
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'SETTLED', 'RELEASED', 'OUTCOME_UNKNOWN')",
            name="ck_user_budget_reservation_state",
        ),
        sa.CheckConstraint(
            "reserved_amount_atomic > 0 "
            "AND (settled_amount_atomic IS NULL OR settled_amount_atomic >= 0)",
            name="ck_user_budget_reservation_amounts",
        ),
        sa.CheckConstraint(
            "window_epoch <= 9223372036854775807 "
            "AND reserved_amount_atomic <= 9223372036854775807 "
            "AND (settled_amount_atomic IS NULL OR settled_amount_atomic <= 9223372036854775807)",
            name="ck_user_budget_reservation_bigint_bounds",
        ),
        sa.CheckConstraint(
            "(resource_kind = 'TOOL_CALL' AND capability_id IS NOT NULL) "
            "OR (resource_kind != 'TOOL_CALL' AND capability_id IS NULL)",
            name="ck_user_budget_reservation_capability_scope",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_user_budget_reservation_revision_nonnegative",
        ),
    )

    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_triggers()


def downgrade() -> None:
    bind = op.get_bind()
    tables = (
        "user_budget_reservations",
        "user_tool_budget_usage",
        "user_budget_accounts",
        "user_budget_windows",
        "user_budget_policies",
    )
    for table in tables:
        count = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Cannot downgrade UBQ-1 while durable user-budget history exists."
            )

    if bind.dialect.name == "sqlite":
        _drop_sqlite_triggers()

    op.drop_table("user_budget_reservations")
    op.drop_table("user_tool_budget_usage")
    op.drop_table("user_budget_accounts")
    op.drop_index(
        "uq_user_budget_windows_one_active_owner",
        table_name="user_budget_windows",
    )
    op.drop_table("user_budget_windows")
    op.drop_table("user_budget_policies")
