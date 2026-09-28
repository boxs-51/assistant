# CTX-F5-3H-A — Atomic Promotion Admission Interface + Transaction Freeze

Primary authority: Issue #15 / CTX.  
Canonical governance: Issue #85 v2.5.

## Status

```text
stage = CTX-F5-3H-A
class = CONTRACT / ARCHITECTURE EVIDENCE ONLY
development baseline = 896a6e293fbe07635541f872dd0a55f21784918a
parent CTX-F5-3G-B = MERGED / CANONICAL / HEALTHY
production delta = ZERO
runtime delta = ZERO
schema/migration delta = ZERO
concrete supported source kinds = NONE
production atomic admission = CLOSED
non-SQLite atomic admission = CLOSED
```

H-A freezes the exact production contract required before any atomic
reservation-to-Memory admission runtime may be claimed. It does not implement
that runtime.

This stage consumes the already canonical contracts and primitives from F5-2,
F5-3D/E/F, F5-3G-A, and F5-3G-B without transferring authority from them.

## 1. Future callable has no duplicate caller intent

The preferred future production callable is:

```python
admit(
    *,
    reservation: PromotionReservation,
    content: Any,
) -> MemoryRecord
```

There is no second caller-supplied `intent` argument.

`PromotionReservation` already structurally carries one
`MemoryPromotionIntent`. Inside the authoritative transaction, the durable
reservation row is the authoritative intent.

A second caller-supplied intent would create duplicate authority and mismatch
ambiguity and is therefore prohibited.

A dedicated admission DTO is not required by H-A. A later production stage may
request one only if independently audited evidence proves another trusted input
is unavoidable.

## 2. Content is the only separate payload material

`MemoryPromotionIntent` intentionally binds `content_digest`, not the actual
Memory payload.

The future admission boundary therefore receives only the actual `content`
payload in addition to the reservation envelope.

Before persistence success:

1. validate the reservation envelope structurally;
2. canonicalize the supplied content with existing Memory canonical JSON rules;
3. compute the canonical content digest;
4. inside the authoritative transaction, load the exact durable reservation by
   `promotion_authority_id`;
5. canonical-compare the durable intent with `reservation.intent`;
6. require computed payload digest to equal the durable
   `intent.content_digest`.

Memory material is derived only from the durable intent:

```text
source provenance = durable intent.source_ref_snapshot
owner = durable intent/source binding
metadata = durable intent.metadata
schema version = durable intent.memory_schema_version
promotion authority = exact durable reservation promotion_authority_id
payload = explicit trusted content argument
```

No second metadata, source, owner, schema-version, authority-id, or intent
authority is accepted.

## 3. SQLite is the first and only released future backend authority

Current evidence proves the serialization discipline of:

`sqlite_memory_admission_transaction(session)`

Therefore the first future production admission implementation is frozen as
SQLite-authoritative only.

Required transaction composition:

```text
fresh injected AsyncSession
-> enter sqlite_memory_admission_transaction(session)
   BEFORE any reservation authority read
-> receive DurableMemoryRecordRepository bound to that same session
-> construct DurablePromotionReservationRepository on that same session
-> perform all reservation and Memory reads/writes on that same session
-> context owns the one commit / rollback
```

The future orchestrator must not:

- call `session.commit()` inside that scope;
- open a second session or connection;
- perform a reservation authority read before entering the SQLite write-intent
  boundary;
- substitute ordinary `session.begin()` for `BEGIN IMMEDIATE` authority;
- claim generic SQL equivalence;
- create another transaction/UoW authority.

Non-SQLite atomic admission remains CLOSED until a separate backend-specific
serialization, row-lock, or CAS contract is independently released.

## 4. Durable reservation intent is authoritative inside the transaction

The caller envelope is untrusted until checked against durable state.

Inside the already-established authoritative SQLite boundary:

1. load by the exact `reservation.promotion_authority_id`;
2. do not search by intent, digest, proof tuple, or alternate authority;
3. require the durable canonical intent to equal `reservation.intent`;
4. require the supplied payload digest to equal durable
   `intent.content_digest`;
5. branch only on the exact durable reservation state.

Unknown or missing authority fails closed. There is no substitution by another
durable winner.

Standalone G-A verifier is not the authoritative atomic replay gate. Its
non-consuming `ISSUED` semantics remain correct for standalone verification,
but atomic admission must also handle a legitimate `CONSUMED` replay after an
ambiguous committed success.

## 5. ISSUED path — atomic create then consume

The only normal first-admission path is:

```text
durable reservation = ISSUED
+ durable intent exact
+ content digest exact
+ no Memory exists for promotion_authority_id
-> construct exact MemoryRecord
-> DurableMemoryRecordRepository.put(...)
-> mark same reservation CONSUMED
-> transaction context commits once
-> return durable Memory winner
```

`create_memory_record(...)` remains a non-authorizing constructor. It may be
used only after the exact durable authorization checks above.

Neither Memory persistence nor reservation transition owns an independent
commit.

### Split-brain corruption fence

This state is forbidden:

```text
reservation = ISSUED
AND Memory already exists for same promotion_authority_id
```

It means the F5-3E atomic durability invariant has already been violated.

Required behavior:

```text
ISSUED + existing durable Memory
=> ADMISSION_MEMORY_REPLAY_CONFLICT
=> FAIL CLOSED
=> no consume
=> no overwrite
=> no "healing"
```

The orchestrator must never silently consume the reservation to normalize this
state.

## 6. CONSUMED path — read-only replay

For a durable `CONSUMED` reservation:

