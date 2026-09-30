"""UBQ-2 dual accounting and TaskBudget incarnation bridge.

Revision ID: 26a_ubq2_dual_accounting_bridge
Revises: 25a_ubq1_user_budget_foundation
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "26a_ubq2_dual_accounting_bridge"
down_revision: Union[str, None] = "25a_ubq1_user_budget_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXHAUSTED_SENTINEL = 9223372036854775807
MAX_ALLOCATABLE_GENERATION = EXHAUSTED_SENTINEL - 1

_SQLITE_TRIGGERS = (
    "trg_ubq2_budget_generation_insert",
    "trg_ubq2_budget_generation_consume",
    "trg_ubq2_budget_generation_immutable",
    "trg_ubq2_allocator_delete",
    "trg_ubq2_allocator_update",
    "trg_ubq2_reservation_incarnation_insert",
    "trg_ubq2_reservation_identity_immutable",
    "trg_ubq2_budget_delete_restrict",
    "trg_ubq2_binding_insert",
    "trg_ubq2_binding_immutable",
    "trg_ubq2_binding_delete",
    "trg_ubq2_agent_task_id_tombstone",
    "trg_ubq2_user_binding_delete_restrict",
    "trg_ubq2_bridge_insert",
    "trg_ubq2_bridge_immutable",
    "trg_ubq2_bridge_delete",
    "trg_ubq2_ubq_receipt_delete_restrict",
)


def _sqlite(sql: str) -> None:
    op.execute(sa.text(sql))


def _drop_sqlite_triggers() -> None:
    for name in _SQLITE_TRIGGERS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))


def _create_sqlite_triggers() -> None:
    _sqlite("""
    CREATE TRIGGER trg_ubq2_budget_generation_insert
    BEFORE INSERT ON agent_task_budgets
    WHEN NOT EXISTS (
        SELECT 1
        FROM agent_task_budget_incarnation_allocator a
        WHERE a.allocator_id = 1
          AND a.next_generation < 9223372036854775807
          AND NEW.incarnation_generation = a.next_generation
    )
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_INCARNATION_EXHAUSTED_OR_MISMATCH');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_budget_generation_consume
    AFTER INSERT ON agent_task_budgets
    BEGIN
        UPDATE agent_task_budget_incarnation_allocator
        SET next_generation = next_generation + 1
        WHERE allocator_id = 1;
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_budget_generation_immutable
    BEFORE UPDATE OF incarnation_generation ON agent_task_budgets
    WHEN NEW.incarnation_generation != OLD.incarnation_generation
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_INCARNATION_IMMUTABLE');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_allocator_delete
    BEFORE DELETE ON agent_task_budget_incarnation_allocator
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_INCARNATION_ALLOCATOR_IMMUTABLE');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_allocator_update
    BEFORE UPDATE ON agent_task_budget_incarnation_allocator
    WHEN NEW.allocator_id != OLD.allocator_id
      OR NEW.next_generation != OLD.next_generation + 1
      OR NEW.next_generation > 9223372036854775807
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_INCARNATION_ALLOCATOR_INVALID');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_reservation_incarnation_insert
    BEFORE INSERT ON agent_task_budget_reservations
    WHEN NOT EXISTS (
        SELECT 1
        FROM agent_task_budgets b
        WHERE b.task_id = NEW.task_id
          AND b.incarnation_generation = NEW.task_budget_incarnation_generation
    )
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_RESERVATION_INCARCATION_MISMATCH');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_reservation_identity_immutable
    BEFORE UPDATE OF
        task_id,
        kind,
        reservation_key,
        task_budget_incarnation_generation,
        payload_fingerprint
    ON agent_task_budget_reservations
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_RESERVATION_IDENTITY_IMMUTABLE');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_budget_delete_restrict
    BEFORE DELETE ON agent_task_budgets
    WHEN EXISTS (
        SELECT 1
        FROM agent_task_budget_reservations r
        WHERE r.task_id = OLD.task_id
          AND r.task_budget_incarnation_generation = OLD.incarnation_generation
    )
    BEGIN
        SELECT RAISE(ABORT, 'TASK_BUDGET_INCARNATION_STILL_REFERENCED');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_binding_insert
    BEFORE INSERT ON user_budget_task_bindings
    WHEN NOT EXISTS (
        SELECT 1 FROM agent_tasks t WHERE t.id = NEW.task_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM users u WHERE u.id = NEW.owner_user_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BINDING_SOURCE_INVALID');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_binding_immutable
    BEFORE UPDATE ON user_budget_task_bindings
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BINDING_IMMUTABLE');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_binding_delete
    BEFORE DELETE ON user_budget_task_bindings
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BINDING_HISTORY_RESTRICTED');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_agent_task_id_tombstone
    BEFORE INSERT ON agent_tasks
    WHEN EXISTS (
        SELECT 1 FROM user_budget_task_bindings b WHERE b.task_id = NEW.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'USER_BUDGET_TASK_ID_TOMBSTONED');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_user_binding_delete_restrict
    BEFORE DELETE ON users
    WHEN EXISTS (
        SELECT 1
        FROM user_budget_task_bindings b
        WHERE b.owner_user_id = OLD.id
    )
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_USER_BINDING_HISTORY_RESTRICTED');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_bridge_insert
    BEFORE INSERT ON user_budget_dual_accounting_receipts
    WHEN NOT EXISTS (
        SELECT 1
        FROM user_budget_task_bindings b
        WHERE b.task_id = NEW.task_id
          AND b.owner_user_id = NEW.owner_user_id
    )
    OR NOT EXISTS (
        SELECT 1
        FROM agent_task_budget_reservations r
        WHERE r.task_id = NEW.task_id
          AND r.kind = NEW.task_budget_kind
          AND r.reservation_key = NEW.task_budget_reservation_key
          AND r.payload_fingerprint = NEW.source_payload_fingerprint
    )
    OR (
        NEW.mirror_dimension != 'NO_CHARGE'
        AND NOT EXISTS (
            SELECT 1
            FROM user_budget_reservations u
            WHERE u.owner_user_id = NEW.owner_user_id
              AND u.reservation_id = NEW.ubq_reservation_id
              AND u.window_epoch = NEW.window_epoch
              AND u.resource_kind = NEW.mirror_dimension
              AND u.idempotency_key = NEW.ubq_idempotency_key
              AND u.payload_fingerprint = NEW.ubq_payload_fingerprint
              AND u.state = 'SETTLED'
              AND u.settled_amount_atomic = NEW.amount_atomic
              AND (
                  (
                    NEW.mirror_dimension = 'TOOL_CALL'
                    AND u.capability_id = NEW.capability_id
                  )
                  OR
                  (
                    NEW.mirror_dimension != 'TOOL_CALL'
                    AND u.capability_id IS NULL
                    AND NEW.capability_id IS NULL
                  )
              )
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BRIDGE_REFERENCE_INVALID');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_bridge_immutable
    BEFORE UPDATE ON user_budget_dual_accounting_receipts
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BRIDGE_IMMUTABLE');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_bridge_delete
    BEFORE DELETE ON user_budget_dual_accounting_receipts
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_BRIDGE_HISTORY_RESTRICTED');
    END
    """)

    _sqlite("""
    CREATE TRIGGER trg_ubq2_ubq_receipt_delete_restrict
    BEFORE DELETE ON user_budget_reservations
    WHEN EXISTS (
        SELECT 1
        FROM user_budget_dual_accounting_receipts b
        WHERE b.ubq_reservation_id = OLD.reservation_id
          AND b.owner_user_id = OLD.owner_user_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'UBQ2_UBQ_RECEIPT_REFERENCED');
    END
    """)


def _create_postgresql_triggers() -> None:
    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_consume_task_budget_generation()
    RETURNS trigger AS $$
    DECLARE current_generation BIGINT;
    BEGIN
      SELECT next_generation
      INTO current_generation
      FROM agent_task_budget_incarnation_allocator
      WHERE allocator_id = 1
      FOR UPDATE;

      IF current_generation IS NULL
         OR current_generation >= 9223372036854775807
         OR NEW.incarnation_generation <> current_generation THEN
        RAISE EXCEPTION 'TASK_BUDGET_INCARNATION_EXHAUSTED_OR_MISMATCH';
      END IF;

      UPDATE agent_task_budget_incarnation_allocator
      SET next_generation = current_generation + 1
      WHERE allocator_id = 1;

      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_budget_generation_consume_pg
    BEFORE INSERT ON agent_task_budgets
    FOR EACH ROW
    EXECUTE FUNCTION ubq2_consume_task_budget_generation()
    """))

    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_incarnation_immutability_guard()
    RETURNS trigger AS $$
    BEGIN
      IF TG_TABLE_NAME = 'agent_task_budgets'
         AND NEW.incarnation_generation <> OLD.incarnation_generation THEN
        RAISE EXCEPTION 'TASK_BUDGET_INCARNATION_IMMUTABLE';
      END IF;
      IF TG_TABLE_NAME = 'agent_task_budget_reservations'
         AND (
           NEW.task_id <> OLD.task_id
           OR NEW.kind <> OLD.kind
           OR NEW.reservation_key <> OLD.reservation_key
           OR NEW.task_budget_incarnation_generation <> OLD.task_budget_incarnation_generation
           OR NEW.payload_fingerprint <> OLD.payload_fingerprint
         ) THEN
        RAISE EXCEPTION 'TASK_BUDGET_RESERVATION_IDENTITY_IMMUTABLE';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_budget_generation_immutable_pg
    BEFORE UPDATE ON agent_task_budgets
    FOR EACH ROW
    EXECUTE FUNCTION ubq2_incarnation_immutability_guard()
    """))

    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_reservation_identity_immutable_pg
    BEFORE UPDATE ON agent_task_budget_reservations
    FOR EACH ROW
    EXECUTE FUNCTION ubq2_incarnation_immutability_guard()
    """))

    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_allocator_guard()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE'
         OR NEW.allocator_id <> OLD.allocator_id
         OR NEW.next_generation <> OLD.next_generation + 1
         OR NEW.next_generation > 9223372036854775807 THEN
        RAISE EXCEPTION 'TASK_BUDGET_INCARNATION_ALLOCATOR_INVALID';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_allocator_update_pg
    BEFORE UPDATE ON agent_task_budget_incarnation_allocator
    FOR EACH ROW
    EXECUTE FUNCTION ubq2_allocator_guard()
    """))
    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_allocator_delete_pg
    BEFORE DELETE ON agent_task_budget_incarnation_allocator
    FOR EACH ROW
    EXECUTE FUNCTION ubq2_allocator_guard()
    """))

    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_binding_guard()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM agent_tasks t WHERE t.id = NEW.task_id) THEN
          RAISE EXCEPTION 'UBQ2_BINDING_SOURCE_INVALID';
        END IF;
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'UBQ2_BINDING_HISTORY_RESTRICTED';
    END;
    $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_binding_guard_pg
    BEFORE INSERT OR UPDATE OR DELETE ON user_budget_task_bindings
    FOR EACH ROW EXECUTE FUNCTION ubq2_binding_guard()
    """))

    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_task_id_tombstone_guard()
    RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM user_budget_task_bindings b WHERE b.task_id = NEW.id
      ) THEN
        RAISE EXCEPTION 'USER_BUDGET_TASK_ID_TOMBSTONED';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_task_id_tombstone_pg
    BEFORE INSERT ON agent_tasks
    FOR EACH ROW EXECUTE FUNCTION ubq2_task_id_tombstone_guard()
    """))

    op.execute(sa.text("""
    CREATE OR REPLACE FUNCTION ubq2_bridge_guard()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'UBQ2_BRIDGE_HISTORY_RESTRICTED';
      END IF;

      IF NOT EXISTS (
        SELECT 1
        FROM agent_task_budget_reservations r
        WHERE r.task_id = NEW.task_id
          AND r.kind = NEW.task_budget_kind
          AND r.reservation_key = NEW.task_budget_reservation_key
          AND r.payload_fingerprint = NEW.source_payload_fingerprint
      ) THEN
        RAISE EXCEPTION 'UBQ2_BRIDGE_SOURCE_INVALID';
      END IF;

      IF NEW.mirror_dimension <> 'NO_CHARGE'
         AND NOT EXISTS (
            SELECT 1
            FROM user_budget_reservations u
            WHERE u.owner_user_id = NEW.owner_user_id
              AND u.reservation_id = NEW.ubq_reservation_id
              AND u.window_epoch = NEW.window_epoch
              AND u.resource_kind = NEW.mirror_dimension
              AND u.idempotency_key = NEW.ubq_idempotency_key
              AND u.payload_fingerprint = NEW.ubq_payload_fingerprint
              AND u.state = 'SETTLED'
              AND u.settled_amount_atomic = NEW.amount_atomic
              AND (
                (
                  NEW.mirror_dimension = 'TOOL_CALL'
                  AND u.capability_id = NEW.capability_id
                )
                OR
                (
                  NEW.mirror_dimension <> 'TOOL_CALL'
                  AND u.capability_id IS NULL
                  AND NEW.capability_id IS NULL
                )
              )
         ) THEN
        RAISE EXCEPTION 'UBQ2_BRIDGE_REFERENCE_INVALID';
      END IF;

      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("""
    CREATE TRIGGER trg_ubq2_bridge_guard_pg
    BEFORE INSERT OR UPDATE OR DELETE ON user_budget_dual_accounting_receipts
    FOR EACH ROW EXECUTE FUNCTION ubq2_bridge_guard()
    """))


def _drop_postgresql_triggers() -> None:
    statements = (
        "DROP TRIGGER IF EXISTS trg_ubq2_bridge_guard_pg ON user_budget_dual_accounting_receipts",
        "DROP FUNCTION IF EXISTS ubq2_bridge_guard()",
        "DROP TRIGGER IF EXISTS trg_ubq2_task_id_tombstone_pg ON agent_tasks",
        "DROP FUNCTION IF EXISTS ubq2_task_id_tombstone_guard()",
        "DROP TRIGGER IF EXISTS trg_ubq2_binding_guard_pg ON user_budget_task_bindings",
        "DROP FUNCTION IF EXISTS ubq2_binding_guard()",
        "DROP TRIGGER IF EXISTS trg_ubq2_allocator_delete_pg ON agent_task_budget_incarnation_allocator",
        "DROP TRIGGER IF EXISTS trg_ubq2_allocator_update_pg ON agent_task_budget_incarnation_allocator",
        "DROP FUNCTION IF EXISTS ubq2_allocator_guard()",
        "DROP TRIGGER IF EXISTS trg_ubq2_reservation_identity_immutable_pg ON agent_task_budget_reservations",
        "DROP TRIGGER IF EXISTS trg_ubq2_budget_generation_immutable_pg ON agent_task_budgets",
        "DROP FUNCTION IF EXISTS ubq2_incarnation_immutability_guard()",
        "DROP TRIGGER IF EXISTS trg_ubq2_budget_generation_consume_pg ON agent_task_budgets",
        "DROP FUNCTION IF EXISTS ubq2_consume_task_budget_generation()",
    )
    for statement in statements:
        op.execute(sa.text(statement))


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "agent_task_budget_incarnation_allocator",
        sa.Column("allocator_id", sa.SmallInteger(), nullable=False),
        sa.Column("next_generation", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("allocator_id"),
        sa.CheckConstraint(
            "allocator_id = 1",
            name="ck_task_budget_incarnation_allocator_singleton",
        ),
        sa.CheckConstraint(
            "next_generation >= 1 AND next_generation <= 9223372036854775807",
            name="ck_task_budget_incarnation_allocator_bounds",
        ),
    )

    op.add_column(
        "agent_task_budgets",
        sa.Column("incarnation_generation", sa.BigInteger(), nullable=True),
    )

    task_ids = [
        str(row[0])
        for row in bind.execute(
            sa.text("SELECT task_id FROM agent_task_budgets ORDER BY task_id")
        ).all()
    ]
    if len(task_ids) > MAX_ALLOCATABLE_GENERATION:
        raise RuntimeError(
            "TaskBudget incarnation namespace exhausted during UBQ-2 migration."
        )

    for generation, task_id in enumerate(task_ids, start=1):
        bind.execute(
            sa.text(
                "UPDATE agent_task_budgets "
                "SET incarnation_generation = :generation "
                "WHERE task_id = :task_id"
            ),
            {"generation": generation, "task_id": task_id},
        )

    next_generation = (
        EXHAUSTED_SENTINEL
        if len(task_ids) == MAX_ALLOCATABLE_GENERATION
        else len(task_ids) + 1
    )
    bind.execute(
        sa.text(
            "INSERT INTO agent_task_budget_incarnation_allocator "
            "(allocator_id, next_generation) VALUES (1, :next_generation)"
        ),
        {"next_generation": next_generation},
    )

    op.add_column(
        "agent_task_budget_reservations",
        sa.Column(
            "task_budget_incarnation_generation",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    bind.execute(sa.text("""
        UPDATE agent_task_budget_reservations
        SET task_budget_incarnation_generation = (
            SELECT b.incarnation_generation
            FROM agent_task_budgets b
            WHERE b.task_id = agent_task_budget_reservations.task_id
        )
    """))
    orphan_count = bind.execute(sa.text("""
        SELECT COUNT(*)
        FROM agent_task_budget_reservations
        WHERE task_budget_incarnation_generation IS NULL
    """)).scalar_one()
    if orphan_count:
        raise RuntimeError(
            "Cannot migrate orphan TaskBudget reservations to UBQ-2."
        )

    with op.batch_alter_table("agent_task_budgets") as batch:
        batch.alter_column(
            "incarnation_generation",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_task_budget_task_incarnation",
            ["task_id", "incarnation_generation"],
        )
        batch.create_check_constraint(
            "ck_task_budget_incarnation_generation",
            "incarnation_generation > 0 "
            "AND incarnation_generation < 9223372036854775807",
        )

    with op.batch_alter_table("agent_task_budget_reservations") as batch:
        batch.alter_column(
            "task_budget_incarnation_generation",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_task_budget_reservation_incarnation_generation",
            "task_budget_incarnation_generation > 0 "
            "AND task_budget_incarnation_generation < 9223372036854775807",
        )
        batch.create_foreign_key(
            "fk_task_budget_reservation_exact_incarnation",
            "agent_task_budgets",
            ["task_id", "task_budget_incarnation_generation"],
            ["task_id", "incarnation_generation"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("user_budget_reservations") as batch:
        batch.create_unique_constraint(
            "uq_user_budget_reservation_exact_bridge_ref",
            [
                "owner_user_id",
                "reservation_id",
                "window_epoch",
                "resource_kind",
                "idempotency_key",
                "payload_fingerprint",
            ],
        )

    op.create_table(
        "user_budget_task_bindings",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("enrollment_version", sa.String(length=32), nullable=False),
        sa.Column("source_auth_type", sa.String(length=32), nullable=False),
        sa.Column("source_api_key_id", sa.String(length=255), nullable=True),
        sa.Column("source_application_id", sa.String(length=255), nullable=True),
        sa.Column("source_organization_id", sa.String(length=255), nullable=True),
        sa.Column("resolution_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint(
            "task_id",
            "owner_user_id",
            name="uq_user_budget_task_binding_task_owner",
        ),
    )

    op.create_table(
        "user_budget_dual_accounting_receipts",
        sa.Column("bridge_receipt_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("task_budget_kind", sa.String(length=32), nullable=False),
        sa.Column("task_budget_reservation_key", sa.String(length=255), nullable=False),
        sa.Column("mirror_dimension", sa.String(length=32), nullable=False),
        sa.Column("source_payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_epoch", sa.BigInteger(), nullable=True),
        sa.Column("ubq_reservation_id", sa.String(length=255), nullable=True),
        sa.Column("ubq_idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("ubq_payload_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "amount_atomic", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("capability_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_user_id"],
            [
                "user_budget_task_bindings.task_id",
                "user_budget_task_bindings.owner_user_id",
            ],
            ondelete="RESTRICT",
            name="fk_ubq2_bridge_task_binding",
        ),
        sa.ForeignKeyConstraint(
            [
                "owner_user_id",
                "ubq_reservation_id",
                "window_epoch",
                "mirror_dimension",
                "ubq_idempotency_key",
                "ubq_payload_fingerprint",
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
        sa.PrimaryKeyConstraint("bridge_receipt_id"),
        sa.UniqueConstraint(
            "task_id",
            "task_budget_kind",
            "task_budget_reservation_key",
            "mirror_dimension",
            name="uq_ubq2_bridge_source_dimension",
        ),
        sa.UniqueConstraint(
            "ubq_reservation_id",
            name="uq_ubq2_bridge_ubq_reservation",
        ),
        sa.CheckConstraint(
            "task_budget_kind IN ('INFERENCE', 'USAGE', 'TOOL_CALL')",
            name="ck_ubq2_bridge_task_budget_kind",
        ),
        sa.CheckConstraint(
            "mirror_dimension IN "
            "('INFERENCE_CALL', 'TOTAL_TOKEN', 'COST_USD', 'TOOL_CALL', 'NO_CHARGE')",
            name="ck_ubq2_bridge_mirror_dimension",
        ),
        sa.CheckConstraint(
            "amount_atomic >= 0 AND amount_atomic <= 9223372036854775807",
            name="ck_ubq2_bridge_amount",
        ),
        sa.CheckConstraint(
            "(mirror_dimension = 'NO_CHARGE' AND amount_atomic = 0 "
            "AND window_epoch IS NULL AND ubq_reservation_id IS NULL "
            "AND ubq_idempotency_key IS NULL "
            "AND ubq_payload_fingerprint IS NULL AND capability_id IS NULL) "
            "OR (mirror_dimension != 'NO_CHARGE' AND amount_atomic > 0 "
            "AND window_epoch IS NOT NULL AND ubq_reservation_id IS NOT NULL "
            "AND ubq_idempotency_key IS NOT NULL "
            "AND ubq_payload_fingerprint IS NOT NULL)",
            name="ck_ubq2_bridge_charge_shape",
        ),
        sa.CheckConstraint(
            "(task_budget_kind = 'INFERENCE' "
            "AND mirror_dimension = 'INFERENCE_CALL' "
            "AND amount_atomic = 1 AND capability_id IS NULL) "
            "OR (task_budget_kind = 'USAGE' "
            "AND mirror_dimension IN ('TOTAL_TOKEN', 'COST_USD', 'NO_CHARGE') "
            "AND capability_id IS NULL) "
            "OR (task_budget_kind = 'TOOL_CALL' "
            "AND mirror_dimension = 'TOOL_CALL' "
            "AND amount_atomic = 1 AND capability_id IS NOT NULL)",
            name="ck_ubq2_bridge_source_dimension_shape",
        ),
    )

    if bind.dialect.name == "sqlite":
        _create_sqlite_triggers()
    elif bind.dialect.name == "postgresql":
        _create_postgresql_triggers()


def downgrade() -> None:
    bind = op.get_bind()

    bridge_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM user_budget_dual_accounting_receipts")
    ).scalar_one()
    binding_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM user_budget_task_bindings")
    ).scalar_one()
    if bridge_count or binding_count:
        raise RuntimeError(
            "Cannot downgrade UBQ-2 while durable binding/bridge history exists."
        )

    live_budget_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM agent_task_budgets")
    ).scalar_one()
    next_generation = bind.execute(
        sa.text(
            "SELECT next_generation "
            "FROM agent_task_budget_incarnation_allocator "
            "WHERE allocator_id = 1"
        )
    ).scalar_one()
    expected = (
        EXHAUSTED_SENTINEL
        if live_budget_count == MAX_ALLOCATABLE_GENERATION
        else live_budget_count + 1
    )
    if next_generation != expected:
        raise RuntimeError(
            "Cannot downgrade UBQ-2 after committed TaskBudget incarnation "
            "history has advanced beyond the live-row namespace."
        )

    if bind.dialect.name == "sqlite":
        _drop_sqlite_triggers()
    elif bind.dialect.name == "postgresql":
        _drop_postgresql_triggers()

    op.drop_table("user_budget_dual_accounting_receipts")
    op.drop_table("user_budget_task_bindings")

    with op.batch_alter_table("user_budget_reservations") as batch:
        batch.drop_constraint(
            "uq_user_budget_reservation_exact_bridge_ref",
            type_="unique",
        )

    with op.batch_alter_table("agent_task_budget_reservations") as batch:
        batch.drop_constraint(
            "fk_task_budget_reservation_exact_incarnation",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "ck_task_budget_reservation_incarnation_generation",
            type_="check",
        )
        batch.drop_column("task_budget_incarnation_generation")

    with op.batch_alter_table("agent_task_budgets") as batch:
        batch.drop_constraint(
            "uq_task_budget_task_incarnation",
            type_="unique",
        )
        batch.drop_constraint(
            "ck_task_budget_incarnation_generation",
            type_="check",
        )
        batch.drop_column("incarnation_generation")

    op.drop_table("agent_task_budget_incarnation_allocator")
