"""Durable nonwired AgentInstance ID reservation and live-owner integrity.

Revision ID: 31a_aic0_agent_instance_identity
Revises: 30a_ctx_f5_user_wide_memory_scope
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "31a_aic0_agent_instance_identity"
down_revision: Union[str, None] = "30a_ctx_f5_user_wide_memory_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQLITE_GUARDS = (
    """CREATE TRIGGER trg_aic_id_insert BEFORE INSERT ON aic_instance_reservations
    WHEN NEW.state != 'ACTIVE' OR NEW.generation != 1
    BEGIN SELECT RAISE(ABORT, 'AIC_ID_INITIAL_STATE_INVALID'); END""",
    """CREATE TRIGGER trg_aic_id_delete BEFORE DELETE ON aic_instance_reservations
    BEGIN SELECT RAISE(ABORT, 'AIC_ID_REUSE_FORBIDDEN'); END""",
    """CREATE TRIGGER trg_aic_id_update BEFORE UPDATE ON aic_instance_reservations
    WHEN NOT (
      NEW.agent_instance_id = OLD.agent_instance_id
      AND (
        (OLD.state = 'ACTIVE' AND NEW.state = 'RETIRING'
         AND NEW.generation = OLD.generation
         AND EXISTS (SELECT 1 FROM aic_instance_live l
                     WHERE l.agent_instance_id = OLD.agent_instance_id))
        OR
        (OLD.state = 'RETIRING' AND NEW.state = 'RETIRED'
         AND NEW.generation > OLD.generation
         AND NOT EXISTS (SELECT 1 FROM aic_instance_live l
                         WHERE l.agent_instance_id = OLD.agent_instance_id))
      )
    )
    BEGIN SELECT RAISE(ABORT, 'AIC_ID_TRANSITION_FORBIDDEN'); END""",
    """CREATE TRIGGER trg_aic_live_insert BEFORE INSERT ON aic_instance_live
    WHEN NOT (
      EXISTS (SELECT 1 FROM users u WHERE u.id = NEW.owner_user_id AND u.status = 'active')
      AND EXISTS (SELECT 1 FROM aic_instance_reservations r
                  WHERE r.agent_instance_id = NEW.agent_instance_id
                    AND r.state = 'ACTIVE')
      AND NEW.lifecycle_state = 'ACTIVE' AND NEW.generation = 1
    )
    BEGIN SELECT RAISE(ABORT, 'AIC_LIVE_OWNER_OR_ID_INVALID'); END""",
    """CREATE TRIGGER trg_aic_live_update BEFORE UPDATE ON aic_instance_live
    WHEN NOT (
      NEW.agent_instance_id = OLD.agent_instance_id
      AND NEW.owner_user_id = OLD.owner_user_id
      AND NEW.definition_id = OLD.definition_id
      AND NEW.definition_version = OLD.definition_version
      AND OLD.lifecycle_state = 'ACTIVE'
      AND NEW.lifecycle_state = 'SUSPENDED'
      AND NEW.generation = OLD.generation + 1
      AND EXISTS (SELECT 1 FROM users u WHERE u.id = OLD.owner_user_id
                  AND u.status = 'active')
    )
    BEGIN SELECT RAISE(ABORT, 'AIC_LIVE_UPDATE_FORBIDDEN'); END""",
    """CREATE TRIGGER trg_aic_live_delete BEFORE DELETE ON aic_instance_live
    WHEN NOT EXISTS (SELECT 1 FROM aic_instance_reservations r
                     WHERE r.agent_instance_id = OLD.agent_instance_id
                       AND r.state = 'RETIRING')
    BEGIN SELECT RAISE(ABORT, 'AIC_LIVE_DELETE_FORBIDDEN'); END""",
    """CREATE TRIGGER trg_aic_user_delete BEFORE DELETE ON users
    WHEN EXISTS (SELECT 1 FROM aic_instance_live l WHERE l.owner_user_id = OLD.id)
    BEGIN SELECT RAISE(ABORT, 'AIC_LIVE_OWNER_DELETE_DENIED'); END""",
    """CREATE TRIGGER trg_aic_user_rekey BEFORE UPDATE OF id ON users
    WHEN NEW.id != OLD.id
      AND EXISTS (SELECT 1 FROM aic_instance_live l WHERE l.owner_user_id = OLD.id)
    BEGIN SELECT RAISE(ABORT, 'AIC_LIVE_OWNER_REKEY_DENIED'); END""",
)

_SQLITE_TRIGGER_NAMES = (
    "trg_aic_user_rekey",
    "trg_aic_user_delete",
    "trg_aic_live_delete",
    "trg_aic_live_update",
    "trg_aic_live_insert",
    "trg_aic_id_update",
    "trg_aic_id_delete",
    "trg_aic_id_insert",
)

_PG_FUNCTIONS = (
    """CREATE FUNCTION aic_id_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'AIC_ID_REUSE_FORBIDDEN';
      END IF;
      IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'ACTIVE' OR NEW.generation <> 1 THEN
          RAISE EXCEPTION 'AIC_ID_INITIAL_STATE_INVALID';
        END IF;
        RETURN NEW;
      END IF;
      IF NEW.agent_instance_id <> OLD.agent_instance_id
         OR NOT (
           (OLD.state = 'ACTIVE' AND NEW.state = 'RETIRING'
            AND NEW.generation = OLD.generation
            AND EXISTS (SELECT 1 FROM aic_instance_live l
                        WHERE l.agent_instance_id = OLD.agent_instance_id))
           OR
           (OLD.state = 'RETIRING' AND NEW.state = 'RETIRED'
            AND NEW.generation > OLD.generation
            AND NOT EXISTS (SELECT 1 FROM aic_instance_live l
                            WHERE l.agent_instance_id = OLD.agent_instance_id))
         ) THEN
        RAISE EXCEPTION 'AIC_ID_TRANSITION_FORBIDDEN';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql""",
    """CREATE FUNCTION aic_live_guard() RETURNS trigger AS $$
    DECLARE
      owner_active boolean;
      reservation_active boolean;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM aic_instance_reservations r
                       WHERE r.agent_instance_id = OLD.agent_instance_id
                         AND r.state = 'RETIRING') THEN
          RAISE EXCEPTION 'AIC_LIVE_DELETE_FORBIDDEN';
        END IF;
        RETURN OLD;
      END IF;
      IF TG_OP = 'INSERT' THEN
        -- A normal UPDATE of reservation.state uses FOR NO KEY UPDATE.
        -- FK key-share alone does not conflict with that lock. Serialize
        -- live INSERT with retirement by taking FOR SHARE on THIS row
        -- before checking ACTIVE, and hold it until caller transaction end.
        SELECT TRUE INTO reservation_active FROM aic_instance_reservations r
        WHERE r.agent_instance_id = NEW.agent_instance_id
          AND r.state = 'ACTIVE' FOR SHARE;
        SELECT TRUE INTO owner_active FROM users
        WHERE id = NEW.owner_user_id AND status = 'active' FOR SHARE;
        IF COALESCE(owner_active, FALSE) = FALSE
          OR COALESCE(reservation_active, FALSE) = FALSE
          OR NEW.lifecycle_state <> 'ACTIVE' OR NEW.generation <> 1 THEN
          RAISE EXCEPTION 'AIC_LIVE_OWNER_OR_ID_INVALID';
        END IF;
        RETURN NEW;
      END IF;
      SELECT TRUE INTO owner_active FROM users
      WHERE id = OLD.owner_user_id AND status = 'active' FOR SHARE;
      IF COALESCE(owner_active, FALSE) = FALSE
         OR NEW.agent_instance_id <> OLD.agent_instance_id
         OR NEW.owner_user_id <> OLD.owner_user_id
         OR NEW.definition_id <> OLD.definition_id
         OR NEW.definition_version <> OLD.definition_version
         OR OLD.lifecycle_state <> 'ACTIVE'
         OR NEW.lifecycle_state <> 'SUSPENDED'
         OR NEW.generation <> OLD.generation + 1 THEN
        RAISE EXCEPTION 'AIC_LIVE_UPDATE_FORBIDDEN';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql""",
    """CREATE FUNCTION aic_user_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM aic_instance_live l WHERE l.owner_user_id = OLD.id) THEN
          RAISE EXCEPTION 'AIC_LIVE_OWNER_DELETE_DENIED';
        END IF;
        RETURN OLD;
      END IF;
      IF NEW.id <> OLD.id
         AND EXISTS (SELECT 1 FROM aic_instance_live l WHERE l.owner_user_id = OLD.id) THEN
        RAISE EXCEPTION 'AIC_LIVE_OWNER_REKEY_DENIED';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql""",
)

# Schema is injected only from the dialect-quoted, verified durable target.
# Trigger target tables AND invoked functions must not resolve via pg_temp.
_PG_TRIGGERS = (
    "CREATE TRIGGER trg_aic_id_guard BEFORE INSERT OR UPDATE OR DELETE ON {schema}.aic_instance_reservations FOR EACH ROW EXECUTE FUNCTION {schema}.aic_id_guard()",
    "CREATE TRIGGER trg_aic_live_guard BEFORE INSERT OR UPDATE OR DELETE ON {schema}.aic_instance_live FOR EACH ROW EXECUTE FUNCTION {schema}.aic_live_guard()",
    "CREATE TRIGGER trg_aic_user_delete BEFORE DELETE ON {schema}.users FOR EACH ROW EXECUTE FUNCTION {schema}.aic_user_guard()",
    "CREATE TRIGGER trg_aic_user_rekey BEFORE UPDATE OF id ON {schema}.users FOR EACH ROW EXECUTE FUNCTION {schema}.aic_user_guard()",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in ("sqlite", "postgresql"):
        raise RuntimeError("AIC only supports SQLite/PostgreSQL verified backends")
    op.create_table(
        "aic_instance_reservations",
        sa.Column("agent_instance_id", sa.String(255), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("state IN ('ACTIVE','RETIRING','RETIRED')",
                           name="ck_aic_reservation_state"),
        sa.CheckConstraint("generation >= 1", name="ck_aic_reservation_generation"),
    )
    op.create_table(
        "aic_instance_live",
        sa.Column("agent_instance_id", sa.String(255),
                  sa.ForeignKey("aic_instance_reservations.agent_instance_id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("owner_user_id", sa.String(255),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("definition_id", sa.String(255), nullable=False),
        sa.Column("definition_version", sa.String(255), nullable=False),
        sa.Column("lifecycle_state", sa.String(16), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("lifecycle_state IN ('ACTIVE','SUSPENDED')", name="ck_aic_live_state"),
        sa.CheckConstraint("generation >= 1", name="ck_aic_live_generation"),
    )
    op.create_index("ix_aic_live_owner_state", "aic_instance_live",
                    ["owner_user_id", "lifecycle_state"])
    if bind.dialect.name == "sqlite":
        for statement in _SQLITE_GUARDS:
            op.execute(sa.text(statement))
    else:
        for statement in _PG_FUNCTIONS:
            op.execute(sa.text(statement))
        # A trigger executes with the caller's SQL search_path unless its
        # PL/pgSQL function is explicitly pinned. With default pg_temp
        # precedence, TEMP tables named users / aic_instance_reservations
        # can otherwise forge ACTIVE owner+marker checks and bind LIVE back
        # to a permanently RETIRED ID despite the real foreign keys.
        #
        # Capture the actual schema in which the migration just created the
        # AIC tables/functions, and lock the three function-local paths to
        # trusted pg_catalog, that real schema, and pg_temp LAST.
        # No runtime session GUC is changed and no cross-schema guesswork.
        aic_schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
        if not aic_schema or aic_schema.startswith("pg_temp"):
            raise RuntimeError("AIC PostgreSQL migration requires durable target schema")
        quoted_schema = bind.dialect.identifier_preparer.quote_identifier(aic_schema)
        for function in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
            op.execute(sa.text(
                f"ALTER FUNCTION {quoted_schema}.{function}() "
                f"SET search_path TO pg_catalog, {quoted_schema}, pg_temp"
            ))
        for statement in _PG_TRIGGERS:
            op.execute(sa.text(statement.format(schema=quoted_schema)))


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM aic_instance_reservations")).scalar_one()
    live = bind.execute(sa.text("SELECT COUNT(*) FROM aic_instance_live")).scalar_one()
    if count or live:
        raise RuntimeError("AIC downgrade denied: retiring reservations would permit ID reuse")
    if bind.dialect.name == "sqlite":
        for name in _SQLITE_TRIGGER_NAMES:
            op.execute(sa.text("DROP TRIGGER IF EXISTS " + name))
    elif bind.dialect.name == "postgresql":
        for name, table in (
            ("trg_aic_user_rekey", "users"),
            ("trg_aic_user_delete", "users"),
            ("trg_aic_live_guard", "aic_instance_live"),
            ("trg_aic_id_guard", "aic_instance_reservations"),
        ):
            op.execute(sa.text("DROP TRIGGER IF EXISTS " + name + " ON " + table))
        for name in ("aic_user_guard", "aic_live_guard", "aic_id_guard"):
            op.execute(sa.text("DROP FUNCTION IF EXISTS " + name + "()"))
    else:
        raise RuntimeError("AIC downgrade unsupported dialect")
    op.drop_index("ix_aic_live_owner_state", table_name="aic_instance_live")
    op.drop_table("aic_instance_live")
    op.drop_table("aic_instance_reservations")
