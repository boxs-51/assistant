"""AIC owner-bound LIVE rows + permanent ownerless ID reservations.

Revision ID: 31a_aic0_agent_instance_identity
Revises: 30a_ctx_f5_user_wide_memory_scope
"""
from alembic import op
import sqlalchemy as sa

revision = "31a_aic0_agent_instance_identity"
down_revision = "30a_ctx_f5_user_wide_memory_scope"
branch_labels = None
depends_on = None

SQLITE_TRIGGERS = {
    "trg_aic_users_live_delete": """CREATE TRIGGER trg_aic_users_live_delete BEFORE DELETE ON users
      WHEN EXISTS(SELECT 1 FROM aic_instance_live WHERE owner_user_id=OLD.id)
      BEGIN SELECT RAISE(ABORT,'AIC_LIVE_USER_DELETE_DENIED'); END""",
    "trg_aic_reservation_no_delete": """CREATE TRIGGER trg_aic_reservation_no_delete BEFORE DELETE ON aic_instance_id_reservations
      BEGIN SELECT RAISE(ABORT,'AIC_ID_NONREUSE'); END""",
    "trg_aic_reservation_transition": """CREATE TRIGGER trg_aic_reservation_transition BEFORE UPDATE ON aic_instance_id_reservations
      WHEN OLD.agent_instance_id != NEW.agent_instance_id
        OR OLD.status != 'LIVE' OR NEW.status != 'RETIRED'
        OR EXISTS(SELECT 1 FROM aic_instance_live WHERE agent_instance_id=OLD.agent_instance_id)
      BEGIN SELECT RAISE(ABORT,'AIC_RESERVATION_IMMUTABLE'); END""",
    "trg_aic_live_insert": """CREATE TRIGGER trg_aic_live_insert BEFORE INSERT ON aic_instance_live
      WHEN NOT EXISTS(SELECT 1 FROM aic_instance_id_reservations r WHERE r.agent_instance_id=NEW.agent_instance_id AND r.status='LIVE')
        OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.owner_user_id AND u.status='active')
      BEGIN SELECT RAISE(ABORT,'AIC_LIVE_OWNER_INVALID'); END""",
    "trg_aic_live_update": """CREATE TRIGGER trg_aic_live_update BEFORE UPDATE ON aic_instance_live
      WHEN NEW.agent_instance_id != OLD.agent_instance_id OR NEW.owner_user_id != OLD.owner_user_id
        OR NEW.definition_id != OLD.definition_id OR NEW.definition_version != OLD.definition_version
        OR NEW.generation != OLD.generation + 1 OR NEW.state != 'SUSPENDED'
        OR OLD.state != 'ACTIVE'
        OR NOT EXISTS(SELECT 1 FROM users u WHERE u.id=NEW.owner_user_id AND u.status='active')
      BEGIN SELECT RAISE(ABORT,'AIC_LIVE_MUTATION_DENIED'); END""",
    "trg_aic_live_retired_insert": """CREATE TRIGGER trg_aic_live_retired_insert BEFORE INSERT ON aic_instance_live
      WHEN EXISTS(SELECT 1 FROM aic_instance_id_reservations r
                  WHERE r.agent_instance_id=NEW.agent_instance_id AND r.status='RETIRED')
      BEGIN SELECT RAISE(ABORT,'AIC_RETIRED_ID_REPLAY'); END""",
}

