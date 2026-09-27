# CTX-F5-3F-A — Durable Reservation Persistence Contract Freeze

Primary authority: Issue #15 / CTX.  
Canonical coordination policy: Issue #85 v2.5.

## Exact claim lineage

```text
release baseline = main@540900f950f4e7826a3a24f4a2e657c175ce818b
claim baseline = main@20019937c7683da8fe87d2fa8ddd25e4843381c7
release -> claim drift = web-tool / web-researcher / tests / benchmarks only
CTX Memory/promotion/repository drift = NONE
canonical Alembic head at claim = 21a_ctx_f5_memory_foundation
R12-B candidate migration = 22a_r12_execution_lease_fence -> 21a_ctx_f5_memory_foundation
F5-3F-A class = CONTRACT / ARCHITECTURE-EVIDENCE ONLY
production/schema/migration delta = ZERO
```

F5-3F-A freezes the persistence contract required by landed F5-3D and F5-3E without implementing trusted reservation persistence.

## 1. Caller envelope is not trusted durable state

The current public/untrusted `PromotionReservation` remains a caller envelope:

```text
PromotionReservation caller envelope
!=
trusted durable reservation row/state
```

The caller envelope carries the requested `promotion_authority_id` and exact `MemoryPromotionIntent`. Constructing or replaying that envelope does not create trusted reservation authority.

Trusted mutable persistence facts must not be added to the caller envelope merely for storage convenience. In particular, trusted durable state, database revision, persistence timestamps, server lock/fence material, and transaction ownership belong to a server-internal persistence representation.

If a later production slice introduces a server-internal domain persistence representation, it is not authorization merely because equivalent values can be constructed by a caller.

## 2. Exact canonical intent material is the authority comparison

Durable reservation authority binds the exact type-sensitive bytes produced by the existing Memory canonicalization boundary:

```python
canonical_memory_bytes(intent.model_dump(mode="json"))
```

The following are frozen:

```text
stored digest/index alone != exact-intent authority
stored JSON object equality != exact-intent authority
exact canonical bytes must be durably retained or exactly reconstructable and re-compared
```

A digest may be persisted and indexed for lookup/concurrency convergence only when all of these remain true:

- exact canonical intent bytes are also retained or losslessly reconstructable;
- every digest hit or uniqueness conflict is confirmed against exact canonical bytes;
- a digest collision with different exact bytes fails closed;
- a digest collision never aliases two intents;
- no second JSON serializer/canonicalization algorithm is introduced.

JSON scalar distinctions such as `true`, `1`, and `1.0` remain authority-significant.

## 3. Canonical reservation convergence

One exact canonical `MemoryPromotionIntent` converges to at most one durable `promotion_authority_id`.

A future persistence design must provide a database-enforceable convergence strategy. The expected strategy is:

1. derive an exact-intent digest/index from the existing canonical intent bytes;
2. use durable uniqueness to select one concurrency winner;
3. confirm every winner/conflict against exact canonical bytes;
4. keep `promotion_authority_id` unique as durable reservation identity;
5. on retry or restart, recover the existing winner rather than minting another authority ID.

The digest/index is a lookup/concurrency aid, not a replacement authorization identity.

## 4. Proof authority tuple single-intent binding

The durable proof binding remains:

```text
(source authority identity,
 proof_receipt_id,
 authority_state_token,
 MEMORY_PROMOTION scope)
-> at most one exact canonical intent
```

For the current CTX model, source authority identity is bound to the canonical `ContextSourceRef` authority identity. The canonical `context_source_id` is an acceptable persisted key for that identity.

A local filesystem path, provider handle, object key, payload locator, UI handle, or other transport/storage locator is not source authority identity.

A future schema/repository must make cross-intent reuse of the same proof authority tuple a durable conflict. Same-tuple reuse for the same exact canonical intent may be idempotent.

## 5. Logical durable reservation state

Trusted durable reservation state is limited to:

```text
ISSUED
CONSUMED
REVOKED
```

Frozen state rules:

- `ISSUED` is the only state eligible for first admission;
- `CONSUMED` cannot transition back to `ISSUED`;
- `REVOKED` cannot transition back to `ISSUED`;
- retry cannot mint a new reservation to bypass `CONSUMED` or `REVOKED`;
- timestamp-only expiry is not reservation authority;
- state is trusted server persistence state, not caller-supplied authorization.

F5-3F-A does not release any additional logical state.

## 6. Session-scoped repository and transaction authority

The future reservation repository must be session-scoped and transaction-composable with the existing `DurableMemoryRecordRepository` in the same `AsyncSession`.

Required repository boundary:

- repository methods do not commit independently;
- repository methods do not open a hidden second session or connection;
- caller owns commit and rollback;
- reservation state changes are visible within the caller-owned transaction;
- repository cancellation/failure leaves the caller transaction rollback-capable;
- no second idempotency, lock, or transaction authority is introduced.

When reservation persistence participates in Memory admission on SQLite:

```text
authoritative BEGIN IMMEDIATE / write intent
-> before any reservation authority read
-> reservation read/validation
-> Memory admission in same AsyncSession
-> reservation ISSUED -> CONSUMED
-> one caller-owned commit
```

Other SQL backends require equivalent one-transaction serialization/CAS semantics.

