from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3H_B1_MEMORY_REPLAY_EQUIVALENCE_HELPER.md"
)
MEMORY = Path("se/src/context/memory.py")
MEMORY_REPOSITORY = Path("se/src/infrastructure/storage/repositories/memory.py")


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"{name} not found in {path}")


def _class_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"{name} not found in {path}")


def test_ctx_f5_3h_b1_has_one_public_replay_equivalence_authority() -> None:
    memory = MEMORY.read_text(encoding="utf-8")
    helper = _function_source(MEMORY, "memory_records_replay_equivalent")

    assert "def memory_records_replay_equivalent(" in memory
    assert "def _same_immutable_record(" not in memory
    assert helper.count("_immutable_record_canonical_bytes(") == 2


def test_ctx_f5_3h_b1_preserves_legacy_canonical_replay_material() -> None:
    canonicalizer = _function_source(MEMORY, "_immutable_record_canonical_bytes")

    assert 'exclude={"created_at"}' in canonicalizer
    assert 'source_snapshot = material.get("source_ref_snapshot")' in canonicalizer
    assert 'source_snapshot.get("source_created_at")' in canonicalizer
    assert "_canonical_replay_datetime(" in canonicalizer
    assert "canonical_memory_bytes(material)" in canonicalizer


def test_ctx_f5_3h_b1_migrates_both_repositories_to_public_helper() -> None:
    memory = MEMORY.read_text(encoding="utf-8")
    repository = MEMORY_REPOSITORY.read_text(encoding="utf-8")
    in_memory = _class_source(MEMORY, "InMemoryMemoryRecordRepository")

    assert "memory_records_replay_equivalent(existing, record)" in in_memory
    assert "_same_immutable_record" not in in_memory
    assert "memory_records_replay_equivalent," in repository
    assert "memory_records_replay_equivalent(winner, incoming)" in repository
    assert "_same_immutable_record" not in repository


def test_ctx_f5_3h_b1_contract_keeps_scope_closed() -> None:
    contract = " ".join(CONTRACT.read_text(encoding="utf-8").split())

    for phrase in (
        "released production files = exactly 2",
        "ignore exactly top-level created_at",
        "preserve current source_ref_snapshot.source_created_at datetime normalization",
        "Memory identity/digest algorithms = UNCHANGED",
        "SQLite admission transaction semantics = UNCHANGED",
        "schema/migration delta = ZERO",
        "H-B2 production CLAIM = CLOSED",
        "runtime/API/source wiring = CLOSED",
    ):
        assert phrase in contract
