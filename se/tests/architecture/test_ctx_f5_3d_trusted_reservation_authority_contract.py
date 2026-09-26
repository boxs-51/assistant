import ast
from pathlib import Path


MODULE = Path("se/src/context/memory_promotion.py")
CONTRACT = Path(
    "docs/context_future/CTX_F5_3D_TRUSTED_RESERVATION_AUTHORITY_4EBAA775.md"
)


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _normalized_contract() -> str:
    return " ".join(_contract().split())


def _protocol_methods(name: str) -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(_source())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                child.name: child
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef)
            }
    raise AssertionError(f"class {name} not found")


def test_ctx_f5_3d_freezes_operation_separation_and_atomicity_blocker():
    text = _normalized_contract()

    required = (
        "trusted reservation verification != atomic admission claim "
        "!= durable Memory admission commit != reservation consumption",
        "Verifier success is non-consuming",
        "two concurrent durable admissions from one reservation",
        "crash after Memory commit but before reservation consumption",
        "reservation consumption before Memory commit",
        "retry creating a second Memory identity",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_server_owned_durable_authority_and_exact_identity():
    text = _normalized_contract()

    required = (
        "server-owned durable authority",
        "caller-created PromotionReservation remains untrusted data",
        "promotion_authority_id is opaque and server-issued",
        "durably commit the authoritative reservation before returning successful issuance",
        "canonical_memory_bytes(...)",
        "true, 1, and 1.0 remain distinct canonical values",
        "a digest alone does not replace exact canonical-material confirmation",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_one_reservation_and_single_intent_proof_binding():
    text = _normalized_contract()

    required = (
        "one exact canonical MemoryPromotionIntent "
        "-> at most one canonical promotion_authority_id",
        "must converge on the same durable reservation identity",
        "may bind to at most one canonical promotion intent",
        "reuse for the same exact canonical intent may be idempotent",
        "reuse across a different canonical intent is a trusted reservation conflict",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_logical_states_and_trusted_verifier_requirements():
    text = _normalized_contract()

    for state in ("ISSUED", "CONSUMED", "REVOKED"):
        assert state in text

    required = (
        "durable trusted reservation state exists",
        "promotion_authority_id matches exactly",
        "full canonical MemoryPromotionIntent material matches exactly",
        "proof receipt/state-token binding matches exactly",
        "logical state is ISSUED",
        "the reservation is neither consumed nor revoked",
        "authority persistence is available and readable",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_issuance_retry_and_crash_convergence():
    text = _normalized_contract()

    required = (
        "retrying the same exact canonical intent must recover or converge "
        "on the same canonical reservation identity",
        "A changed canonical intent after such a partial outcome is a conflict, not a retry",
        "restart-safe durable lookup/recovery semantics",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_source_lifecycle_and_cross_track_boundaries():
    text = _normalized_contract()

    required = (
        "Issue #74 / CAS retains ASSET, provider, lifecycle, deletion "
        "and physical-GC authority",
        "Issue #31 / R11 retains transcript/checkpoint persistence, retention "
        "and destructive-GC authority",
        "CTX reservation state must not keep source material alive by implication",
        "Source current-read/liveness re-proof remains a separate source-authority responsibility",
        "No concrete source kind is supported for production Memory promotion",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_freezes_operational_failure_taxonomy():
    text = _normalized_contract()

    required = (
        "UNKNOWN_RESERVATION",
        "RESERVATION_INTENT_CONFLICT",
        "PROOF_REUSE_CONFLICT",
        "RESERVATION_CONSUMED",
        "RESERVATION_REVOKED",
        "RESERVATION_AUTHORITY_UNAVAILABLE",
        "RESERVATION_PERSISTENCE_FAILURE",
        "distinct from F5-3C structural-integrity errors",
    )
    for phrase in required:
        assert phrase in text


def test_ctx_f5_3d_binds_current_parent_protocol_surface_without_implementing_it():
    verifier_methods = _protocol_methods("PromotionReservationVerifier")
    issuer_methods = _protocol_methods("PromotionReservationIssuer")
    source_methods = _protocol_methods("SourcePromotionAuthorityPort")

    assert set(verifier_methods) == {"verify"}
    assert set(issuer_methods) == {"reserve"}
    assert set(source_methods) == {"reprove_for_memory_promotion"}

    verify = verifier_methods["verify"]
    reserve = issuer_methods["reserve"]
    reprove = source_methods["reprove_for_memory_promotion"]

    assert [arg.arg for arg in verify.args.args + verify.args.kwonlyargs] == [
        "self",
        "reservation",
        "intent",
    ]
    assert [arg.arg for arg in reserve.args.args + reserve.args.kwonlyargs] == [
        "self",
        "intent",
    ]
    assert [arg.arg for arg in reprove.args.args + reprove.args.kwonlyargs] == [
        "self",
        "source_ref",
        "owner_user_id",
    ]

    text = _normalized_contract()
    assert "This document does not grant implementation authority" in text
    assert "production delta = ZERO" in text


def test_ctx_f5_3d_records_parent_first_integration_and_zero_production_scope():
    text = _normalized_contract()

    required = (
        "parent exact FINAL-GREEN HEAD = "
        "4ebaa775ec6ed779aaf2841da70489ff8ae7bc25",
        "integration status = NOT_CANONICAL",
        "merge order = #101 first",
        "F5-3D does not alter se/src/** production code",
        "Issue #15's zero-production auto-merge policy may apply only after "
        "that parent-first integration boundary is satisfied",
    )
    for phrase in required:
        assert phrase in text
