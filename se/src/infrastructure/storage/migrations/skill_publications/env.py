from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from se.src.infrastructure.storage.models.sql.capability.publication import (
    PublicationBase,
    SKILL_PUBLICATION_SCHEMA,
    SKILL_PUBLICATION_VERSION_TABLE,
)


config = context.config
target_metadata = PublicationBase.metadata
DSN_ENV = "ASSISTANT_SKILL_PUBLICATION_DATABASE_URL"


def _dsn() -> str:
    value = str(os.environ.get(DSN_ENV) or "").strip()
    if not value:
        raise RuntimeError(f"{DSN_ENV} is required for Skill publication migrations.")
    if not value.startswith("postgresql+asyncpg://"):
        raise RuntimeError("Skill publication migrations require postgresql+asyncpg://.")
    return value


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table=SKILL_PUBLICATION_VERSION_TABLE,
        version_table_schema=SKILL_PUBLICATION_SCHEMA,
    )
    with context.begin_transaction():
        context.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SKILL_PUBLICATION_SCHEMA}"))
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table=SKILL_PUBLICATION_VERSION_TABLE,
        version_table_schema=SKILL_PUBLICATION_SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_dsn(), poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"CREATE SCHEMA IF NOT EXISTS {SKILL_PUBLICATION_SCHEMA}")
            )
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
