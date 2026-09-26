# CTX-F5-3E — Atomic Reservation-to-Memory Admission Contract

Primary authority: Issue #15.  
Canonical policy: Issue #85 v2.

## Exact release baseline

```text
canonical main = fd5b2d2e916bac6c21c3fb4749a815dbda60f5ec
Wave #109 = COMPLETE
CTX-F5-3D = LANDED / CANONICAL / HEALTHY
post-wave Architecture #1392 = GREEN/GREEN
F5-3E class = CONTRACT / AUTHORITY FREEZE ONLY
production delta = ZERO
concrete supported source kinds = NONE
```

F5-3E freezes the atomic durability semantics required before any trusted Memory-promotion runtime may be released. It does not implement durable reservation state, reservation persistence, issuer/verifier authority, or a promotion orchestrator.

## 1. One authoritative admission operation

A successful future promotion admission means both conditions become durable together:

```text
exact Memory record is durable
AND
exact trusted reservation transitions ISSUED -> CONSUMED
```

There is no successful durable outcome where only one side commits.

Standalone `PromotionReservationVerifier.verify(...)` remains non-consuming trusted state/equality verification only. Verifier success is insufficient as the final durable admission handoff.

The authoritative operation must therefore own admission as one durability decision rather than composing:

```text
verify
-> independently put Memory
-> best-effort mark reservation consumed
```

That split sequence is explicitly insufficient.

## 2. One durability and transaction boundary

Future reservation authority and durable Memory admission must be transaction-co-locatable.

One authoritative transaction/session must perform, in one durability boundary:

1. load and validate the trusted reservation state;
2. acquire the reservation serialization fence through lock, compare-and-swap, or backend-equivalent transactional authority;
3. revalidate exact canonical `MemoryPromotionIntent`;
4. construct the exact `MemoryRecord` from already-authorized provenance;
5. perform existing Memory admission/replay logic;
6. transition the same reservation from `ISSUED` to `CONSUMED`;
7. commit exactly once.

The implementation must not:

- open an independent reservation transaction followed by a Memory transaction;
- consume before Memory durability;
- commit Memory and later best-effort consume;
- use a second connection/session that escapes the authoritative transaction boundary;
- create another idempotency or Memory identity authority.

For SQLite, future implementation must preserve the existing Memory admission discipline that acquires write intent before any read. The same authoritative session/connection must encompass reservation validation and Memory admission.

For other SQL backends, equivalent single-transaction serialization/CAS semantics are required.

F5-3E freezes this requirement only. It releases no SQL statements, row model, schema, repository, locking primitive, or migration.

## 3. Atomic state transition

The authoritative transition is:

```text
ISSUED + exact canonical intent
-> atomic exact Memory commit + reservation CONSUMED
```

Rules:

- `ISSUED` with exact canonical intent may proceed.
- `REVOKED` fails closed.
- A different canonical intent against the same reservation fails closed.
- F5-3E introduces no new durable logical reservation state beyond F5-3D `ISSUED / CONSUMED / REVOKED`.
- Any future state-machine expansion requires separate authority review.

Reservation state is not timestamp freshness authority.

## 4. Idempotent ambiguous-commit retry

If the authoritative transaction commits but the response is lost or cancellation becomes visible after commit, retry of the same exact canonical intent must converge on the already committed Memory result.

The admission operation may treat this state as replay success only when all are true:

```text
reservation state = CONSUMED
exact Memory exists for same promotion_authority_id
Memory identity matches
immutable Memory material is exact replay-equivalent
owner/source/content/metadata/schema bindings are exact
```

This replay rule does not change standalone verifier semantics. `PromotionReservationVerifier.verify(...)` still succeeds only for an eligible `ISSUED` reservation.

If `CONSUMED` exists but Memory is absent, has another identity, or differs in any immutable replay material, admission fails closed as durable authority corruption/conflict.

No new `promotion_authority_id` may be minted to escape ambiguous commit state.

## 5. Concurrent admission

Two concurrent admission attempts using one exact reservation cannot create two Memory records or two independent successful commits.

Required observable semantics:

- one authoritative transaction wins;
- a concurrent/later retry with the same exact canonical intent converges on the same committed Memory;
- a concurrent/later different intent fails closed;
- no second successful admission is authorized by process-local timing.

The existing durable Memory UNIQUE constraint on `promotion_authority_id` remains defense-in-depth. It is not itself the authorization protocol and cannot replace trusted reservation serialization and exact-intent validation.

## 6. Cancellation and crash boundary

Before authoritative transaction commit:

```text
error or cancellation
-> reservation remains non-consumed
-> Memory is not durably admitted
```

After authoritative commit:

```text
response lost or cancellation observed by caller
-> retry recovers exact committed Memory
-> reservation remains CONSUMED
```

Forbidden split outcomes:

- reservation CONSUMED while Memory commit rolled back;
- Memory committed while reservation remains ISSUED because consume was best-effort;
- retry mints a new promotion authority to bypass an ambiguous outcome.

Cancellation semantics must not weaken the atomic durability boundary.

## 7. Exact canonical intent is authoritative inside the transaction

The authoritative transaction must revalidate the complete trusted promotion material inside the same durability boundary.

Required exact bindings include:

