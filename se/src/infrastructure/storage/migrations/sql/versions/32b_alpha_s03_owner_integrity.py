"""Forward-only S03 physical owner/identity retention after the 32a merge.

Revision ID: 32b_alpha_s03_owner_integrity
Revises: 32a_alpha_31b_31t_merge

SQLite foreign_keys=OFF and recursive_triggers=OFF are untrusted settings.
The durable witness is NOT deleted on child deletion or user replacement.
PostgreSQL uses a real FK for CTX memory and guarded raw CAS identity writes.
Never rewrite 31a/31b/31t/32a or weaken their owner/permission guards.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "32b_alpha_s03_owner_integrity"
down_revision = "32a_alpha_31b_31t_merge"
branch_labels = None
depends_on = None

_OWNER_SOURCES = (
    ("memory_records", "owner_user_id"),
    ("files", "owner_user_id"),
    ("user_budget_policies", "owner_user_id"),
    ("user_budget_accounts", "owner_user_id"),
    ("user_budget_windows", "owner_user_id"),
    ("user_tool_budget_usage", "owner_user_id"),
    ("user_budget_reservations", "owner_user_id"),
    ("user_budget_task_bindings", "owner_user_id"),
    ("user_budget_dual_accounting_receipts", "owner_user_id"),
)


def _sqlite_upgrade() -> None:
    # Witnesses survive REPLACE's implicit victim DELETE with recursive
    # triggers disabled. A BEFORE INSERT check here would incorrectly deny
    # valid ON CONFLICT DO NOTHING replays; test collisions AFTER insertion.
    op.execute(sa.text("""
    CREATE TABLE s03_owner_witness (
      owner_user_id TEXT PRIMARY KEY NOT NULL,
      owner_email TEXT NOT NULL UNIQUE
    )"""))
    op.execute(sa.text("""
    CREATE TABLE s03_memory_witness (
      memory_id TEXT PRIMARY KEY NOT NULL,
      promotion_authority_id TEXT NOT NULL UNIQUE
    )"""))
    db = op.get_bind()
    # Do not silently adopt pre-existing orphaned or ambiguous retained data.
    for table, owner in _OWNER_SOURCES:
        orphans = db.execute(sa.text(
            "SELECT COUNT(*) FROM " + table +
            " AS c LEFT JOIN users AS u ON u.id=c." + owner +
            " WHERE u.id IS NULL"
        )).scalar_one()
        if orphans:
            raise RuntimeError("S03_EXISTING_OWNER_ORPHANS_" + table)
        op.execute(sa.text(
            "INSERT OR IGNORE INTO s03_owner_witness(owner_user_id,owner_email) "
            "SELECT DISTINCT u.id,u.email FROM " + table +
            " AS c JOIN users AS u ON u.id=c." + owner
        ))
    op.execute(sa.text(
        "INSERT INTO s03_memory_witness(memory_id,promotion_authority_id) "
        "SELECT memory_id,promotion_authority_id FROM memory_records"
    ))

    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_user_insert AFTER INSERT ON users BEGIN
      SELECT RAISE(ABORT,'S03_OWNER_REPLACE_DENIED')
      WHERE EXISTS(SELECT 1 FROM s03_owner_witness w
                   WHERE w.owner_user_id=NEW.id OR w.owner_email=NEW.email);
    END"""))
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_user_delete BEFORE DELETE ON users
    WHEN EXISTS(SELECT 1 FROM s03_owner_witness w WHERE w.owner_user_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'S03_RETAINED_OWNER_DELETE_DENIED'); END"""))
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_user_rekey BEFORE UPDATE OF id,email ON users
    WHEN (NEW.id != OLD.id OR NEW.email != OLD.email) AND EXISTS(
      SELECT 1 FROM s03_owner_witness w
      WHERE w.owner_user_id=OLD.id OR w.owner_user_id=NEW.id
         OR w.owner_email=NEW.email)
    BEGIN SELECT RAISE(ABORT,'S03_RETAINED_OWNER_REKEY_DENIED'); END"""))

    for table, owner in _OWNER_SOURCES:
        op.execute(sa.text(
            "CREATE TRIGGER trg_s03_" + table + "_owner_insert "
            "AFTER INSERT ON " + table + " BEGIN "
            "SELECT RAISE(ABORT,'S03_CHILD_OWNER_MISSING') WHERE "
            "NOT EXISTS(SELECT 1 FROM users WHERE id=NEW." + owner + "); "
            "INSERT OR IGNORE INTO s03_owner_witness(owner_user_id,owner_email) "
            "SELECT id,email FROM users WHERE id=NEW." + owner + "; "
            "END"
        ))

    # Immutable memory: even a no-op UPDATE or UPSERT DO UPDATE is denied.
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_memory_insert AFTER INSERT ON memory_records BEGIN
      SELECT RAISE(ABORT,'S03_MEMORY_PROVENANCE_INVALID')
      WHERE NOT json_valid(NEW.source_ref_json)
         OR json_type(NEW.source_ref_json) != 'object'
         OR COALESCE(json_extract(NEW.source_ref_json,'$.owner_user_id'),'')
              != NEW.owner_user_id;
      SELECT RAISE(ABORT,'S03_MEMORY_REPLACE_DENIED')
      WHERE EXISTS(
        SELECT 1 FROM s03_memory_witness w
        WHERE w.memory_id=NEW.memory_id
           OR w.promotion_authority_id=NEW.promotion_authority_id);
      INSERT INTO s03_memory_witness(memory_id,promotion_authority_id)
      VALUES (NEW.memory_id,NEW.promotion_authority_id);
    END"""))
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_memory_update BEFORE UPDATE ON memory_records
    BEGIN SELECT RAISE(ABORT,'S03_MEMORY_IMMUTABLE'); END"""))
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_memory_delete BEFORE DELETE ON memory_records
    BEGIN SELECT RAISE(ABORT,'S03_MEMORY_IMMUTABLE'); END"""))

    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_file_immutable BEFORE UPDATE ON files
    WHEN NEW.id != OLD.id OR NEW.owner_user_id != OLD.owner_user_id
      OR NEW.blob_id != OLD.blob_id
    BEGIN SELECT RAISE(ABORT,'S03_FILE_IDENTITY_IMMUTABLE'); END"""))
    op.execute(sa.text("""
    CREATE TRIGGER trg_s03_binding_immutable BEFORE UPDATE
    ON file_provider_bindings
    WHEN NEW.id != OLD.id OR NEW.file_id != OLD.file_id
      OR NEW.provider_name != OLD.provider_name
      OR NEW.provider_namespace != OLD.provider_namespace
      OR NEW.source_blob_id IS NOT OLD.source_blob_id
      OR NEW.source_sha256 IS NOT OLD.source_sha256
    BEGIN SELECT RAISE(ABORT,'S03_PROVIDER_BINDING_IMMUTABLE'); END"""))


