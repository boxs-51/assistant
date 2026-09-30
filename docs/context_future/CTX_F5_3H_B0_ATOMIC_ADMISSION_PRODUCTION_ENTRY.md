# CTX-F5-3H-B0 — Atomic Admission Production Entry Freeze

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.

## Status

```text
stage = CTX-F5-3H-B0
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
baseline = 73714a4405f9ed10f16117b763215467d0a3e607
corrective baseline = fe608280c33e1471fd1fd84006de2e9e166a642c
post-merge P1 = P1-CTX-HB0-MUTABLE-CONTENT-SNAPSHOT-1
canonical migration head = 25a_ubq1_user_budget_foundation
parent CTX-F5-3H-A = LANDED / CANONICAL / HEALTHY
production delta = ZERO
runtime delta = ZERO
schema/migration delta = ZERO
H-B1 production CLAIM = CLOSED
H-B2 production CLAIM = CLOSED
runtime/API/source/non-SQLite authority = CLOSED
```

H-B0 freezes the production-entry prerequisites for the later SQLite atomic
reservation-to-Memory admission implementation. It grants no production
implementation authority.

## 1. Stage decomposition

The accepted H-B decomposition is:

```text
H-B0 = contract / architecture evidence / zero production
H-B1 = public canonical Memory replay-equivalence helper migration
H-B2 = SQLite atomic promotion-admission service + exact errors
H-B3 = EXIT / INTEGRATION EVIDENCE GATE for H-B2
```

H-B3 is not an independent production authority. H-B2 cannot reach FINAL GREEN
or integration-ready status until H-B3-equivalent exit evidence is satisfied.

Hard rule:

```text
H-B2 production cannot merge without H-B3-equivalent exit evidence.
```

## 2. Public canonical Memory replay-equivalence authority

The future public domain helper is frozen in:

`se/src/context/memory.py`

Preferred surface:

```python
def memory_records_replay_equivalent(
    left: MemoryRecord,
    right: MemoryRecord,
) -> bool:
    ...
```

It must be the single canonical replay-equivalence authority.

Required semantics:
- exactly the current immutable Memory replay semantics;
- ignore only top-level `created_at`;
- preserve canonical source-ref datetime normalization;
- no I/O;
- no repository access;
- no mutation;
- no identity minting;
- no persistence semantics;
- both in-memory and durable Memory replay handling migrate to this helper;
- private duplicate comparison authority must not remain as a second semantic implementation;
- atomic admission must consume this helper rather than duplicate comparison logic.

The H-B1 production paths are limited to:
- `se/src/context/memory.py`;
- `se/src/infrastructure/storage/repositories/memory.py`;
plus dedicated tests/docs.

## 3. Exact admission error family

The future admission-level error family is frozen in:

`se/src/context/memory_promotion.py`

```python
class MemoryPromotionAdmissionError(RuntimeError): ...

class PromotionAdmissionReservationNotIssuedError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionReservationRevokedError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionIntentConflictError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionMemoryReplayConflictError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionConsumedMemoryMissingError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionConsumedMemoryMismatchError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionTransactionUnavailableError(MemoryPromotionAdmissionError): ...
class PromotionAdmissionPersistenceFailureError(MemoryPromotionAdmissionError): ...
```

No HTTP/status-code mapping is opened.

Exact mapping:

```text
caller reservation structural-integrity failure
  -> existing PromotionPrimitiveIntegrityError family propagates unchanged

supplied content not valid canonical Memory JSON
  -> PromotionAdmissionIntentConflictError

missing durable reservation authority
  -> PromotionAdmissionReservationNotIssuedError

durable reservation REVOKED
  -> PromotionAdmissionReservationRevokedError

durable intent != envelope intent
or computed payload digest != durable intent.content_digest
  -> PromotionAdmissionIntentConflictError

ISSUED + pre-existing durable Memory for same authority
  -> PromotionAdmissionMemoryReplayConflictError

MemoryRecordConflictError on first-admission persistence/convergence
  -> PromotionAdmissionMemoryReplayConflictError

CONSUMED + missing Memory
  -> PromotionAdmissionConsumedMemoryMissingError

CONSUMED + reconstructed Memory present but not canonical replay-equivalent
  -> PromotionAdmissionConsumedMemoryMismatchError

MemoryAdmissionTransactionError
or failure acquiring the SQLite authoritative BEGIN IMMEDIATE boundary
  -> PromotionAdmissionTransactionUnavailableError

durable-row reconstruction corruption,
reservation persistence unavailable after transaction establishment,
unexpected state-transition conflict after authoritative lock,
Memory persistence/reconstruction failure,
or commit failure
  -> PromotionAdmissionPersistenceFailureError
```

