# CTX-F5-3G-A — Trusted PromotionReservationVerifier

## Authority

CTX-F5-3G-A is a production, zero-migration, non-consuming authority-adapter stage released by Issue #15 under Policy #85 v2.5.

Its finite objective is to implement the landed `PromotionReservationVerifier.verify(...)` contract against canonical durable promotion-reservation persistence.

Production scope is exactly:

`se/src/infrastructure/storage/services/promotion_reservation_verifier.py`

Production merge authority is **NONE** until the normal exact-head CI, independent audit, review, and integration-wave gates complete.

## Trusted verification sequence

The verifier performs exactly:

```text
validate_reservation_matches_intent(reservation, intent)
-> repository.get(reservation.promotion_authority_id)
-> require exact durable row exists
-> compare durable reconstructed intent to supplied intent with canonical_memory_bytes(...)
-> require durable state == ISSUED
-> return None
```

The caller `PromotionReservation` remains an untrusted envelope. Structural validity or exact intent equality in the caller envelope is not durable authorization.

The caller-provided `promotion_authority_id` is the only durable identity that may be read for authorization. The verifier does not substitute an equivalent reservation discovered by intent digest, proof tuple, or another authority id.

## Failure surface

The verifier reuses existing canonical failures:

- malformed reservation: `PromotionReservationIntegrityError`;
- malformed supplied intent: `MemoryPromotionIntentIntegrityError`;
- caller reservation/supplied-intent mismatch: `PromotionReservationIntentMismatchError`;
- missing exact durable authority id: `PromotionReservationNotFoundError`;
- durable exact intent mismatch: `PromotionReservationExactIntentMismatchError`;
- durable state CONSUMED: `PromotionReservationAlreadyConsumedError`;
- durable state REVOKED: `PromotionReservationRevokedError`;
- persistence unavailable: propagate `PromotionReservationPersistenceUnavailableError`;
- durable reconstruction corruption: propagate `PromotionReservationReconstructionCorruptionError`.

No HTTP, public API, or model-visible error mapping is introduced.

## Transaction boundary

The verifier receives an already-created `DurablePromotionReservationRepository`, which is already bound to the caller-owned `AsyncSession`.

The verifier:

- creates no session or sessionmaker;
- opens no hidden transaction;
- never commits or rolls back;
- never inserts or converges reservation candidates;
- never marks a reservation consumed or revoked;
- never creates or persists a Memory record.

This makes it composable inside a future independently released atomic admission transaction:

```text
future orchestrator establishes write intent / transaction
-> same-session verifier read
-> Memory admission
-> ISSUED -> CONSUMED
-> one caller-owned commit
```

SQLite `BEGIN IMMEDIATE` or any other serialization/write-intent mechanism belongs to that future orchestrator, not this verifier.

## Retry and cancellation

Verification is read-only and non-consuming.

- repeated verification of unchanged exact ISSUED authority is idempotent;
- successful verification leaves state ISSUED;
- cancellation or read failure performs no durable mutation;
- later CONSUMED and REVOKED states fail with their existing terminal-state errors;
- persistence ambiguity cannot be converted into success;
- no retry may mint or substitute another authority id.

## Source and migration fences

Concrete supported production source kinds remain **NONE**. This stage does not invoke `SourcePromotionAuthorityPort` and performs no source re-proof.

Schema delta: **ZERO**. Migration delta: **ZERO**.

R12-D2A owns the parallel migration `24a_r12_stale_lease_scan_index -> 23a_ctx_f5_promotion_reservation`. Its landing is non-blocking to this stage unless actual shared-path or authority semantics change.

## Explicitly closed

CTX-F5-3G-A does not release:

- `PromotionReservationIssuer` implementation or wiring;
- reservation issuance orchestration;
- atomic reservation-to-Memory admission;
- runtime `ISSUED -> CONSUMED` admission wiring;
- Memory repository changes;
- public/model-callable promotion APIs;
- source re-proof adapters or concrete source kinds;
- retrieval / ContextBuilder / model visibility;
- revocation policy/runtime;
- deletion, erasure, or tombstone behavior;
- schema/migration authority;
- CAS/R11/R12 authority;
- F6/F7/F9/F10.
