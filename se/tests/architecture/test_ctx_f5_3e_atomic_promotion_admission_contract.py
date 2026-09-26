import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3E_ATOMIC_PROMOTION_ADMISSION_FD5B2D2E.md"
)
MEMORY_DOMAIN = Path("se/src/context/memory.py")
MEMORY_MODEL = Path("se/src/infrastructure/storage/models/sql/memory.py")
MEMORY_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/memory.py"
)
F5_3D_CONTRACT = Path(
    "docs/context_future/CTX_F5_3D_TRUSTED_RESERVATION_AUTHORITY_4EBAA775.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).replace("`", "").split())


def _class_async_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(_read(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef)
            }
    raise AssertionError(f"class {class_name} not found in {path}")


def _function_source(path: Path, function_name: str) -> str:
    source = _read(path)
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                end = getattr(node, "end_lineno", node.lineno)
                return "\n".join(lines[node.lineno - 1 : end])
    raise AssertionError(f"function {function_name} not found in {path}")


def test_ctx_f5_3e_freezes_one_authoritative_atomic_admission_operation():
    text = _normalized(CONTRACT)

    required = (
        "exact Memory record is durable AND exact trusted reservation transitions "
        "ISSUED -> CONSUMED",
        "There is no successful durable outcome where only one side commits",
        "Standalone PromotionReservationVerifier.verify(...) remains non-consuming",
        "Verifier success is insufficient as the final durable admission handoff",
        "verify -> independently put Memory -> best-effort mark reservation consumed",
        "That split sequence is explicitly insufficient",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_freezes_single_transaction_boundary_and_sqlite_discipline():
    text = _normalized(CONTRACT)
    repository_source = _read(MEMORY_REPOSITORY)

    required = (
        "One authoritative transaction/session must perform",
        "load and validate the trusted reservation state",
        "revalidate exact canonical MemoryPromotionIntent",
        "perform existing Memory admission/replay logic",
        "transition the same reservation from ISSUED to CONSUMED",
        "commit exactly once",
        "same authoritative session/connection must encompass reservation validation "
        "and Memory admission",
    )
    for phrase in required:
        assert phrase in text

    assert "BEGIN IMMEDIATE" in repository_source
    assert "sqlite_memory_admission_transaction" in repository_source
    assert "write intent before any read" in repository_source
    assert "await session.commit()" in repository_source
    assert "await session.rollback()" in repository_source


def test_ctx_f5_3e_binds_existing_memory_uniqueness_and_repository_surface():
    model_source = _read(MEMORY_MODEL)
    methods = _class_async_methods(
        MEMORY_REPOSITORY,
        "DurableMemoryRecordRepository",
    )

    assert '"promotion_authority_id"' in model_source
    assert "UniqueConstraint" in model_source
    assert "uq_memory_records_promotion_authority" in model_source

    assert {"put", "get", "get_by_promotion_authority"} <= methods

    protocol_methods = _class_async_methods(
        MEMORY_DOMAIN,
        "MemoryRecordRepository",
    )
    assert protocol_methods == {
        "put",
        "get",
        "get_by_promotion_authority",
    }


def test_ctx_f5_3e_binds_canonical_replay_and_non_authorizing_constructor():
    memory_source = _read(MEMORY_DOMAIN)
    constructor = _function_source(MEMORY_DOMAIN, "create_memory_record")
    text = _normalized(CONTRACT)

    assert "def _same_immutable_record" in memory_source
    assert "_immutable_record_canonical_bytes" in memory_source
    assert "canonical_memory_bytes" in memory_source
    assert "already-authorized provenance input" in constructor

    required = (
        "These primitives are not promotion authorization by themselves",
        "create_memory_record(...) constructs a Memory value from "
        "already-authorized provenance input",
        "F5-3E does not modify them",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_freezes_ambiguous_commit_replay_and_corruption_fail_closed():
    text = _normalized(CONTRACT)

    required = (
        "retry of the same exact canonical intent must converge on the already "
        "committed Memory result",
        "reservation state = CONSUMED",
        "exact Memory exists for same promotion_authority_id",
        "immutable Memory material is exact replay-equivalent",
        "If CONSUMED exists but Memory is absent",
        "fails closed as durable authority corruption/conflict",
        "No new promotion_authority_id may be minted to escape ambiguous commit state",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_freezes_concurrent_admission_and_cancellation_boundaries():
    text = _normalized(CONTRACT)

    required = (
        "Two concurrent admission attempts using one exact reservation cannot create "
        "two Memory records or two independent successful commits",
        "one authoritative transaction wins",
        "same exact canonical intent converges on the same committed Memory",
        "promotion_authority_id remains defense-in-depth",
        "reservation remains non-consumed",
        "Memory is not durably admitted",
        "response lost or cancellation observed by caller",
        "retry recovers exact committed Memory",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_freezes_exact_canonical_intent_inside_durability_boundary():
    text = _normalized(CONTRACT)

    required = (
        "authoritative transaction must revalidate the complete trusted promotion "
        "material inside the same durability boundary",
        "exact context_source_id",
        "exact proof receipt",
        "exact authority state token",
        "MEMORY_PROMOTION proof scope",
        "exact content and content digest",
        "exact canonical metadata",
        "Memory schema version",
        "exact promotion_authority_id",
        "Python dict/object equality is not sufficient authority comparison",
        "canonical_memory_bytes(...) remains the canonical Memory JSON representation "
        "boundary",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_preserves_f5_3d_states_and_non_consuming_verifier():
    current = _normalized(CONTRACT)
    prior = _normalized(F5_3D_CONTRACT)

    for state in ("ISSUED", "CONSUMED", "REVOKED"):
        assert state in prior
        assert state in current

    assert "Verifier success is non-consuming" in prior
    assert (
        "PromotionReservationVerifier.verify(...) still succeeds only for an eligible "
        "ISSUED reservation"
        in current
    )


def test_ctx_f5_3e_freezes_failure_taxonomy_and_authority_boundaries():
    text = _normalized(CONTRACT)

    failures = (
        "ADMISSION_RESERVATION_NOT_ISSUED",
        "ADMISSION_RESERVATION_REVOKED",
        "ADMISSION_INTENT_CONFLICT",
        "ADMISSION_MEMORY_REPLAY_CONFLICT",
        "ADMISSION_CONSUMED_MEMORY_MISSING",
        "ADMISSION_CONSUMED_MEMORY_MISMATCH",
        "ADMISSION_TRANSACTION_UNAVAILABLE",
        "ADMISSION_PERSISTENCE_FAILURE",
    )
    for failure in failures:
        assert failure in text

    required = (
        "concrete supported source kinds = NONE",
        "CAS authority transfer = NONE",
        "R11/R12 authority transfer = NONE",
        "reservation SQL model/schema/migration = CLOSED",
        "reservation repository implementation = CLOSED",
        "atomic admission runtime/orchestrator = CLOSED",
        "production MemoryRecordRepository modification = CLOSED",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3e_evidence_is_future_compatible_and_scope_bounded():
    text = _normalized(CONTRACT)

    required = (
        "Evidence must not add a permanent live-source absence invariant",
        "or a permanent repository/schema/runtime absence invariant",
        "exactly this two-file zero-production delta",
        "fresh Linux + Windows Architecture GREEN/GREEN",
        "independent FINAL GREEN",
        "Issue #15 zero-production contract/evidence merge policy may apply",
    )
    for phrase in required:
        assert phrase in text