```text
load Memory by exact promotion_authority_id
-> missing:
     ADMISSION_CONSUMED_MEMORY_MISSING
-> present but immutable replay material differs:
     ADMISSION_CONSUMED_MEMORY_MISMATCH
-> exact canonical replay-equivalent:
     return the existing durable Memory
```

This path is strictly read-only.

It must NOT call `MemoryRecordRepository.put(...)`.

Using `put()` on this path could create a missing Memory and conceal a
previous split transaction/corruption. That is prohibited.

It must not call `mark_consumed(...)` again and must not mint another
promotion authority.

## 7. REVOKED and missing authority fail closed

`REVOKED` never creates Memory and never returns replay success.

Missing or unknown reservation authority fails closed as a reservation
not-issued/not-found admission failure.

No intent/digest/proof lookup may substitute another authority ID.

## 8. Canonical non-mutating Memory replay equivalence is a production prerequisite

Current Memory replay semantics already define canonical immutable equivalence
and ignore only `created_at`.

The future CONSUMED replay path needs that same authority without mutation.

Before a production atomic orchestrator may be CLAIMED, one canonical reusable
non-mutating replay-equivalence helper must be independently released or exposed
from the Memory ownership boundary.

Required properties:

- performs no write;
- ignores only `created_at`, exactly matching current Memory replay semantics;
- compares every other immutable Memory field;
- reuses the existing canonical JSON and datetime normalization semantics;
- cannot create a missing record;
- cannot become a second Memory identity or idempotency authority.

H-A does not implement or expose this helper.

Duplicating immutable-field comparison logic inside the future orchestrator is
not released by this contract.

## 9. Error surface freeze

H-A preserves the F5-3E semantic categories:

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

Additional exact mappings:

```text
ISSUED + existing Memory
  -> ADMISSION_MEMORY_REPLAY_CONFLICT

durable intent != reservation.intent
or supplied payload digest != durable intent.content_digest
  -> ADMISSION_INTENT_CONFLICT

SQLite clean-session / write-intent acquisition failure
  -> ADMISSION_TRANSACTION_UNAVAILABLE

Memory/reservation persistence failure or commit failure
  -> ADMISSION_PERSISTENCE_FAILURE
```

Existing lower-level repository errors may be causal inputs. The future atomic
admission boundary must expose stable admission-level semantics.

Exact Python exception classes remain reserved for the later production stage.

No HTTP/API mapping is opened.

`asyncio.CancelledError` propagates unchanged and is never converted into
success.

## 10. Cancellation, failure, and ambiguous commit

Before the authoritative transaction commit:

```text
failure or cancellation
-> no successful admission response
-> transaction rollback owns both Memory and reservation changes
```

After a durable commit but before the caller observes success:

```text
retry same reservation + same exact content
-> durable state is CONSUMED
-> read exact Memory
-> canonical replay-equivalence check
-> return same Memory
```

The retry never mints a replacement promotion authority.

## 11. Authority boundaries remain closed

H-A does not release:

- atomic admission orchestrator/service implementation;
- a new production Protocol;
- Memory repository production modification;
- promotion-reservation repository production modification;
- SQL model/schema/Alembic migration;
- UnitOfWork or DatabaseDriver modification;
- runtime/container wiring;
- HTTP/public/model-callable promotion API;
- source-specific proof adapters;
- any concrete promotable source kind;
- non-SQLite transaction authority;
- retrieval, ContextBuilder, model Working Set, embeddings, or ranking;
- revocation/erasure/tombstone implementation;
- CAS lifecycle/provider/GC authority;
- R11 retention/checkpoint/destructive-GC authority;
- R12 execution/recovery authority;
- F6/F7/F9/F10 authority.

Atomic admission production remains CLOSED.

## 12. Production entry prerequisites after H-A

A later production stage may be PRE-CLAIMED only after an independent audit
freezes at minimum:

1. the canonical non-mutating Memory replay-equivalence helper;
2. exact admission-level Python exceptions;
3. exact orchestrator production path and ownership;
4. exact session-context dependency;
5. exact SQLite-only dialect guard;
6. positive crash/cancellation/concurrency evidence;
7. exact same-session proof for reservation + Memory;
8. no second commit/session/UoW;
9. no public/runtime/source wiring unless separately released.

## 13. H-A exact scope and acceptance

This H-A slice is exactly two files:

- `docs/context_future/CTX_F5_3H_A_ATOMIC_PROMOTION_ADMISSION_INTERFACE.md`
- `se/tests/architecture/test_ctx_f5_3h_a_atomic_promotion_admission_interface.py`

No `se/src/**` file changes.

Architecture evidence must positively freeze:

1. no duplicate caller `intent`;
2. explicit payload digest binding;
3. durable intent authority inside transaction;
4. SQLite write intent before reservation read;
5. same session for reservation and Memory;
6. one commit owner only;
7. ISSUED + no Memory -> future create/consume atomically;
8. ISSUED + existing Memory -> fail closed;
9. CONSUMED + exact Memory -> read-only replay success;
10. CONSUMED + missing Memory -> fail closed without creation;
11. CONSUMED + mismatch -> fail closed;
12. REVOKED -> fail closed;
13. no standalone verifier as replay prerequisite;
14. no second session/transaction;
15. non-SQLite production authority remains closed;
16. canonical non-mutating Memory replay-equivalence helper is required before a
    production orchestrator CLAIM.

## Non-authority statement

CTX-F5-3H-A defines no production callable, executes no atomic admission,
changes no repository, makes no source kind promotable, creates no Memory,
consumes no reservation, exposes no API, and grants no production merge
authority.
