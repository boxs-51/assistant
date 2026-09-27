import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3F_A_DURABLE_RESERVATION_PERSISTENCE_CONTRACT_20019937.md"
)
PROMOTION = Path("se/src/context/memory_promotion.py")
SOURCE_IDENTITY = Path("se/src/context/source_identity.py")
MEMORY_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/memory.py"
)
F5_3D = Path(
    "docs/context_future/CTX_F5_3D_TRUSTED_RESERVATION_AUTHORITY_4EBAA775.md"
)
F5_3E = Path(
    "docs/context_future/CTX_F5_3E_ATOMIC_PROMOTION_ADMISSION_FD5B2D2E.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(
        _read(path).replace("`", "").replace("**", "").split()
    )


def _class_fields(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(_read(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
            }
    raise AssertionError(f"class {class_name} not found")


def test_ctx_f5_3f_a_keeps_caller_envelope_separate_from_trusted_state():
    fields = _class_fields(PROMOTION, "PromotionReservation")
    promotion_source = _read(PROMOTION)
    text = _normalized(CONTRACT)

    assert {"promotion_authority_id", "intent"} <= fields
    assert {
        "state",
        "revision",
        "created_at",
        "updated_at",
        "consumed_at",
        "revoked_at",
        "lock_token",
    }.isdisjoint(fields)
    assert "Untrusted reservation envelope" in promotion_source

    required = (
        "PromotionReservation caller envelope != trusted durable reservation row/state",
        "trusted durable state, database revision, persistence timestamps, "
        "server lock/fence material, and transaction ownership belong to a "
        "server-internal persistence representation",
        "Constructing or replaying that envelope does not create trusted "
        "reservation authority",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_reuses_exact_type_sensitive_canonical_intent_boundary():
    promotion_source = _read(PROMOTION)
    text = _normalized(CONTRACT)

    assert "canonical_memory_bytes" in promotion_source
    assert 'intent.model_dump(mode="json")' in promotion_source

    required = (
        'canonical_memory_bytes(intent.model_dump(mode="json"))',
        "stored digest/index alone != exact-intent authority",
        "stored JSON object equality != exact-intent authority",
        "exact canonical bytes must be durably retained or exactly reconstructable "
        "and re-compared",
        "a digest collision with different exact bytes fails closed",
        "true, 1, and 1.0 remain authority-significant",
        "no second JSON serializer/canonicalization algorithm is introduced",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_canonical_reservation_convergence():
    text = _normalized(CONTRACT)

    required = (
        "One exact canonical MemoryPromotionIntent converges to at most one durable "
        "promotion_authority_id",
        "use durable uniqueness to select one concurrency winner",
        "confirm every winner/conflict against exact canonical bytes",
        "on retry or restart, recover the existing winner rather than minting "
        "another authority ID",
        "digest/index is a lookup/concurrency aid, not a replacement authorization "
        "identity",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_proof_tuple_and_canonical_source_authority():
    source_source = _read(SOURCE_IDENTITY)
    text = _normalized(CONTRACT)

    assert "context_source_id" in source_source
    assert "authority_id must be a native logical authority" in source_source

    required = (
        "(source authority identity, proof_receipt_id, authority_state_token, "
        "MEMORY_PROMOTION scope) -> at most one exact canonical intent",
        "canonical context_source_id is an acceptable persisted key for that identity",
        "filesystem path, provider handle, object key, payload locator, UI handle",
        "cross-intent reuse of the same proof authority tuple a durable conflict",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_preserves_f5_3d_state_machine():
    prior = _normalized(F5_3D)
    text = _normalized(CONTRACT)

    for state in ("ISSUED", "CONSUMED", "REVOKED"):
        assert state in prior
        assert state in text

    required = (
        "CONSUMED cannot transition back to ISSUED",
        "REVOKED cannot transition back to ISSUED",
        "timestamp-only expiry is not reservation authority",
        "state is trusted server persistence state, not caller-supplied authorization",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_session_scoped_no_commit_repository_boundary():
    repository_source = _read(MEMORY_REPOSITORY)
    text = _normalized(CONTRACT)

    assert "AsyncSession" in repository_source
    assert "BEGIN IMMEDIATE" in repository_source
    assert "write intent before any read" in repository_source
    assert "DurableMemoryRecordRepository" in repository_source

    required = (
        "session-scoped and transaction-composable with the existing "
        "DurableMemoryRecordRepository in the same AsyncSession",
        "repository methods do not commit independently",
        "repository methods do not open a hidden second session or connection",
        "caller owns commit and rollback",
        "authoritative BEGIN IMMEDIATE / write intent -> before any reservation "
        "authority read",
        "reservation read/validation -> Memory admission in same AsyncSession -> "
        "reservation ISSUED -> CONSUMED -> one caller-owned commit",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_retry_recovery_and_fail_closed_collision():
    text = _normalized(CONTRACT)

    required = (
        "retry of the same exact canonical intent must recover the existing durable "
        "reservation and exact promotion_authority_id",
        "must not mint a new authority ID merely because the caller cannot distinguish "
        "timeout from successful commit",
        "stored digest/index material matches but exact bytes differ, fail closed as "
        "collision/corruption",
        "reconstruction cannot prove exact canonical intent or proof binding, the row "
        "is not usable as trusted reservation authority",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_persistence_failure_taxonomy():
    text = _normalized(CONTRACT)

    for failure in (
        "RESERVATION_NOT_FOUND",
        "RESERVATION_INTENT_MISMATCH",
        "RESERVATION_PROOF_REUSE_CONFLICT",
        "RESERVATION_ALREADY_CONSUMED",
        "RESERVATION_REVOKED",
        "RESERVATION_STATE_TRANSITION_CONFLICT",
        "RESERVATION_PERSISTENCE_UNAVAILABLE",
        "RESERVATION_PERSISTENCE_FAILURE",
        "RESERVATION_RECONSTRUCTION_CORRUPTION",
        "RESERVATION_CANONICAL_DIGEST_COLLISION",
    ):
        assert failure in text

    assert "adds no production exception classes" in text
    assert "HTTP status mapping" in text


def test_ctx_f5_3f_a_keeps_migration_lineage_unresolved():
    text = _normalized(CONTRACT)

    required = (
        "F5-3F-A migration revision = UNRESOLVED",
        "F5-3F-A down_revision = UNRESOLVED",
        "competing sibling CTX migration from current 21a = NOT RELEASED",
        "canonical Alembic head = 21a_ctx_f5_memory_foundation",
        "R12-B PR #117 candidate = 22a_r12_execution_lease_fence -> 21a",
        "do not create a sibling CTX revision from 21a",
        "resolve the exact canonical head at production CLAIM time",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_freezes_finite_future_ownership_without_releasing_it():
    text = _normalized(CONTRACT)

    required = (
        "one CTX reservation SQL model file",
        "one CTX reservation repository file",
        "one Alembic migration file after canonical lineage is resolved",
        "optional one narrowly scoped server-internal persistence representation",
        "reservation SQL model/schema/migration implementation = CLOSED",
        "reservation repository implementation = CLOSED",
        "atomic reservation-to-Memory runtime/orchestrator = CLOSED",
        "concrete supported source kinds = NONE",
        "CAS authority transfer = NONE",
        "R11/R12 execution authority transfer = NONE",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3f_a_preserves_landed_atomic_admission_boundary():
    prior = _normalized(F5_3E)
    text = _normalized(CONTRACT)

    assert "SQLite admission establishes write intent before read" in prior
    assert "authoritative BEGIN IMMEDIATE / write intent" in text
    assert "before any reservation authority read" in text
    assert "F5-3F-A does not wire the atomic admission orchestrator" in text


def test_ctx_f5_3f_a_evidence_is_future_compatible():
    text = _normalized(CONTRACT)

    required = (
        "must not require future reservation model/repository/migration/runtime files "
        "to remain absent forever",
        "later separately released implementation is allowed to add the production "
        "surfaces identified by this contract",
        "permanent authority boundaries on the untrusted caller envelope and exact "
        "canonicalization semantics",
        "production/schema/migration delta = ZERO",
    )
    for phrase in required:
        assert phrase in text
