from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3A_PROMOTION_AUTHORITY_FREEZE_912CF1AC.md"
)


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_ctx_f5_3a_freeze_is_contract_only_and_keeps_runtime_closed():
    text = _contract()

    required = (
        "mode                         = CONTRACT / AUTHORITY FREEZE ONLY",
        "production promotion runtime = NOT RELEASED",
        "source-specific adapters     = NOT RELEASED",
        "automatic promotion          = CLOSED",
        "It adds no production service, endpoint, adapter, repository, schema, migration",
        "A production runtime, source adapter, schema, repository, or wiring change",
        "requires a separate production release and merge authorization",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3a_freezes_source_claim_proof_and_promotion_authority_separation():
    text = _contract()

    required = (
        "source authority",
        "!= ContextSourceRef projection",
        "!= Memory promotion authority",
        "!= durable Memory persistence",
        "!= Memory retrieval authorization",
        "!= model Working Set visibility",
        "A ContextSourceRef is an immutable source identity/provenance claim",
        "not current source read authority",
        "server-owned promotion_authority_id",
        "Client-selected owner ids never become promotion authority",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3a_freezes_source_reproof_and_stale_proof_failure():
    text = _contract()

    required = (
        "source_kind",
        "context_source_id",
        "authority_id",
        "authority_version / source revision where applicable",
        "owner_user_id",
        "current read/liveness authorization scope",
        "proof revision / state token / receipt identity",
        "ordering evidence sufficient for stale-proof rejection",
        "A successful source proof is not an evergreen authorization token",
        "fail closed when deterministic stale-proof rejection cannot be established",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3a_keeps_cross_track_authority_external():
    text = _contract()

    required = (
        "### ASSET / CAS",
        "CAS remains owner of:",
        "ObjectStorage",
        "provider hydration/upload",
        "FileProviderBinding",
        "asset lifecycle, deletion, reconciliation, and physical GC",
        "### AGENT_TRANSCRIPT and other R11-owned durable execution evidence",
        "R11 remains owner of:",
        "retention policy",
        "destructive-GC authority for R11-owned evidence",
        "Memory provenance must not turn a source into a retention root",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3a_reuses_f5_2_instead_of_defining_a_second_memory_authority():
    text = _contract()

    required = (
        "F5-3 must not create a second Memory persistence or replay path",
        "reuse the existing canonical content digest",
        "reuse the existing deterministic Memory identity",
        "perform exactly one F5-2 durable admission/replay operation",
        "No alternate repository, alternate Memory identity, or alternate idempotency",
        "authority is released",
    )

    for phrase in required:
        assert phrase in text


def test_ctx_f5_3a_does_not_encode_a_future_global_absence_invariant():
    text = _contract()

    assert (
        "no global historical invariant is introduced that would prohibit a later"
        in text
    )
    assert "se/src/context/memory_promotion.py must not exist" not in text
    assert "Memory promotion implementation may never exist" not in text
