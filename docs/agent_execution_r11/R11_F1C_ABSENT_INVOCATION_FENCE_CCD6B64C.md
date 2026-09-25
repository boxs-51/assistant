# R11-F1-C Repair C — Absent Invocation-ID Serialization Fence

Status: **BOUNDED REPAIR C / PRODUCTION SERIALIZATION / F1-C STILL HOLD**

Primary authority: Issue #31.

Exact refreshed baseline:

```text
original Repair-C base = ccd6b64ca58408ec45db0533fd55ee2ba7c01c13
current canonical main = 628f61fd7894e1425daf9a989f4c4494f7492a94
main Architecture #1247 / run 36119165438 = GREEN/GREEN
Repair B / PR #76 = MERGED / LANDED
P1-R11-F1C-ABSENT-INVOCATION-FENCE-2 = OPEN / BLOCKING
R11-F1-C destructive implementation remains HOLD
```

Repair C was re-anchored without semantic scope expansion after CAS-F5 main drift. The five intervening commits have no direct path overlap with Repair C's four owned files.

## 1. Proven gap

Repair B made Agent fresh `save_tool_call()` share the existing-row
CapabilityInvocation GC fence. That fence was incomplete for an invocation id
whose R6 row did not exist yet:

- PostgreSQL `SELECT ... FOR UPDATE` on an absent row acquires no row lock;
- SQLite existing-row no-op UPDATE returns zero rows when absent;
- `SqlCapabilityInvocationStore.create()` previously inserted the R6 row
  without acquiring any shared invocation-id authority.

Therefore two writers could independently observe an absent invocation id and
commit conflicting durable ownership.

## 2. Shared absent-or-existing key authority

Repair C adds one transaction-scoped invocation-id serialization authority.

### PostgreSQL

A stable signed 64-bit key is derived from SHA-256 over the namespaced
invocation id and locked with:

```text
pg_advisory_xact_lock
```

The lock is transaction scoped, cross-process, and exists independently of
whether a CapabilityInvocation row currently exists.

Hash collisions are conservative: unrelated ids may serialize, but conflicting
ownership cannot become less protected.

### SQLite

SQLite does not provide transaction-scoped advisory locks. Repair C executes a
semantic no-op UPDATE with an always-false predicate against the
CapabilityInvocation table. It mutates zero rows but deliberately starts a
SQLite write transaction, obtaining the database writer lock even for an absent
invocation id.

This is coarser than PostgreSQL but fail-safe and cross-connection/process for
the SQLite database.

Unsupported SQL dialects fail closed rather than silently falling back to an
absent-key-unsafe implementation.

## 3. Both writer paths share the same authority

### Agent fresh save_tool_call

The existing Agent fresh `save_tool_call()` path continues to call
`lock_invocation_gc_serialization_fence()`. Repair C strengthens that method
so it first acquires the new absent-or-existing invocation-id key authority,
then preserves the existing-row SQLite UPDATE / row-locking
`SELECT ... FOR UPDATE` behavior.

After the shared key is held, Agent rechecks durable CapabilityInvocation
existence. An already-owned R6 id is rejected before the AgentToolCall insert.

### SqlCapabilityInvocationStore.create

`SqlCapabilityInvocationStore.create()` now acquires the same shared
invocation-id authority before inserting the R6 row.

After the lock is held it:

1. rejects an already-existing CapabilityInvocation;
2. reads durable AgentToolCall bindings for the invocation id;
3. rejects ambiguous multiple Agent bindings;
4. if one Agent binding exists, requires exact
   `execution_id + tool_call_id + capability_id` identity agreement;
5. inserts the R6 CapabilityInvocation only after that recheck.

This preserves the canonical Agent-first then R6-create flow when identity
matches, while rejecting an unrelated R6 writer that races on the same
invocation id.

CapabilityInvocation lifecycle remains R6-owned. Repair C changes only
serialization and durable identity admission before initial create.

## 4. Existing-row GC fence remains intact

The existing Repair-B behavior remains:

- SQLite existing CapabilityInvocation row uses semantic no-op UPDATE;
- row-locking dialects use `SELECT ... FOR UPDATE`;
- the new key authority is acquired first, so GC, Agent fresh writes, and R6
  create serialize on the same invocation-id namespace.

No R6 state transition, revision transition, retry policy, remote outcome
policy, or attempt lifecycle is transferred to R11.

## 5. Concurrency evidence

SQLite integration regressions freeze both directions of the absent-key race:

1. an absent invocation-id lock is held by an Agent-side owner; a conflicting
   R6 create blocks, then after AgentToolCall commit wakes and fails closed on
   durable identity mismatch;
2. an absent invocation-id lock is held by an R6-side owner; Agent fresh
   save_tool_call blocks, then after the R6 row commits wakes and rejects the
   already-owned invocation id.

Architecture evidence additionally freezes PostgreSQL
`pg_advisory_xact_lock`, stable SHA-256 key derivation, SQLite absent-key
writer locking, and use of the shared fence by
`SqlCapabilityInvocationStore.create`.

## 6. Scope and ownership boundaries

Repair C does not:

- implement or merge the parked R11-F1-C destructive executor;
- change Task/checkpoint Repair-B semantics;
- change schema or migrations;
- change CapabilityInvocation lifecycle/state authority;
- add R12 lease/recovery semantics;
- change ClientInvocationLedger ownership;
- change CAS/CTX lifecycle or retention authority.

```text
P1-R11-F1C-ABSENT-INVOCATION-FENCE-2 must land
+ post-merge canonical-main Architecture GREEN/GREEN
before PR #82 may be refreshed/re-anchored and re-audited.
```

Repair C is production-affecting and therefore requires separate explicit user
merge confirmation after exact-head CI and independent FINAL GREEN.
