"""Forward-only repair of installed 31a PostgreSQL AIC guard functions.

Revision ID: 31b_aic0_pg_guard_forward
Revises: 31a_aic0_agent_instance_identity

A database that already stamped 31a will not rerun that revision when its
source changes. This revision repairs existing physical function definitions
transactionally, after verifying the trusted guard trigger attachments.
Never infer the physical relation target from session search_path.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "31b_aic0_pg_guard_forward"
down_revision: Union[str, None] = "31a_aic0_agent_instance_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EXPECTED_TRIGGERS = {
    "trg_aic_id_guard": ("aic_instance_reservations", "aic_id_guard", 31),
    "trg_aic_live_guard": ("aic_instance_live", "aic_live_guard", 31),
    "trg_aic_user_delete": ("users", "aic_user_guard", 11),
    "trg_aic_user_rekey": ("users", "aic_user_guard", 19),
}

# Self-contained, frozen trusted function bodies from the AIC 31a contract.
# CREATE OR REPLACE preserves the existing function OID referenced by triggers.
# The public migration profile is separately guarded by Alembic env.py (#494).
_TRUSTED_PG_FUNCTIONS = (
    """CREATE OR REPLACE FUNCTION {schema}.aic_id_guard() RETURNS trigger AS $$
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
    """CREATE OR REPLACE FUNCTION {schema}.aic_live_guard() RETURNS trigger AS $$
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
    """CREATE OR REPLACE FUNCTION {schema}.aic_user_guard() RETURNS trigger AS $$
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


def _check_physical_attachments(bind, schema: str) -> None:
    """Reject missing, shadowed, disabled or rebound physical guard objects."""
    functions = bind.execute(sa.text("""
        SELECT p.proname, p.pronargs, p.prosecdef,
               p.prorettype = 'pg_catalog.trigger'::pg_catalog.regtype,
               lang.lanname
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace ns ON ns.oid = p.pronamespace
          JOIN pg_catalog.pg_language lang ON lang.oid = p.prolang
         WHERE ns.nspname = :schema
           AND p.proname IN ('aic_id_guard', 'aic_live_guard', 'aic_user_guard')
    """), {"schema": schema}).all()
    if len(functions) != 3 or {
        name for name, args, security_definer, is_trigger, language in functions
        if args == 0 and not security_definer and is_trigger and language == "plpgsql"
    } != {"aic_id_guard", "aic_live_guard", "aic_user_guard"}:
        raise RuntimeError("AIC 31b: missing or altered durable guard function")

    triggers = bind.execute(sa.text("""
        SELECT t.tgname, relation.relname, proc.proname,
               proc_ns.nspname, t.tgtype, t.tgenabled, t.tgisinternal,
               t.tgattr::text
          FROM pg_catalog.pg_trigger t
          JOIN pg_catalog.pg_class relation ON relation.oid = t.tgrelid
          JOIN pg_catalog.pg_namespace relation_ns
            ON relation_ns.oid = relation.relnamespace
          JOIN pg_catalog.pg_proc proc ON proc.oid = t.tgfoid
          JOIN pg_catalog.pg_namespace proc_ns ON proc_ns.oid = proc.pronamespace
         WHERE relation_ns.nspname = :schema
           AND t.tgname IN (
                'trg_aic_id_guard', 'trg_aic_live_guard',
                'trg_aic_user_delete', 'trg_aic_user_rekey'
           )
           AND relation.relkind IN ('r', 'p')
    """), {"schema": schema}).all()
    if len(triggers) != 4 or {row[0] for row in triggers} != set(_EXPECTED_TRIGGERS):
        raise RuntimeError("AIC 31b: durable guard triggers absent/duplicated")
    for name, table, function, fn_schema, event_mask, enabled, internal, attrs in triggers:
        expected_table, expected_function, expected_mask = _EXPECTED_TRIGGERS[name]
        if (table != expected_table or function != expected_function
                or fn_schema != schema or event_mask != expected_mask
                or enabled != "O" or internal
                or (name == "trg_aic_user_rekey" and not attrs.strip())
                or (name != "trg_aic_user_rekey" and bool(attrs.strip()))):
            raise RuntimeError(
                "AIC 31b: invalid physical guard trigger "
                f"{name}: table={table!r}, function={function!r}, "
                f"function_schema={fn_schema!r}, type={event_mask!r}, "
                f"enabled={enabled!r}, internal={internal!r}, "
                f"update_columns={attrs!r}"
            )

    corrupted = bind.execute(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM {schema}.aic_instance_live l
            JOIN {schema}.aic_instance_reservations r
              ON r.agent_instance_id = l.agent_instance_id
            WHERE r.state = 'RETIRED'
        )
    """.replace("{schema}", bind.dialect.identifier_preparer.quote_identifier(schema)))).scalar_one()
    if corrupted:
        raise RuntimeError("AIC 31b: retired ID already rebound; repair denied")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # No PostgreSQL functions exist. Keep FK-OFF and 31a SQLite guards intact.
        return
    if bind.dialect.name != "postgresql":
        raise RuntimeError("AIC 31b: unsupported SQL dialect")

    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one_or_none()
    if not schema or schema.startswith("pg_temp") or schema == "pg_catalog":
        raise RuntimeError("AIC 31b requires a verified durable target schema")
    quoted_schema = bind.dialect.identifier_preparer.quote_identifier(schema)

    # PostgreSQL transactional DDL: a failed check/replacement cannot stamp 31b.
    # In production the migration role and public-only search_path are fenced by
    # the independently reviewed #494 Alembic online preflight.
    _check_physical_attachments(bind, schema)
    for statement in _TRUSTED_PG_FUNCTIONS:
        op.execute(sa.text(statement.format(schema=quoted_schema)))
    for function in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
        op.execute(sa.text(
            f"ALTER FUNCTION {quoted_schema}.{function}() "
            f"SET search_path TO pg_catalog, {quoted_schema}, pg_temp"
        ))
    _check_physical_attachments(bind, schema)
    for function in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
        config = bind.execute(sa.text("""
            SELECT p.proconfig
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = :schema AND p.proname = :function
              AND p.pronargs = 0
        """), {"schema": schema, "function": function}).scalar_one()
        if not config or len(config) != 1 or not config[0].startswith(
            "search_path=pg_catalog,"
        ) or not config[0].endswith(", pg_temp"):
            raise RuntimeError("AIC 31b: guard function search_path not pinned")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The SQLite upgrade is inert: it creates no guard functions or tables.
        # Historical SQLite down-migrations must remain reversible.
        return
    if bind.dialect.name == "postgresql":
        # Restoring vulnerable PostgreSQL guards is never an authorized rollback.
        raise RuntimeError("AIC 31b PostgreSQL security downgrade denied")
    raise RuntimeError("AIC 31b: unsupported SQL dialect")
