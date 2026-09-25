from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import UniqueConstraint

import se.src.infrastructure.storage.models.sql.memory as memory_model
import se.src.infrastructure.storage.repositories.memory as memory_repository
from se.src.context.source_identity import ContextSourceKind


def test_ctx_f5_2_repository_surface_is_exact_admission_only():
    public = {
        name
        for name in dir(memory_repository.DurableMemoryRecordRepository)
        if not name.startswith("_")
    }
    assert public == {
        "get",
        "get_by_promotion_authority",
        "put",
    }

    for forbidden in (
        "search",
        "list",
        "rank",
        "score",
        "top_k",
        "embedding",
        "vector",
        "promote",
        "hydrate",
        "update",
        "delete",
        "expire",
        "revoke",
        "erase",
        "gc",
    ):
        assert forbidden not in public


def test_ctx_f5_2_sql_model_has_only_frozen_identity_replay_constraints():
    table = memory_model.MemoryRecordRow.__table__
    assert table.name == "memory_records"
    assert table.c.memory_id.primary_key is True

    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques == {
        "uq_memory_records_promotion_authority": (
            "promotion_authority_id",
        )
    }


def test_ctx_f5_2_repository_has_no_runtime_application_or_provider_dependencies():
    path = Path(memory_repository.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden_prefixes = (
        "fastapi",
        "se.src.application",
        "se.src.runtimes",
        "se.src.provider",
        "se.src.transport",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in forbidden_prefixes
    )


def test_ctx_f5_2_remains_unwired_and_does_not_open_memory_source_kind():
    assert "MEMORY" not in ContextSourceKind.__members__

    roots = (
        Path("se/src/application"),
        Path("se/src/runtimes"),
        Path("se/src/provider"),
        Path("se/src/transport"),
    )
    needles = (
        "DurableMemoryRecordRepository",
        "models.sql.memory",
        "repositories.memory",
    )
    offenders: list[str] = []

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if any(needle in source for needle in needles):
                offenders.append(path.as_posix())

    assert offenders == []


def test_ctx_f5_2_alembic_env_registers_memory_model_metadata():
    env_source = Path(
        "se/src/infrastructure/storage/migrations/sql/env.py"
    ).read_text(encoding="utf-8")
    assert (
        "from se.src.infrastructure.storage.models.sql import memory"
        in env_source
    )


def test_ctx_f5_2_migration_is_single_linear_child_of_20a():
    migration = Path(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "21a_ctx_f5_memory_foundation.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "21a_ctx_f5_memory_foundation"' in migration
    assert (
        'down_revision: Union[str, None] = "20a_cas_f5_binding_foundation"'
        in migration
    )
    assert "op.create_table(" in migration
    assert '"memory_records"' in migration
    assert "op.drop_table(" in migration