- trusted owner;
- exact source snapshot;
- exact `context_source_id`;
- exact proof receipt;
- exact authority state token;
- `MEMORY_PROMOTION` proof scope;
- exact content and content digest;
- exact canonical metadata;
- Memory schema version;
- exact `promotion_authority_id`.

Authority equality is type-sensitive canonical equality. Python dict/object equality is not sufficient authority comparison.

Existing `canonical_memory_bytes(...)` remains the canonical Memory JSON representation boundary.

## 8. Existing Memory admission primitives remain subordinate to the future authority operation

Current canonical F5-2 provides Memory durability primitives that a future atomic admission implementation may compose inside the authoritative transaction:

- `MemoryRecordRow.promotion_authority_id` has a UNIQUE constraint;
- `DurableMemoryRecordRepository` is transaction-scoped;
- `MemoryRecordRepository` exposes `put`, `get`, and `get_by_promotion_authority`;
- replay equivalence uses canonical immutable Memory material;
- SQLite admission establishes write intent before read;
- `sqlite_memory_admission_transaction(...)` owns one SQLite admission transaction and commits/rolls back the same session;
- `create_memory_record(...)` constructs a Memory value from already-authorized provenance input.

These primitives are not promotion authorization by themselves.

F5-3E does not modify them.

## 9. Source authority remains unchanged

Concrete supported promotion source kinds remain:

```text
NONE
```

Authority boundaries remain:

- CAS owns ASSET/provider/lifecycle/deletion/physical-GC authority.
- R11/R12 own execution/checkpoint/continuation/recovery authority in their domains.
- CTX atomic admission does not become a source retention root.
- Source current-read/liveness proof remains source-owner responsibility.
- No source-specific invalidation adapter is released here.
- No ASSET, transcript, checkpoint, Session, Task, Branch, or Tool Response source is made promotable by F5-3E.

Atomic Memory admission authority does not imply source lifecycle authority.

## 10. Contract-only failure taxonomy

F5-3E freezes semantic failure categories only:

```text
ADMISSION_RESERVATION_NOT_ISSUED
ADMISSION_RESERVATION_REVOKED
ADMISSION_INTENT_CONFLICT
ADMISSION_MEMORY_REPLAY_CONFLICT
ADMISSION_CONSUMED_MEMORY_MISSING
ADMISSION_CONSUMED_MEMORY_MISMATCH
ADMISSION_TRANSACTION_UNAVAILABLE
ADMISSION_PERSISTENCE_FAILURE
```

These do not add production exception classes, retry policy, HTTP mapping, telemetry mapping, or fallback authority.

A later implementation stage must separately freeze concrete error surfaces.

## 11. Future-compatible architecture evidence

F5-3E architecture evidence may positively bind canonical existing surfaces needed by this contract:

- durable Memory uniqueness by `promotion_authority_id`;
- transaction-scoped `DurableMemoryRecordRepository`;
- `MemoryRecordRepository.put/get/get_by_promotion_authority`;
- canonical immutable replay comparison;
- SQLite write-intent-before-read behavior;
- same-session SQLite admission transaction commit/rollback behavior;
- non-authorizing `create_memory_record(...)`;
- landed F5-3D states `ISSUED / CONSUMED / REVOKED`;
- landed F5-3D non-consuming verifier semantics.

Evidence must not add a permanent live-source absence invariant or a permanent repository/schema/runtime absence invariant that would make a later separately released, correct reservation/admission implementation fail merely because it exists.

## 12. Explicitly closed implementation authority

The following remain CLOSED:

```text
reservation SQL model/schema/migration = CLOSED
reservation repository implementation = CLOSED
trusted issuer implementation = CLOSED
trusted verifier implementation = CLOSED
atomic admission runtime/orchestrator = CLOSED
production MemoryRecordRepository modification = CLOSED
HTTP/public/model-callable promotion API = CLOSED
source-specific proof adapters = CLOSED
concrete supported source kinds = NONE
retrieval/ContextBuilder/model visibility = CLOSED
revocation/erasure/tombstone implementation = CLOSED
CAS authority transfer = NONE
R11/R12 authority transfer = NONE
F6/F7/F9/F10 authority = CLOSED
```

No production file is changed by F5-3E.

## 13. Integration gate

Exact candidate scope from `main@fd5b2d2e916bac6c21c3fb4749a815dbda60f5ec` is only:

1. `docs/context_future/CTX_F5_3E_ATOMIC_PROMOTION_ADMISSION_FD5B2D2E.md`
2. `se/tests/architecture/test_ctx_f5_3e_atomic_promotion_admission_contract.py`

Integration requires:

- exactly this two-file zero-production delta;
- fresh Linux + Windows Architecture GREEN/GREEN;
- zero unresolved blocking review threads;
- independent FINAL GREEN;
- Policy #85 v2 classification of any later canonical drift.

After FINAL GREEN, Issue #15 zero-production contract/evidence merge policy may apply unless the candidate is intentionally enrolled in an Integration Wave.

## Non-authority statement

F5-3E freezes the future atomic reservation-to-Memory durability contract only. It creates no reservation persistence, performs no trusted verification, executes no atomic admission, makes no source kind promotable, persists no new Memory through a promotion runtime, and exposes no model-visible Memory capability.
