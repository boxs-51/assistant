# CTX-F5-3C — Dormant Generic Memory Promotion Primitives

Primary authority: Issue #15.
Policy: Issue #85 v2.

## Exact release baseline

```text
canonical main = 9e746d9a3ad44305d56724815011114f25414f03
post-main Architecture #1346 = GREEN/GREEN
release = independent Issue #15 audit comment #5843863441
stage = CTX-F5-3C
class = PRODUCTION PRIMITIVES ONLY
concrete supported source kinds = NONE
production promotion orchestrator/runtime = CLOSED
```

F5-3C introduces only dormant generic values, consumer-side Protocols, and pure structural integrity validation. It does not make Memory promotion executable.

## Critical authority boundary

```text
structural/self-consistency integrity != trusted server authorization
```

A `SourcePromotionProof`, `MemoryPromotionIntent`, or `PromotionReservation` can be structurally valid while still carrying no trusted authorization. A `PromotionReservation` is an untrusted data envelope until a trusted verifier succeeds.

Structural validation does not authorize promotion. Only a future trusted `PromotionReservationVerifier` implementation backed by server authority may establish that trusted server state confirms issuance + exact intent match.

F5-3C contains no issuer implementation, verifier implementation, authorization registry, reservation repository, persistence schema, service, route, DI binding, or runtime invocation.

## Released primitive surface

### `MemoryPromotionProofScope`

Exactly one scope is released:

```text
MEMORY_PROMOTION = "MEMORY_PROMOTION"
```

A generic read/discovery proof is insufficient for Memory promotion.

### `SourcePromotionProof`

Immutable, extra-forbid value:

```text
source_ref_snapshot: ContextSourceRef
proof_receipt_id: str
authority_state_token: str
scope: MemoryPromotionProofScope = MEMORY_PROMOTION
```

The embedded `ContextSourceRef` remains the single binding for source kind, context_source_id, authority_id, authority_version, and owner_user_id. `proof_receipt_id` and `authority_state_token` are opaque normalized non-empty values. `authority_state_token` represents source-owner revision/state/order evidence, not timestamp freshness.

### `MemoryPromotionIntent`

Immutable, extra-forbid exact intent:

```text
owner_user_id: str
source_ref_snapshot: ContextSourceRef
source_proof: SourcePromotionProof
content_digest: str
metadata: canonical frozen JSON object
memory_schema_version: int >= 1
```

Integrity requires owner equality with the source snapshot, an exact source snapshot match between intent and proof, `MEMORY_PROMOTION` proof scope, a real positive integer schema version, and canonical Memory JSON semantics for metadata.

Metadata canonicality reuses existing `canonical_memory_bytes(...)`; F5-3C does not define a second JSON canonicalization or metadata digest.

### `PromotionReservation`

Immutable, extra-forbid envelope:

```text
promotion_authority_id: str
intent: MemoryPromotionIntent
```

`promotion_authority_id` alone is insufficient authorization. The envelope binds the complete exact intent but remains untrusted until a trusted future verifier confirms server issuance and exact intent match.

The reservation is not a second Memory identity and does not change the existing F5-2 `memory_id()` material:

```text
owner_user_id
context_source_id
promotion_authority_id
content_digest
memory_schema_version
```

## Released Protocol contracts

```python
class SourcePromotionAuthorityPort(Protocol):
    async def reprove_for_memory_promotion(
        self,
        *,
        source_ref: ContextSourceRef,
        owner_user_id: str,
    ) -> SourcePromotionProof: ...

class PromotionReservationIssuer(Protocol):
    async def reserve(
        self,
        *,
        intent: MemoryPromotionIntent,
    ) -> PromotionReservation: ...

class PromotionReservationVerifier(Protocol):
    async def verify(
        self,
        *,
        reservation: PromotionReservation,
        intent: MemoryPromotionIntent,
    ) -> None: ...
```

The source port is a CTX consumer contract only; source-owner implementations retain their own authority. Issuer/verifier implementation = CLOSED. Promotion orchestrator/service = CLOSED.