PG_FUNCTIONS = {
    "aic_guard_user_delete": """CREATE FUNCTION aic_guard_user_delete() RETURNS trigger AS $$
      BEGIN IF EXISTS(SELECT 1 FROM aic_instance_live WHERE owner_user_id=OLD.id) THEN
      RAISE EXCEPTION 'AIC_LIVE_USER_DELETE_DENIED'; END IF; RETURN OLD; END;
      $$ LANGUAGE plpgsql""",
    "aic_guard_reservation": """CREATE FUNCTION aic_guard_reservation() RETURNS trigger AS $$
      BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'AIC_ID_NONREUSE'; END IF;
        IF OLD.agent_instance_id <> NEW.agent_instance_id OR OLD.status <> 'LIVE'
          OR NEW.status <> 'RETIRED'
          OR EXISTS(SELECT 1 FROM aic_instance_live WHERE agent_instance_id=OLD.agent_instance_id)
        THEN RAISE EXCEPTION 'AIC_RESERVATION_IMMUTABLE'; END IF;
        RETURN NEW; END; $$ LANGUAGE plpgsql""",
    "aic_guard_live": """CREATE FUNCTION aic_guard_live() RETURNS trigger AS $$
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NOT EXISTS (SELECT 1 FROM aic_instance_id_reservations WHERE agent_instance_id=NEW.agent_instance_id AND status='LIVE')
            OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.owner_user_id AND status='active')
          THEN RAISE EXCEPTION 'AIC_LIVE_OWNER_INVALID'; END IF;
        ELSE
          IF NEW.agent_instance_id <> OLD.agent_instance_id
            OR NEW.owner_user_id <> OLD.owner_user_id
            OR NEW.definition_id <> OLD.definition_id
            OR NEW.definition_version <> OLD.definition_version
            OR NEW.generation <> OLD.generation+1
            OR NEW.state <> 'SUSPENDED' OR OLD.state <> 'ACTIVE'
            OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.owner_user_id AND status='active')
          THEN RAISE EXCEPTION 'AIC_LIVE_MUTATION_DENIED'; END IF;
        END IF; RETURN NEW;
      END; $$ LANGUAGE plpgsql"""
}


def upgrade():
    op.create_table("aic_instance_id_reservations",
        sa.Column("agent_instance_id",sa.String(255),primary_key=True),
        sa.Column("status",sa.String(16),nullable=False),
        sa.CheckConstraint("status IN ('LIVE','RETIRED')",name="ck_aic_reservation_state"))
    op.create_table("aic_instance_live",
        sa.Column("agent_instance_id",sa.String(255),sa.ForeignKey("aic_instance_id_reservations.agent_instance_id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("owner_user_id",sa.String(255),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("definition_id",sa.String(255),nullable=False),
        sa.Column("definition_version",sa.String(255),nullable=False),
        sa.Column("state",sa.String(16),nullable=False),
        sa.Column("generation",sa.Integer(),nullable=False),
        sa.CheckConstraint("state IN ('ACTIVE','SUSPENDED')",name="ck_aic_live_state"),
        sa.CheckConstraint("generation >= 1",name="ck_aic_live_generation"))
    op.create_index("ix_aic_instance_live_owner_user_id","aic_instance_live",["owner_user_id"])
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for trigger in SQLITE_TRIGGERS.values():
            op.execute(sa.text(trigger))
    elif dialect == "postgresql":
        for sql in PG_FUNCTIONS.values():
            op.execute(sa.text(sql))
        op.execute(sa.text("""CREATE TRIGGER trg_aic_users_live_delete BEFORE DELETE ON users
            FOR EACH ROW EXECUTE FUNCTION aic_guard_user_delete()"""))
        op.execute(sa.text("""CREATE TRIGGER trg_aic_reservation_guard BEFORE UPDATE OR DELETE
            ON aic_instance_id_reservations FOR EACH ROW EXECUTE FUNCTION aic_guard_reservation()"""))
        op.execute(sa.text("""CREATE TRIGGER trg_aic_live_guard BEFORE INSERT OR UPDATE
            ON aic_instance_live FOR EACH ROW EXECUTE FUNCTION aic_guard_live()"""))
    else:
        raise RuntimeError("AIC migrations only support SQLite and PostgreSQL")


def downgrade():
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM aic_instance_id_reservations")).scalar_one()
    if count:
        raise RuntimeError("AIC_DOWNGRADE_DENIED_NONREUSABLE_IDS")
    dialect = bind.dialect.name
    if dialect == "sqlite":
        for name in SQLITE_TRIGGERS:
            op.execute(sa.text("DROP TRIGGER IF EXISTS " + name))
    elif dialect == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_aic_users_live_delete ON users"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_aic_reservation_guard ON aic_instance_id_reservations"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_aic_live_guard ON aic_instance_live"))
        for name in PG_FUNCTIONS:
            op.execute(sa.text("DROP FUNCTION IF EXISTS "+name+"()"))
    else:
        raise RuntimeError("Unsupported AIC downgrade dialect")
    op.drop_index("ix_aic_instance_live_owner_user_id",table_name="aic_instance_live")
    op.drop_table("aic_instance_live")
    op.drop_table("aic_instance_id_reservations")