F5-3F-A does not wire the atomic admission orchestrator.

## 7. Required persistence lookup and conditional transition primitives

A future repository must be able to support, without committing by itself:

- lookup by `promotion_authority_id`;
- lookup/recovery by exact-intent digest followed by exact-byte confirmation;
- lookup/conflict detection by proof authority tuple;
- creation/convergence of one canonical reservation winner;
- conditional trusted state transition with expected-current-state semantics;
- reconstruction that revalidates exact canonical material.

The exact production method names are deliberately not frozen here. Later implementation may choose a minimal API that satisfies these capabilities without creating parallel authority.

## 8. Retry, crash, and ambiguity semantics

If durable reservation issuance commits but its response is lost, retry of the same exact canonical intent must recover the existing durable reservation and exact `promotion_authority_id`.

A retry must not mint a new authority ID merely because the caller cannot distinguish timeout from successful commit.

If stored digest/index material matches but exact bytes differ, fail closed as collision/corruption rather than selecting the row as an authority winner.

If reconstruction cannot prove exact canonical intent or proof binding, the row is not usable as trusted reservation authority.

## 9. Persistence-layer conflict and failure taxonomy

F5-3F-A freezes semantic persistence categories at least equivalent to:

```text
RESERVATION_NOT_FOUND
RESERVATION_INTENT_MISMATCH
RESERVATION_PROOF_REUSE_CONFLICT
RESERVATION_ALREADY_CONSUMED
RESERVATION_REVOKED
RESERVATION_STATE_TRANSITION_CONFLICT
RESERVATION_PERSISTENCE_UNAVAILABLE
RESERVATION_PERSISTENCE_FAILURE
RESERVATION_RECONSTRUCTION_CORRUPTION
RESERVATION_CANONICAL_DIGEST_COLLISION
```

These are persistence/authority semantics only. F5-3F-A adds no production exception classes, retry framework, HTTP status mapping, model-visible errors, or telemetry contract.

## 10. Finite future production ownership matrix

F5-3F-A identifies but does not release the expected future production surface:

```text
one CTX reservation SQL model file
one CTX reservation repository file
one Alembic migration file after canonical lineage is resolved
optional one narrowly scoped server-internal persistence representation if independently justified
dedicated unit/integration/architecture evidence
```

Any need to change Memory admission runtime, promotion orchestrator, public API, source adapters, CAS, R11/R12 execution surfaces, ContextBuilder/retrieval, or concrete source support requires a separate release.

## 11. Migration lineage intentionally unresolved

F5-3F-A freezes no migration revision and no `down_revision`.

```text
F5-3F-A migration revision = UNRESOLVED
F5-3F-A down_revision = UNRESOLVED
competing sibling CTX migration from current 21a = NOT RELEASED
```

Current coordination fact at claim time:

```text
canonical Alembic head = 21a_ctx_f5_memory_foundation
R12-B PR #117 candidate = 22a_r12_execution_lease_fence -> 21a
R12-B is not canonical yet
```

Production CLAIM rule:

- if R12-B #117/22a lands first, resolve the CTX migration from the then-current canonical head, expected to be 22a unless another migration lands;
- if #117 is replaced, abandoned, or another migration lands, resolve the exact canonical head at production CLAIM time;
- do not create a sibling CTX revision from 21a while the R12-B lineage is still under integration/review.

This contract intentionally avoids creating migration drift.

## 12. Authority fences remain closed

The following remain CLOSED:

```text
reservation SQL model/schema/migration implementation = CLOSED
reservation repository implementation = CLOSED
trusted PromotionReservationIssuer wiring = CLOSED
trusted PromotionReservationVerifier wiring = CLOSED
atomic reservation-to-Memory runtime/orchestrator = CLOSED
production MemoryRecordRepository modification = CLOSED
HTTP/public/model-callable promotion API = CLOSED
source-specific proof adapters = CLOSED
concrete supported source kinds = NONE
retrieval/ContextBuilder/model visibility = CLOSED
revocation/erasure/tombstone runtime = CLOSED
CAS authority transfer = NONE
R11/R12 execution authority transfer = NONE
F6/F7/F9/F10 authority = CLOSED
```

## 13. Future-compatible evidence rule

Architecture evidence for F5-3F-A positively binds current canonical facts and this contract. It must not require future reservation model/repository/migration/runtime files to remain absent forever.

A later separately released implementation is allowed to add the production surfaces identified by this contract without invalidating F5-3F-A merely because those surfaces now exist.

Evidence may continue to enforce permanent authority boundaries on the untrusted caller envelope and exact canonicalization semantics.

## Integration rule

F5-3F-A is zero-production only. Integration requires:

- exactly the released docs/tests contract candidate;
- fresh Linux + Windows Architecture GREEN/GREEN;
- no unresolved blocking review threads;
- independent FINAL GREEN;
- Policy #85 v2.5 classification of any later canonical drift.

After FINAL GREEN, normal zero-production coordination/Integration Wave rules apply.

## Non-authority statement

F5-3F-A specifies durable reservation persistence requirements only. It creates no trusted database state, creates no migration, implements no repository, verifies no caller reservation, consumes no reservation, admits no Memory record, enables no source kind, and exposes no public or model-visible promotion behavior.
