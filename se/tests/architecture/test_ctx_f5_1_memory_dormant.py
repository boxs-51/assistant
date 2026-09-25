from __future__ import annotations

import ast
from pathlib import Path

import se.src.context.memory as memory
from se.src.context.source_identity import ContextSourceKind


def test_ctx_f5_1_memory_module_has_no_sql_runtime_or_external_storage_dependencies():
    path = Path(memory.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_prefixes = (
        "sqlalchemy",
        "alembic",
        "fastapi",
        "se.src.infrastructure",
        "se.src.runtimes",
        "se.src.application",
        "se.src.provider",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )


def test_ctx_f5_1_memory_surface_is_dormant_and_has_no_retrieval_or_mutation_api():
    for forbidden_name in (
        "search",
        "rank",
        "score",
        "top_k",
        "embedding",
        "vector",
        "hydrate",
        "promote",
        "issue_promotion_authority",
        "delete",
        "update",
        "collect",
        "gc",
    ):
        assert not hasattr(memory, forbidden_name)

    assert "MEMORY" not in ContextSourceKind.__members__


def test_ctx_f5_1_reference_repository_surface_is_minimal():
    public = {
        name
        for name in dir(memory.InMemoryMemoryRecordRepository)
        if not name.startswith("_")
    }
    assert public == {
        "get",
        "get_by_promotion_authority",
        "put",
    }


def test_ctx_f5_1_domain_module_itself_has_no_durable_persistence_authority():
    # Historical F5-1 evidence freezes what that released domain module owns;
    # later independently released F5 stages may add separate persistence
    # modules without rewriting F5-1 authority.
    source = Path(memory.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "MemoryRecordRow",
        "DurableMemoryRecordRepository",
        "sqlalchemy",
        "alembic",
        "infrastructure.storage",
    ):
        assert forbidden not in source


def test_ctx_f5_1_no_runtime_module_imports_memory_foundation():
    roots = (
        Path("se/src/runtimes"),
        Path("se/src/application"),
        Path("se/src/provider"),
    )
    offenders: list[str] = []
    needles = (
        "se.src.context.memory",
        "InMemoryMemoryRecordRepository",
        "create_memory_record",
    )

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if any(needle in source for needle in needles):
                offenders.append(path.as_posix())

    assert offenders == []