Cancellation, deadline, and runtime execution-context shapes are not frozen in this stage and must be re-audited with the first real I/O implementation.

## Pure integrity helpers

Stage-local helpers may establish only canonical shape, equality, and self-consistency:

```text
validate_source_promotion_proof_integrity(...)
validate_memory_promotion_intent_integrity(...)
validate_promotion_reservation_integrity(...)
validate_reservation_matches_intent(...)
```

`validate_reservation_matches_intent(...)` proves exact envelope equality only. It is not authorization and cannot substitute for `PromotionReservationVerifier.verify(...)`.

## Failure semantics

F5-3C activates only local structural failures:

```text
PromotionPrimitiveIntegrityError
SourcePromotionProofIntegrityError
MemoryPromotionIntentIntegrityError
PromotionReservationIntegrityError
PromotionReservationIntentMismatchError
```

They carry no retry, fallback, recovery, or source-selection authority.

Operational failure taxonomy from F5-3B remains future contract semantics only: unsupported source, source unavailable, unreadable/unauthorized source, stale/foreign proof, owner mismatch, missing/invalid reservation, trusted replay/conflict, F5-2 admission conflict, and persistence failure.

## CLOSED authority

```text
concrete supported source kinds = NONE
ASSET = UNSUPPORTED
AGENT_TRANSCRIPT / checkpoint = UNSUPPORTED
SESSION / TASK / BRANCH / TOOL_RESPONSE_PAYLOAD = UNSUPPORTED
source-specific adapters = CLOSED
issuer/verifier implementation = CLOSED
promotion orchestrator/service = CLOSED
HTTP/public API = CLOSED
automatic promotion = CLOSED
create_memory_record()/MemoryRecordRepository.put() handoff = CLOSED
schema/migration/repository persistence change = CLOSED
retrieval/ContextBuilder/model visibility = CLOSED
revocation/erasure/tombstone implementation = CLOSED
F6/F7/F9/F10 = CLOSED
CAS/R11 authority transfer = NONE
```

F5-3C must not import or call CAS asset/provider/ObjectStorage/FileProviderBinding paths and must not import or call R11 transcript/checkpoint/retention repositories.

## Cross-issue boundaries

Issue #74 / CAS retains ASSET current-read/liveness, FileAsset/FileBlob/FileReference, ObjectStorage, provider hydration, lifecycle, deletion, and GC authority. No ASSET -> Memory promotion is released.

Issue #31 / R11 retains transcript/checkpoint source read-liveness, persistence, retention, and destructive-GC authority. No AGENT_TRANSCRIPT/checkpoint -> Memory promotion is released.

SESSION/TASK/BRANCH/TOOL_RESPONSE_PAYLOAD require separate source-specific proof releases before becoming promotable.

## Candidate file boundary

Released files are exactly:

```text
se/src/context/memory_promotion.py
se/tests/unit/test_context_memory_promotion.py
se/tests/architecture/test_ctx_f5_3c_promotion_primitives.py
docs/context_future/CTX_F5_3C_PROMOTION_PRIMITIVES_9E746D9A.md
```

No existing `se/src/**` file is changed by this slice.

## Integration and merge gate

This is a production-code slice. Standing contract/evidence auto-merge authority does not apply.

Before integration, the exact candidate requires fresh Linux + Windows Architecture, independent FINAL GREEN, scope/dependency/drift revalidation, and explicit production merge authorization or an explicitly authorized future Integration Wave containing the frozen candidate.

Issue #15 has no member in Wave #99 at release time. Any F5-3C integration candidate belongs to a future Integration Wave unless the active coordinator is explicitly amended before authorization.

The dormant boundary is stage-local and may be superseded by a later independently released stage. This document does not encode a permanent global absence invariant against future promotion runtime implementation.

## Non-authority statement

F5-3C creates no trusted reservation by construction, supports no source kind by construction, persists no Memory record, and grants no model-visible Memory capability.