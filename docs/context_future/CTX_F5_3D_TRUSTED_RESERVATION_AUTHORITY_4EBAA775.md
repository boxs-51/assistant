# CTX-F5-3D — Trusted Promotion-Reservation Authority Contract

Primary authority: Issue #15.  
Canonical policy: Issue #85 v2.

## Exact release lineage

```text
parent stage = CTX-F5-3C
parent PR = #101
parent exact FINAL-GREEN HEAD = 4ebaa775ec6ed779aaf2841da70489ff8ae7bc25
parent integration = Wave #102 / COMPLETE / LANDED / CANONICAL / HEALTHY
canonical integration base = main@6228734ae7a380719bb14fa520e3307c5330aa31
F5-3D class = CONTRACT / AUTHORITY FREEZE ONLY
production delta = ZERO
parent-first prerequisite = SATISFIED
candidate integration state = REFRESHED / AWAITING FRESH CI + INDEPENDENT FINAL GREEN
concrete supported source kinds = NONE
```

F5-3D freezes the trusted server reservation authority semantics that a later implementation must satisfy. It does not implement that authority and does not make Memory promotion executable.

Integration refresh note: parent PR #101 is now canonical through completed Wave #102, and this candidate has been retargeted onto exact `main@6228734ae7a380719bb14fa520e3307c5330aa31`. This metadata refresh does not change any F5-3D authority semantics.

## Critical operation separation

The following are distinct operations and authority boundaries:

```text
trusted reservation verification
!= atomic admission claim
!= durable Memory admission commit
!= reservation consumption
```

The current F5-3C consumer Protocol:

```python
PromotionReservationVerifier.verify(
    reservation: PromotionReservation,
    intent: MemoryPromotionIntent,
) -> None
```

is frozen as trusted state/equality verification only.

Verifier success may prove that trusted server state currently contains an eligible reservation issued for the exact canonical intent. It MUST NOT itself claim admission, commit a Memory record, or mutate the reservation to CONSUMED.

A future runtime must not treat:

```text
verify -> create/put Memory -> mark consumed
```

as a sufficient atomic protocol.

## 1. Server-owned durable authority

A reservation is trusted only when backed by server-owned durable authority.

- A caller-created `PromotionReservation` remains untrusted data.
- `promotion_authority_id` is opaque and server-issued.
- A future trusted issuer MUST durably commit the authoritative reservation before returning successful issuance.
- In-memory-only issuance cannot authorize a future durable Memory admission.
- Persistence implementation, repository implementation, schema and migration are not released by F5-3D.
- F5-3D introduces no trusted issuer or verifier implementation.

Structural validity remains distinct from trusted authorization.

## 2. Exact canonical intent identity

Trusted reservation identity binds the exact canonical full `MemoryPromotionIntent` material.

Exact intent equality is defined by type-sensitive canonical bytes under the existing `canonical_memory_bytes(...)` representation boundary.

Therefore:

- `true`, `1`, and `1.0` remain distinct canonical values;
- Python object/dict equality is insufficient for exact authority matching;
- an implementation may use a digest as an index, but a digest alone does not replace exact canonical-material confirmation;
- F5-3D introduces no second metadata or intent canonicalization.

The F5-3C exact-binding repair on parent HEAD `4ebaa775...` is the canonical parent surface for this contract.

## 3. One canonical reservation per exact intent

Freeze:

```text
one exact canonical MemoryPromotionIntent
    -> at most one canonical promotion_authority_id
```

Concurrent or retried `reserve(intent)` operations for the same exact canonical intent must converge on the same durable reservation identity.

This invariant is required because `promotion_authority_id` participates in existing `memory_id()` identity material. Minting multiple authority IDs for one exact intent would permit divergent durable Memory identities for one logical promotion decision.

This convergence requirement does not choose a storage implementation, locking primitive, transaction mechanism, or distributed coordination design.

## 4. Proof receipt/state-token single-intent binding

One exact source proof authority tuple:

```text
(source authority identity,
 proof_receipt_id,
 authority_state_token,
 MEMORY_PROMOTION scope)
```

may bind to at most one canonical promotion intent.

Rules:

- reuse for the same exact canonical intent may be idempotent;
- reuse across a different canonical intent is a trusted reservation conflict;
- proof receipt/state-token reuse conflict must fail closed;
- F5-3D does not make any concrete source kind promotable;
- source proof/liveness semantics remain owned by the source authority.

## 5. Logical reservation states

F5-3D freezes these logical states without adding production enums or persistence schema:

```text
ISSUED
CONSUMED
REVOKED
```

### ISSUED

Durable trusted reservation state exists and may satisfy verifier checks, subject to exact canonical intent, proof binding, source/admission gates, and future runtime rules.

### CONSUMED

The reservation has already participated in one successfully committed durable Memory admission and must not authorize another admission.

### REVOKED

The reservation authorization has been invalidated and cannot be resurrected as ISSUED by retry or caller replay.

Timestamp-only freshness or expiry is not an authority mechanism in F5-3D.

## 6. Trusted verifier semantics

A future trusted implementation of `PromotionReservationVerifier.verify(...)` may succeed only when all of the following are true:

