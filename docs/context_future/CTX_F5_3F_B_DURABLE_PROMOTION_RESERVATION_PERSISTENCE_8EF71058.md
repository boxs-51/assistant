# CTX-F5-3F-B — Durable Promotion-Reservation Persistence Foundation

## Claim baseline

~~~text
primary authority = Issue #15
policy = Issue #85 v2.5
release = independent auditor comment #5853267393
development base = 8ef71058b3358c23ccc81deb712546801ef152e1
canonical migration head at CLAIM = 22a_r12_execution_lease_fence
revision = 23a_ctx_f5_promotion_reservation
down_revision = 22a_r12_execution_lease_fence
class = PRODUCTION / PERSISTENCE FOUNDATION
production merge authority = NONE
~~~

F5-3F-A already froze the durable reservation contract. F5-3F-B implements
only the trusted server-internal persistence representation, repository
primitives, and one linear migration needed by that contract.

## Production ownership

Exactly these production paths are owned by this slice:

1. se/src/infrastructure/storage/models/sql/promotion_reservation.py
2. se/src/infrastructure/storage/repositories/promotion_reservation.py
3. se/src/infrastructure/storage/migrations/sql/versions/23a_ctx_f5_promotion_reservation.py

No runtime callsite or public API is part of this slice.

## Trusted durable representation

The durable row is distinct from the caller-constructible PromotionReservation
envelope. Durable authority material contains promotion_authority_id,
intent_json, exact canonical intent bytes, SHA-256 intent_digest, canonical
source_context_source_id, proof receipt/token/scope, and trusted state exactly
ISSUED, CONSUMED, or REVOKED.

Reload reconstructs MemoryPromotionIntent, re-runs structural integrity,
re-canonicalizes it using the existing canonical_memory_bytes boundary, and
requires an exact byte match with the persisted canonical bytes. The digest is
then recomputed from those bytes. A digest hit never substitutes for exact-byte
identity.

## Durable uniqueness and convergence

The database enforces one promotion_authority_id primary identity, one unique
intent_digest concurrency winner, and one unique proof authority tuple:
source_context_source_id + proof_receipt_id + authority_state_token +
proof_scope.

Repository conflict resolution always confirms exact canonical bytes. Same
exact intent with competing candidate authority IDs converges to one durable
winner. Digest equality with different canonical bytes fails closed. Proof
tuple reuse with different exact intent fails closed. Retry never resets a
terminal reservation to ISSUED.

## State machine

Only ISSUED -> CONSUMED and ISSUED -> REVOKED are released. Persisting REVOKED
is only a storage primitive; the policy deciding when or why to revoke remains
CLOSED.

## Transaction authority

DurablePromotionReservationRepository receives one caller-owned AsyncSession.
It never commits, creates a hidden session/connection, or creates a second
idempotency authority. This permits a later independently released stage to
combine it with DurableMemoryRecordRepository in one authoritative
transaction. SQLite BEGIN IMMEDIATE before reservation authority read remains
a later orchestrator responsibility and is not implemented here.

## Downgrade safety

Revision 23a_ctx_f5_promotion_reservation refuses downgrade while any
promotion_reservations row exists.

## Evidence

Evidence covers linear migration 23a -> 22a, one new persistence table,
constraints and uniqueness, fail-closed downgrade, exact canonical round-trip,
type-sensitive JSON identity, synthetic digest collision failure, proof reuse
conflict, same-intent convergence, real file-backed SQLite competing inserts,
terminal transitions, no hidden commit, and shared-AsyncSession composability.

## Still CLOSED

Trusted PromotionReservationIssuer/PromotionReservationVerifier
implementation or wiring, atomic reservation-to-Memory admission, Memory
repository changes, public/model-visible APIs, source adapters, ContextBuilder,
retrieval/model visibility, revocation policy/runtime, CAS/R11/R12 authority,
and F6/F7/F9/F10 remain CLOSED.

A later independently released stage may add issuer/verifier/orchestrator
implementation without being blocked by F5-3F-B evidence. This stage tests
only the persistence boundary it owns.