def _pg_upgrade() -> None:
    db = op.get_bind()
    schema = db.exec_driver_sql("SELECT current_schema()").scalar_one()
    ident = db.dialect.identifier_preparer.quote(schema)

    # Genuine FK: PostgreSQL's internal FK row locks serialize child INSERT
    # and parent DELETE/REKEY across sessions, including two-PID races.
    db.exec_driver_sql(
        "ALTER TABLE " + ident + ".memory_records "
        "ADD CONSTRAINT fk_s03_memory_owner "
        "FOREIGN KEY(owner_user_id) REFERENCES " + ident +
        ".users(id) ON DELETE RESTRICT ON UPDATE RESTRICT"
    )

    # Bind function names and table references to the migration's trusted
    # schema, never to the invoker's arbitrary search_path.
    db.exec_driver_sql(f"""
    CREATE FUNCTION {ident}.s03_memory_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = {ident}, pg_catalog AS $$
    BEGIN
      IF TG_OP != 'INSERT' THEN
        RAISE EXCEPTION 'S03_MEMORY_IMMUTABLE';
      END IF;
      IF jsonb_typeof(NEW.source_ref_json::jsonb) IS DISTINCT FROM 'object'
         OR (NEW.source_ref_json::jsonb ->> 'owner_user_id')
            IS DISTINCT FROM NEW.owner_user_id THEN
        RAISE EXCEPTION 'S03_MEMORY_PROVENANCE_INVALID';
      END IF;
      RETURN NEW;
    END $$""")
    db.exec_driver_sql(f"""
    CREATE TRIGGER trg_s03_memory_guard
    BEFORE INSERT OR UPDATE OR DELETE ON {ident}.memory_records
    FOR EACH ROW EXECUTE FUNCTION {ident}.s03_memory_guard()""")
    db.exec_driver_sql(f"""
    CREATE FUNCTION {ident}.s03_file_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = {ident}, pg_catalog AS $$
    BEGIN
      IF NEW.id IS DISTINCT FROM OLD.id
         OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
         OR NEW.blob_id IS DISTINCT FROM OLD.blob_id THEN
        RAISE EXCEPTION 'S03_FILE_IDENTITY_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$""")
    db.exec_driver_sql(f"""
    CREATE TRIGGER trg_s03_file_guard BEFORE UPDATE ON {ident}.files
    FOR EACH ROW EXECUTE FUNCTION {ident}.s03_file_guard()""")
    db.exec_driver_sql(f"""
    CREATE FUNCTION {ident}.s03_binding_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = {ident}, pg_catalog AS $$
    BEGIN
      IF NEW.id IS DISTINCT FROM OLD.id
         OR NEW.file_id IS DISTINCT FROM OLD.file_id
         OR NEW.provider_name IS DISTINCT FROM OLD.provider_name
         OR NEW.provider_namespace IS DISTINCT FROM OLD.provider_namespace
         OR NEW.source_blob_id IS DISTINCT FROM OLD.source_blob_id
         OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256 THEN
        RAISE EXCEPTION 'S03_PROVIDER_BINDING_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$""")
    db.exec_driver_sql(f"""
    CREATE TRIGGER trg_s03_binding_guard
    BEFORE UPDATE ON {ident}.file_provider_bindings
    FOR EACH ROW EXECUTE FUNCTION {ident}.s03_binding_guard()""")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _sqlite_upgrade()
    elif dialect == "postgresql":
        _pg_upgrade()
    else:
        raise RuntimeError("S03_UNSUPPORTED_DB_DIALECT")


def downgrade() -> None:
    raise RuntimeError("S03_OWNER_INTEGRITY_FORWARD_ONLY")
