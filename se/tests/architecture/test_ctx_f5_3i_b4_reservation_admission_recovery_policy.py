from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B4_RESERVATION_ADMISSION_RECOVERY_POLICY.md"
)
B2_SERVICE = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_source_authority.py"
)
B3_ORCHESTRATION = Path(
    "se/src/infrastructure/storage/services/"
    "tool_response_payload_promotion_orchestration.py"
)
RESERVATION_REPOSITORY = Path(
    "se/src/infrastructure/storage/repositories/promotion_reservation.py"
)
RESERVATION_ISSUER = Path(
    "se/src/infrastructure/storage/services/promotion_reservation_issuer.py"
)
ADMISSION = Path(
    "se/src/infrastructure/storage/services/memory_promotion_admission.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic_contract() -> str:
    return " ".join(_read(CONTRACT).replace(chr(96), "").split())


def _class_node(source: str, name: str) -> ast.ClassDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function_node(
    class_node: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_b4_contract_records_parent_health_and_keeps_production_closed() -> None:
    contract = _semantic_contract()

    for phrase in (
        "stage = CTX-F5-3I-B4",
        "class = CONTRACT / ARCHITECTURE EVIDENCE ONLY",
        "exact development baseline = bb5fc2b06b34dd4838805eff5f72c2d14e74cc68",
        "parent CTX-F5-3I-B3 = LANDED / CANONICAL / HEALTHY",
        "post-merge Architecture #1901 / 37088891913 = GREEN/GREEN",
        "IW-2026-10-03-01 = COMPLETE / authorization consumed",
        "production PRE-CLAIM = HOLD pending independent B4 audit",
        "production CLAIM = NONE",
        "Memory admission invocation = CLOSED",
        "runtime/container/API wiring = CLOSED",
        "durable content persistence = CLOSED",
        "retention/pinning/GC = CLOSED",
        "production/runtime/schema/migration delta = ZERO",
    ):
        assert phrase in contract


def test_b3_returns_durable_reservation_plus_same_attempt_transient_material() -> None:
    source = _read(B3_ORCHESTRATION)

    for phrase in (
        "class TrustedToolResponsePromotionReservation:",
        "reservation: PromotionReservation",
        "trusted_material: TrustedToolResponsePromotionMaterial",
        "def content_snapshot(self) -> Any:",
        "return self.trusted_material.content_snapshot",
        "class DurableToolResponsePayloadPromotionOrchestration:",
    ):
        assert phrase in source

    cls = _class_node(source, "DurableToolResponsePayloadPromotionOrchestration")
    reserve = _function_node(cls, "reserve")
    assert isinstance(reserve, ast.AsyncFunctionDef)
    assert [arg.arg for arg in reserve.args.kwonlyargs] == [
        "source_ref",
        "owner_user_id",
    ]

    awaited_calls: list[str] = []
    for node in ast.walk(reserve):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        if isinstance(function, ast.Attribute):
            awaited_calls.append(function.attr)
        elif isinstance(function, ast.Name):
            awaited_calls.append(function.id)

    assert awaited_calls.count("read_trusted_promotion_material") == 1
    assert awaited_calls.count("reserve") == 1
    assert len(awaited_calls) == 2
    assert "TrustedToolResponsePromotionReservation(" in source


def test_b2_proof_and_authority_state_are_deterministic_domain_hashes() -> None:
    source = _read(B2_SERVICE)

    for phrase in (
        '_AUTHORITY_STATE_DOMAIN = "ctx-trp-source-state-v1"',
        '_PROOF_RECEIPT_DOMAIN = "ctx-trp-proof-receipt-v1"',
        "def _domain_hash(domain: str, material: object) -> str:",
        "hashlib.sha256(",
        '+ b"\\x00"',
        "+ canonical_payload_bytes(material)",
        "authority_state_token = _domain_hash(",
        "_AUTHORITY_STATE_DOMAIN,",
        "proof_receipt_id = _domain_hash(",
        "_PROOF_RECEIPT_DOMAIN,",
        '"authority_state_token": authority_state_token',
    ):
        assert phrase in source

    for forbidden in ("uuid.uuid4", "secrets.", "random.", "time.time("):
        assert forbidden not in source


def test_durable_reservation_reconstructs_exact_authority_without_content() -> None:
    source = _read(RESERVATION_REPOSITORY)
    record = _class_node(source, "DurablePromotionReservationRecord")

    field_names = {
        node.target.id
        for node in record.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert field_names == {
        "promotion_authority_id",
        "intent",
        "intent_digest",
        "source_context_source_id",
        "proof_receipt_id",
        "authority_state_token",
        "proof_scope",
        "state",
    }
    assert "content" not in field_names
    assert "content_snapshot" not in field_names

    for phrase in (
        '"intent_json": payload',
        '"intent_canonical_bytes": canonical_intent',
        "intent = MemoryPromotionIntent.model_validate(intent_payload)",
        "reconstructed = _canonical_intent_bytes(intent)",
        "if reconstructed != stored_canonical:",
        "if stored_digest != reconstructed_digest:",
        "if stored_source_id != source_id:",
        "if stored_receipt != receipt_id:",
        "if stored_token != state_token:",
        "return DurablePromotionReservationRecord(",
    ):
        assert phrase in source


def test_admission_still_requires_explicit_content_and_exact_digest_match() -> None:
    source = _read(ADMISSION)
    cls = _class_node(source, "DurableMemoryPromotionAdmission")
    admit = _function_node(cls, "admit")

    assert isinstance(admit, ast.AsyncFunctionDef)
    assert [arg.arg for arg in admit.args.kwonlyargs] == [
        "reservation",
        "content",
    ]

    for phrase in (
        "canonical_payload_bytes = canonical_memory_bytes(content)",
        "content_snapshot = json.loads(canonical_payload_bytes.decode",
        "payload_digest = memory_content_digest(content_snapshot)",
        "durable = await reservation_repository.get(",
        "if payload_digest != durable.intent.content_digest:",
        "promotion content digest does not match durable intent",
    ):
        assert phrase in source


def test_b4_freezes_source_backed_retry_and_fail_closed_source_loss() -> None:
    contract = _semantic_contract()

    for phrase in (
        "every cross-process retry starts from a fresh B2 source re-proof",
        "the reconstructed narrow server intent must be exact",
        "recovery must first resolve an exact durable winner by trusted canonical intent/proof authority",
        "no durable winner -> only the existing issuance path may call the issuer",
        "ISSUED winner -> recover its exact PromotionReservation envelope",
        "CONSUMED winner -> recover its exact PromotionReservation envelope without calling the issuer",
        "REVOKED winner -> fail closed",
        "if the source remains live, recovery may continue using fresh transient content from that same B2 re-proof",
        "if the source is unavailable/rejected after process loss, recovery fails closed",
        "fail-closed recovery does not synthesize content",
        "fail-closed recovery does not create a replacement reservation",
        "CTX does not pin/extend Agent/R11/R12 source retention",
        "no durable content copy is added to reservation rows",
        "This policy intentionally chooses safety over guaranteed liveness after source deletion",
    ):
        assert phrase in contract


def test_b4_keeps_open_fence_and_lifecycle_authority_external() -> None:
    contract = _semantic_contract()

    for phrase in (
        "FUTURE-FENCE-CTX-B3-RESERVATION-TO-ADMISSION-CONTENT-RECOVERY-1",
        "B4 itself does not close the fence by owner assertion",
        "A stranded ISSUED reservation is a liveness/lifecycle condition",
        "Reservation lifecycle cleanup/revocation policy remains separately owned",
        "adding content/content_snapshot to promotion reservation rows",
        "pinning Agent tool-result rows or changing R11/R12 retention",
        "auto-revoking an ISSUED reservation because source re-proof failed",
        "invoking DurableMemoryPromotionAdmission.admit(...)",
        "adding runtime/container/API wiring",
        "changing CAS/UBQ/R11/R12/#156 authority",
    ):
        assert phrase in contract


def test_b4_contract_candidate_is_exactly_two_zero_production_files() -> None:
    contract = _semantic_contract()

    for phrase in (
        "CTX-F5-3I-B4 contract candidate is exactly two files",
        "docs/context_future/CTX_F5_3I_B4_RESERVATION_ADMISSION_RECOVERY_POLICY.md",
        "se/tests/architecture/test_ctx_f5_3i_b4_reservation_admission_recovery_policy.py",
        "No se/src/** production file changes are part of this candidate",
        "production reservation-to-admission PRE-CLAIM = SEPARATE / REQUIRED",
        "production CLAIM = NONE",
        "runtime/container/API authority = CLOSED",
        "durable content persistence = CLOSED unless separately released",
        "retention/pinning/GC authority = CLOSED",
        "production merge authority = NONE",
    ):
        assert phrase in contract


def test_issuer_converges_only_issued_and_rejects_terminal_states() -> None:
    source = _read(RESERVATION_ISSUER)

    for phrase in (
        "candidate_authority_id = self._authority_id_factory()",
        "winner = await repository.insert_or_converge_issued_candidate(",
        "if winner.state is DurablePromotionReservationState.CONSUMED:",
        "raise PromotionReservationAlreadyConsumedError(",
        "if winner.state is DurablePromotionReservationState.REVOKED:",
        "raise PromotionReservationRevokedError(",
        "if winner.state is not DurablePromotionReservationState.ISSUED:",
        "return PromotionReservation(",
        "promotion_authority_id=winner.promotion_authority_id",
        "intent=winner.intent",
    ):
        assert phrase in source


def test_repository_has_non_minting_exact_intent_and_proof_resolution_primitives() -> None:
    source = _read(RESERVATION_REPOSITORY)
    cls = _class_node(source, "DurablePromotionReservationRepository")
    get_by_intent = _function_node(cls, "get_by_intent")
    get_by_proof = _function_node(cls, "get_by_proof_authority")

    assert isinstance(get_by_intent, ast.AsyncFunctionDef)
    assert isinstance(get_by_proof, ast.AsyncFunctionDef)

    for phrase in (
        "canonical_intent = _canonical_intent_bytes(intent)",
        "winner = await self._get_by_digest(digest)",
        "winner_canonical = _canonical_intent_bytes(winner.intent)",
        "if winner_canonical != canonical_intent:",
        "raise PromotionReservationCanonicalDigestCollisionError(",
        "source_id, receipt_id, state_token, scope = _proof_tuple_from_intent(intent)",
        "return await self._get_by_proof_tuple(",
    ):
        assert phrase in source


def test_admission_consumed_replay_requires_exact_envelope_and_content() -> None:
    source = _read(ADMISSION)

    for phrase in (
        "validate_promotion_reservation_integrity(reservation)",
        "durable = await reservation_repository.get(",
        "validate_reservation_matches_intent(",
        "if payload_digest != durable.intent.content_digest:",
        "if durable.state is DurablePromotionReservationState.CONSUMED:",
        "existing = (",
        "await memory_repository.get_by_promotion_authority(",
        "memory_records_replay_equivalent(",
        "return existing",
    ):
        assert phrase in source


def test_b4_freezes_terminal_state_aware_non_minting_recovery_split() -> None:
    contract = _semantic_contract()

    for phrase in (
        "issuance recovery = fresh B2 re-proof -> exact intent -> issuer create/converge only while ISSUED",
        "admission replay recovery = fresh B2 re-proof -> exact intent -> trusted non-minting exact-reservation resolution",
        "durable CONSUMED winner -> do NOT call issuer",
        "durable REVOKED winner -> FAIL CLOSED",
        "The resolver must mint no promotion_authority_id",
        "must not accept a caller-supplied promotion_authority_id as authority",
        "if no durable winner exists, only the issuance path may call the issuer",
        "CONSUMED winner -> recover its exact PromotionReservation envelope without calling the issuer",
        "weakening DurablePromotionReservationIssuer so CONSUMED or REVOKED becomes issuable/reusable",
        "caller promotion_authority_id/content alone never becomes recovery authority",
    ):
        assert phrase in contract


def test_b4_records_p1_repair_without_claiming_independent_closure() -> None:
    contract = _semantic_contract()

    for phrase in (
        "P1-CTX-F5-3I-B4-CONSUMED-REPLAY-RESERVATION-RECOVERY-1",
        "is repaired by this contract candidate",
        "independent replacement-head audit remains required before closure",
        "production PRE-CLAIM = HOLD pending independent B4 audit",
        "production CLAIM = NONE",
        "production merge authority = NONE",
    ):
        assert phrase in contract
