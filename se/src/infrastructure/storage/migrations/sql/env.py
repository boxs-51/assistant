import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from se.src.infrastructure.storage.models.sql.chat_data import attachment
from se.src.infrastructure.storage.models.sql.chat_data import project
from se.src.infrastructure.storage.models.sql.user_data import api_key, application, member, oauth_account, organization, pending_registration
from se.src.infrastructure.storage.models.sql.user_data import permission

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

# --- THAY ĐỔI QUAN TRỌNG ---
# Import Base từ model của chúng ta để Alembic có thể "thấy" được các table
from se.src.infrastructure.storage.models.sql.user_data import (
    user
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.chat_data import (
    session
) 
from se.src.infrastructure.storage.models.sql import agent
from se.src.infrastructure.storage.models.sql import capability
from se.src.infrastructure.storage.models.sql import assets
from se.src.infrastructure.storage.models.sql import memory
from se.src.infrastructure.storage.models.sql import user_budget
target_metadata = Base.metadata

# --- CẤU HÌNH DATABASE URL ---
# Lấy URL từ cấu hình của ứng dụng thay vì hardcode
# Tạm thời chúng ta sẽ hardcode URL của SQLite để đơn giản hóa
# Trong thực tế, bạn sẽ đọc từ file config của ứng dụng
DB_URL = os.getenv(
    "ASSISTANT_ALEMBIC_DATABASE_URL",
    "sqlite+aiosqlite:///data/gateway_storage.db",
)
config.set_main_option("sqlalchemy.url", DB_URL)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, 
                      target_metadata=target_metadata,
                      render_as_batch=True)

    with context.begin_transaction():
        context.run_migrations()


# Cooperative session lock survives preflight COMMIT and the Alembic migration.
# A legacy migrator that ignores this lock remains an external deployment risk.
_PG_ALEMBIC_LOCK = "1977, 156426"


def _prepare_postgresql_version_table(connection: Connection) -> None:
    """Idempotently widen only Alembic bookkeeping, never stamp or delete."""
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version ("
        "version_num VARCHAR(255) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    connection.exec_driver_sql("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE")
    column = connection.execute(text("""
        SELECT data_type, character_maximum_length, is_nullable
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'alembic_version' AND column_name = 'version_num'
    """)).one_or_none()
    pk = connection.execute(text("""
        SELECT COUNT(*)
          FROM pg_catalog.pg_constraint AS con
          JOIN pg_catalog.pg_class AS rel ON rel.oid = con.conrelid
          JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
         WHERE ns.nspname = current_schema()
           AND rel.relname = 'alembic_version'
           AND rel.relkind IN ('r', 'p')
           AND con.contype = 'p'
           AND pg_catalog.pg_get_constraintdef(con.oid)
               = 'PRIMARY KEY (version_num)'
    """)).scalar_one()
    if (
        column is None
        or column.data_type != "character varying"
        or column.is_nullable != "NO"
        or column.character_maximum_length is None
        or pk != 1
    ):
        raise RuntimeError(
            "Unsafe PostgreSQL Alembic version table shape; upgrade denied"
        )
    if column.character_maximum_length < 255:
        connection.exec_driver_sql(
            "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"
        )


async def run_async_migrations() -> None:
    """Run online migrations with PostgreSQL version-width preflight."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            if connection.dialect.name != "postgresql":
                await connection.run_sync(do_run_migrations)
                return

            acquired = (await connection.exec_driver_sql(
                "SELECT pg_try_advisory_lock(" + _PG_ALEMBIC_LOCK + ")"
            )).scalar_one()
            await connection.commit()
            if not acquired:
                raise RuntimeError("Concurrent PostgreSQL Alembic migration denied")
            try:
                # Separate preflight transaction, preserving Alembic's native
                # context.begin_transaction semantics for every revision.
                await connection.run_sync(_prepare_postgresql_version_table)
                await connection.commit()
                await connection.run_sync(do_run_migrations)
            finally:
                await connection.rollback()
                unlocked = (await connection.exec_driver_sql(
                    "SELECT pg_advisory_unlock(" + _PG_ALEMBIC_LOCK + ")"
                )).scalar_one()
                await connection.commit()
                if not unlocked:
                    raise RuntimeError("PostgreSQL Alembic migration lock lost")
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

#alembic revision --autogenerate -m "commit"
#alembic upgrade head