`asyncio.CancelledError` propagates unchanged.

The admission service must not use a broad `BaseException` translation. The
transaction context may retain its existing BaseException rollback discipline,
but the admission boundary must never convert cancellation into an admission
failure.

## 4. Pure preflight before SQLite write intent

Only pure/non-persistent work may occur before the authoritative SQLite
transaction, and the caller-owned payload must be detached before the first
await that can cross into session or transaction work.

Required preflight shape:

```text
validate_promotion_reservation_integrity(reservation)

canonicalize caller content to detached canonical JSON bytes
materialize one detached canonical content_snapshot
compute payload digest using existing memory_content_digest(content_snapshot)
```

One acceptable implementation shape is:

```python
canonical_payload_bytes = canonical_memory_bytes(content)
content_snapshot = json.loads(canonical_payload_bytes.decode("utf-8"))
payload_digest = memory_content_digest(content_snapshot)
```

An equivalent single-purpose domain helper is allowed only if it preserves the
same canonical JSON domain and still produces a detached snapshot.

Required authority invariant:

```text
AUTHORIZED PAYLOAD = detached canonical preflight snapshot
NOT the caller-owned mutable object after preflight
```

The snapshot must no longer alias caller `dict` / `list` containers.
Invalid canonical Memory JSON fails before session acquisition and maps to
`PromotionAdmissionIntentConflictError`.

After snapshot completion, the original caller `content` object MUST NOT be
read or used again. Every later digest comparison and every expected-Memory
construction must use the exact detached `content_snapshot`.

No repository read is allowed during validation, canonicalization, snapshot
creation, or digest computation.

The payload digest must use the existing `memory_content_digest(...)`
authority over the detached snapshot. No second digest implementation is
allowed.

After pure preflight:

```text
async with session_factory() as session:
    immediately enter sqlite_memory_admission_transaction(session)
    BEFORE any reservation or Memory durable read
```

## 5. Exact orchestrator ownership

Preferred production path:

`se/src/infrastructure/storage/services/memory_promotion_admission.py`

Preferred service:

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

No new public runtime Protocol is required for the initial H-B production slice
unless a later independent audit proves a concrete consumer requires one.

## 6. Exact session dependency and transaction ownership

The service-local dependency is:

```python
MemoryAdmissionSessionContextFactory
    = Callable[[], AsyncContextManager[AsyncSession]]
```

Do not reuse/import the issuer's `SessionContextFactory` merely to share a type
alias. Admission ownership must remain independent from issuer ownership.

Per admission call:
- exactly one fresh session context;
- the session must be clean at transaction entry;
- exactly one `sqlite_memory_admission_transaction(session)`;
- transaction entry occurs before every durable reservation/Memory read;
- use the yielded `DurableMemoryRecordRepository`;
- construct `DurablePromotionReservationRepository(session)` on the same session;
- no `session.commit()`, `rollback()`, or `begin()` in the orchestrator;
- no second session or UoW;
- no `SqlAlchemyUnitOfWork` or `DatabaseDriver` modification;
- the transaction context is the sole commit/rollback owner;
- non-SQLite admission fails closed.

## 7. Durable authority validation

Inside the already-established SQLite write-intent boundary:

1. load the durable reservation only by exact
   `reservation.promotion_authority_id`;
2. missing authority maps to
   `PromotionAdmissionReservationNotIssuedError`;
3. canonical-compare durable intent to envelope intent;
4. compare the payload digest precomputed from the detached
   `content_snapshot` with durable `intent.content_digest`;
5. branch only on exact durable reservation state.

No lookup by intent, digest, proof tuple, or alternate authority may substitute
another promotion authority.

Standalone G-A verifier remains outside this authoritative replay path.

## 8. Expected Memory construction

For first admission and CONSUMED replay comparison, expected Memory is
constructed only from durable authority using existing
`create_memory_record(...)` semantics:

```text
source_ref = durable intent.source_ref_snapshot
promotion_authority_id = durable reservation authority id
content = detached preflight content_snapshot
metadata = durable intent.metadata
memory_schema_version = durable intent.memory_schema_version
```

There is no caller-supplied source, owner, metadata, schema-version, intent, or
alternate authority field.

For CONSUMED replay, constructing this expected in-process Memory is
non-authorizing and non-mutating. Both first admission and CONSUMED replay
must use the exact same detached preflight `content_snapshot`; neither path
may reread the original caller-owned `content` object.

## 9. Exact state machine