1. durable trusted reservation state exists;
2. `promotion_authority_id` matches exactly;
3. full canonical `MemoryPromotionIntent` material matches exactly;
4. proof receipt/state-token binding matches exactly;
5. logical state is `ISSUED`;
6. the reservation is neither consumed nor revoked;
7. authority persistence is available and readable.

It fails closed for:

```text
unknown or missing reservation
foreign owner or foreign intent
canonical intent mismatch
proof reuse conflict
already consumed reservation
revoked reservation
authority unavailable
authority persistence failure
```

Verifier success is non-consuming. It does not mutate durable reservation state.

## 7. Issuance retry and crash semantics

If durable issuance commits but the response is lost, cancelled, or otherwise not observed by the caller, retrying the same exact canonical intent must recover or converge on the same canonical reservation identity.

A changed canonical intent after such a partial outcome is a conflict, not a retry.

A future implementation therefore needs restart-safe durable lookup/recovery semantics before issuance can be considered trusted. F5-3D freezes the behavior but does not choose the repository, transaction, id-generation, or recovery mechanism.

## 8. Atomic admission/consume boundary remains closed

F5-3D explicitly blocks release of a promotion orchestrator or durable admission handoff until a later independently audited stage freezes and implements an atomic/idempotent admission protocol.

That future protocol must prevent:

- two concurrent durable admissions from one reservation;
- crash after Memory commit but before reservation consumption;
- reservation consumption before Memory commit;
- retry creating a second Memory identity;
- replay after a committed admission.

A later design may use one transaction or a durable recoverable state machine. F5-3D deliberately chooses neither.

## 9. Source lifecycle and retention boundary

Reservation authority is not source lifecycle authority.

- Issue #74 / CAS retains ASSET, provider, lifecycle, deletion and physical-GC authority.
- Issue #31 / R11 retains transcript/checkpoint persistence, retention and destructive-GC authority.
- CTX reservation state must not keep source material alive by implication.
- Source current-read/liveness re-proof remains a separate source-authority responsibility.
- No source-specific invalidation hook is released by F5-3D.
- No concrete source kind is supported for production Memory promotion.

A durable reservation may preserve only the trusted authority facts needed for reservation semantics; it does not become a retention root for the source payload.

## 10. Operational failure taxonomy

F5-3D freezes these semantic trusted-authority failure categories:

```text
UNKNOWN_RESERVATION
RESERVATION_INTENT_CONFLICT
PROOF_REUSE_CONFLICT
RESERVATION_CONSUMED
RESERVATION_REVOKED
RESERVATION_AUTHORITY_UNAVAILABLE
RESERVATION_PERSISTENCE_FAILURE
```

These are distinct from F5-3C structural-integrity errors.

F5-3D does not add production exception classes, retry policy, HTTP status mapping, telemetry mapping, or recovery behavior for these categories.

## 11. F5-3D explicit non-scope

The following remain closed:

```text
production issuer implementation = CLOSED
production verifier implementation = CLOSED
reservation repository/store implementation = CLOSED
schema/Alembic migration = CLOSED
atomic admission/consume implementation = CLOSED
promotion orchestrator/service = CLOSED
create_memory_record()/MemoryRecordRepository.put() handoff = CLOSED
source-specific proof adapter = CLOSED
concrete supported source kinds = NONE
HTTP/public/model-callable promotion API = CLOSED
retrieval/ContextBuilder/model visibility = CLOSED
revocation/erasure/tombstone implementation = CLOSED
CAS/R11 authority transfer = NONE
F6/F7/F9/F10 authority = CLOSED
```

F5-3D does not alter `se/src/**` production code.

## 12. Parent surface frozen by this contract

F5-3D is defined against the exact F5-3C parent surface at `4ebaa775...`:

- `SourcePromotionProof`;
- `MemoryPromotionIntent`;
- `PromotionReservation`;
- `SourcePromotionAuthorityPort.reprove_for_memory_promotion(...)`;
- `PromotionReservationIssuer.reserve(...)`;
- `PromotionReservationVerifier.verify(...)`;
- type-sensitive canonical exact-binding helpers already repaired in F5-3C.

This document does not grant implementation authority to any of those Protocols.

## 13. Integration rule

F5-3D was developed as a stacked zero-production child of F5-3C. The parent-first prerequisite is now satisfied because PR #101 landed through completed Wave #102.

```text
development parent = PR #101 exact HEAD 4ebaa775...
parent canonical merge / integration main = 6228734ae7a380719bb14fa520e3307c5330aa31
current PR base = main
parent-first prerequisite = SATISFIED
candidate integration status = AWAITING FRESH EXACT-BASE CI + INDEPENDENT FINAL GREEN
```

The logical candidate delta remains exactly the two released zero-production F5-3D files. Fresh exact-base evidence and independent integration FINAL GREEN are required before merge eligibility is evaluated.

Issue #15's zero-production auto-merge policy may apply only after this refreshed integration gate is satisfied.

## Non-authority statement

F5-3D freezes future trusted reservation semantics only. It creates no trusted reservation, supports no source kind, claims no Memory admission, consumes no reservation, persists no Memory record, and exposes no model-visible Memory capability.
