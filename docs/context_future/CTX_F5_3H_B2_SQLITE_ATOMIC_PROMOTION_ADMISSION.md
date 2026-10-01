# CTX-F5-3H-B2 — SQLite atomic Memory promotion admission

Primary workspace: Issue #15 / CTX  
Policy: Issue #85 v2.5  
Independent PRE-CLAIM release: Issue #15 comment #5915147365

## Exact baseline and authority

```text
baseline = 9031690d176f0cd82484c83a645b6ced05f21f3b
parent H-B1 = LANDED / CANONICAL / HEALTHY
parent Architecture #1642 = GREEN/GREEN
released production files = exactly 2
1. se/src/context/memory_promotion.py
2. se/src/infrastructure/storage/services/memory_promotion_admission.py
schema/migration authority = CLOSED
runtime/API/source/non-SQLite authority = CLOSED
H-B3 = mandatory exit/evidence gate
```

No repository, SQL model, Alembic, UnitOfWork, DatabaseDriver, runtime, API,
source-adapter, retrieval, CAS, R11, R12, or UBQ authority is transferred.

## Public admission service

```python
class DurableMemoryPromotionAdmission:
    async def admit(
        self,
        *,
        reservation: PromotionReservation,
        content: Any,
    ) -> MemoryRecord:
        ...
```

The service owns exactly one injected
`MemoryAdmissionSessionContextFactory` and immediately enters
`sqlite_memory_admission_transaction(session)` before any durable reservation or
Memory read. The existing SQLite transaction context remains the sole
commit/rollback owner.

## Detached payload authority

Before the first session/transaction await:

```text
validate_promotion_reservation_integrity(reservation)
canonical_payload_bytes = canonical_memory_bytes(content)
content_snapshot = json.loads(canonical_payload_bytes.decode("utf-8"))
payload_digest = memory_content_digest(content_snapshot)
original caller content = DEAD
```

Every later digest comparison and `create_memory_record(...)` call consumes the
same detached `content_snapshot`. Invalid canonical Memory JSON, including
recursive/cyclic caller structures that surface as `RecursionError` during canonical
validation, maps to `PromotionAdmissionIntentConflictError` before session acquisition.

## Durable authority and state machine

The durable reservation is loaded only by exact
`reservation.promotion_authority_id`. Exact intent equality reuses public
`validate_reservation_matches_intent(...)`; repository-private
`_canonical_intent_bytes` is not admission authority.

Expected Memory is created only from durable intent/authority plus the detached
payload snapshot. Durable intent metadata is recursively thawed into ordinary JSON
material through the intent's public JSON serialization before
`create_memory_record(...)`; the frozen in-memory metadata view is never passed
directly as record-construction input. This preserves durable intent as the sole
metadata authority while accepting valid nested object/array metadata.

```text
missing -> ReservationNotIssued
REVOKED -> ReservationRevoked

ISSUED + existing Memory -> MemoryReplayConflict / zero consume
ISSUED + no Memory -> put expected -> mark CONSUMED -> context commits once

CONSUMED + missing Memory -> ConsumedMemoryMissing
CONSUMED + mismatch -> ConsumedMemoryMismatch
CONSUMED + exact replay -> return existing / ZERO put / ZERO consume
```

## Failure boundary

```text
before sqlite transaction context yields:
  MemoryAdmissionTransactionError / SQLAlchemy acquisition failure
  -> PromotionAdmissionTransactionUnavailableError

after transaction context yields:
  reservation persistence/reconstruction failure
  Memory persistence/read/write failure
  unexpected reservation transition conflict
  commit failure
  -> PromotionAdmissionPersistenceFailureError
```

`MemoryRecordConflictError` on first Memory admission maps to
`PromotionAdmissionMemoryReplayConflictError`.
`asyncio.CancelledError` propagates unchanged.
No broad `BaseException` translation exists in the service.

## H-B3-equivalent exit matrix

Canonical H-B0 proofs remain mandatory and are exercised by the H-B2
unit/integration/architecture evidence:

1. same session for Memory and reservation;
2. transaction entered before first durable reservation read;
3. transaction context is the one commit owner;
4. ISSUED success persists Memory and CONSUMED atomically;
5. ISSUED + existing Memory does not heal;
6. CONSUMED exact replay performs zero writes;
7. CONSUMED missing/mismatch performs zero writes;
8. REVOKED/missing authority performs zero Memory write;
9. envelope/digest mismatch performs zero write;
10. cancellation before commit rolls back both effects;
11. persistence failure rolls back both effects;
12. ambiguous post-commit retry returns the same Memory;
13. concurrent same reservation cannot produce two Memory identities or split state;
14. non-SQLite admission fails closed;
15. no second session/UoW/commit path;
16. migration lineage remains healthy with zero migration delta;
17. pure envelope/content preflight occurs before BEGIN IMMEDIATE with no durable read;
18. canonical payload digest uses existing `memory_content_digest`;
19. CONSUMED replay constructs expected Memory in-process and performs zero mutation;
20. commit-time failure maps to `PromotionAdmissionPersistenceFailureError`;
21. cancellation is not swallowed or translated;
22. durable-row corruption cannot become reservation-not-issued or replay success;
23. caller content is detached before first session/transaction await;
24. later caller mutation cannot change persisted/compared Memory;
25. original caller content is not reused after snapshot creation;
26. digest and `create_memory_record(...)` consume the same snapshot;
27. file-backed SQLite pre-yield SQLAlchemy/BEGIN exhaustion maps to TransactionUnavailable;
28. post-yield SQLAlchemy/commit failure maps to PersistenceFailure;
29. durable intent equality uses public `validate_reservation_matches_intent` and never repository-private canonical-intent helpers;
30. implementation changes no third production file and requires no repository/UoW/schema/runtime mutation.

H-B2 cannot be integration-ready until exact-head Linux + Windows Architecture,
fresh review, independent FINAL GREEN, and independent confirmation that the full
30-proof exit gate is satisfied.

## Cross-track boundary

At CLAIM, UBQ-2 PR #171 is a separate TaskBudget/UserBudget + migration lane and
does not touch either H-B2 production path. CAS-F7-0 is canonical and disjoint.
AE-R12-F0 is zero-production and disjoint. Any later material main/migration drift
must be reclassified before H-B2 integration.