```text
ISSUED:
  pre-existing Memory by exact authority
    -> PromotionAdmissionMemoryReplayConflictError
    -> FAIL CLOSED
    -> zero consume

  no existing Memory
    -> create expected Memory
    -> durable Memory put
    -> mark exact reservation CONSUMED
    -> transaction context commits once
    -> return durable Memory winner

CONSUMED:
  exact-authority Memory missing
    -> PromotionAdmissionConsumedMemoryMissingError

  Memory present but replay mismatch
    -> PromotionAdmissionConsumedMemoryMismatchError

  exact replay-equivalent
    -> return existing durable Memory
    -> ZERO put
    -> ZERO consume
    -> ZERO mutation

REVOKED:
  -> PromotionAdmissionReservationRevokedError
  -> ZERO Memory write

missing:
  -> PromotionAdmissionReservationNotIssuedError
  -> ZERO Memory write
```

First-admission `MemoryRecordConflictError` maps to
`PromotionAdmissionMemoryReplayConflictError`.

ISSUED + existing Memory must never be healed by consuming the reservation.

## 10. Cancellation, failure, and retry boundary

Before transaction commit:
- failure produces no success;
- cancellation propagates unchanged;
- transaction rollback owns both Memory and reservation effects.

After durable commit with lost response:
- retry sees CONSUMED;
- pure preflight creates a fresh detached snapshot for that retry invocation;
- load exact Memory;
- construct expected Memory in-process from that invocation's detached snapshot;
- compare with `memory_records_replay_equivalent(...)`;
- exact replay returns the same Memory;
- no replacement promotion authority is minted.

Commit-time failure from transaction-context exit maps to
`PromotionAdmissionPersistenceFailureError`.

Low-level durable-row corruption must never be reported as
reservation-not-issued or replay success.

## 11. Mandatory H-B2 / H-B3-equivalent evidence

Before H-B2 can reach FINAL GREEN / integration-ready, evidence must prove:

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
16. current migration lineage remains healthy with zero migration delta;
17. pure envelope/content preflight occurs before BEGIN IMMEDIATE with no durable read;
18. canonical payload digest uses existing memory_content_digest authority;
19. CONSUMED replay constructs expected Memory only in-process and performs zero repository mutation;
20. commit-time failure is translated to PromotionAdmissionPersistenceFailureError;
21. cancellation is not swallowed or translated;
22. durable-row corruption cannot be misreported as reservation-not-issued or replay success;
23. mutable caller content is detached before the first session/transaction await;
24. mutation of the original caller dict/list after preflight cannot change the persisted/compared Memory payload or identity;
25. the original caller content object is never reused after snapshot creation;
26. payload digest and create_memory_record(...) consume the same detached snapshot.

## 12. Closed authority

H-B0 does not release:
- H-B1 production implementation;
- H-B2 production implementation;
- repository/model/schema/Alembic changes;
- runtime/container wiring;
- HTTP/public/model-callable promotion;
- source-specific promotion adapters;
- any concrete promotable source kind;
- non-SQLite transaction authority;
- ContextBuilder/Working Set/retrieval/ranking;
- CAS lifecycle/provider/GC authority;
- R11 retention/checkpoint/destructive-GC authority;
- R12 execution/recovery authority.

No `se/src/**` file changes are permitted in H-B0.

## 13. Cross-track checkpoint

At H-B0 release:
- AE-R12-E / PR #154 remains in its own HOLD/review/CI lineage with no CTX collision;
- CAS-F7-0 remains an independent generated-media contract/evidence lane;
- UBQ-1 is canonical and does not own CTX Memory promotion/admission;
- R11 retention/checkpoint/destructive-GC ownership remains external to CTX.

## 14. Next-stage gates

H-B1 production CLAIM remains CLOSED until:
- the post-merge P1 corrective H-B0 follow-up lands from current canonical main;
- the detached canonical payload snapshot invariant above is frozen;
- the mandatory evidence matrix contains all 26 proofs;
- corrective exact-head CI is GREEN/GREEN;
- fresh independent corrective FINAL GREEN is recorded;
- the corrective transition reaches canonical main and passes exact-main post-merge health;
- no material main/dependency drift invalidates the freeze.

H-B2 production CLAIM remains CLOSED until H-B1 is independently released,
implemented, evidenced, and canonical.

## H-B0 exact scope

Exactly:
- `docs/context_future/CTX_F5_3H_B0_ATOMIC_ADMISSION_PRODUCTION_ENTRY.md`
- `se/tests/architecture/test_ctx_f5_3h_b0_atomic_admission_production_entry.py`

Production/runtime/schema/migration delta = ZERO.
