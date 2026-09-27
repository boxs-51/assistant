# AE-R12-C — Atomic Execution Lease Authority

**Primary authority:** Issue #107  
**Parent candidate:** PR #117 @ `90fcd08dd41689f6593cbbc732bd7c69010af65a`  
**Policy:** Issue #85 v2.5  
**Stage class:** PRODUCTION / ATOMIC LEASE AUTHORITY ONLY  
**Merge authority:** NONE  
**Parent-first integration:** REQUIRED

## 1. Goal

R12-C activates only the atomic authority primitives over the durable R12-B
representation:

```text
owner_instance_id
lease_expires_at
lease_generation
```

R12-C does not scan stale executions, take over expired owners, move executions
to WAITING(RECOVERY), reconcile invocations, or wire lease checks into runtime
provider/tool dispatch.

## 2. Critical revision boundary

Existing generic execution CAS increments:

```text
AgentExecution.revision = expected_revision + 1
```

Lease renewal is periodic distributed ownership maintenance and must not churn
that semantic execution revision.

Therefore acquire / renew / release use dedicated SQL predicates and mutate only
lease fields. A successful lease-only mutation preserves:

- AgentExecution.revision;
- state;
- checkpoint pointer;
- active budget;
- task/branch/parent lineage;
- request/result/context payloads.

## 3. Time authority

Distributed lease authority uses timezone-aware UTC wall-clock datetimes only.

Inputs fail closed when:
- datetime is naive;
- datetime offset is not UTC;
- acquire expiry <= now_utc;
- renew target expiry <= now_utc.

Process-monotonic timestamps are not durable lease authority.

## 4. Acquire

Atomic acquire is allowed only when:

```text
state == RUNNING
owner_instance_id IS NULL
lease_expires_at IS NULL
requested owner is non-empty
requested expiry > now_utc
```

Success:
- owner is set;
- expiry is set;
- generation increments atomically by exactly 1;
- execution revision is unchanged.

Any non-null current owner/expiry pair blocks R12-C acquisition, even if a
caller believes it is expired. Expired-owner takeover belongs to R12-E.

## 5. Renew

Atomic renew requires:

```text
state == RUNNING
owner_instance_id == expected owner
lease_generation == expected generation
lease_expires_at IS NOT NULL
lease_expires_at > now_utc
new_expiry > lease_expires_at
```

Success changes expiry only. Generation and execution revision remain unchanged.

Already expired leases are never resurrected by R12-C renew.

## 6. Release

Atomic release requires:
- RUNNING execution;
- exact owner;
- exact generation;
- current owner/expiry pair present.

Success clears owner + expiry together and retains generation.

Generation is never reset/reused. A later fresh acquire increments it again.

## 7. Active fence predicate

Read-only validation succeeds only when:

```text
state == RUNNING
owner == expected owner
generation == expected generation
expiry > now_utc
```

This predicate exists for later runtime integration but R12-C does not wire it
into provider/tool dispatch or durable commit callsites.

## 8. Conflict model

DurableAgentStore maps predicate loss to lease-specific conflict codes:

```text
LEASE_ACQUIRE_REJECTED
LEASE_RENEW_REJECTED
LEASE_RELEASE_REJECTED
```

Invalid argument shape/time authority raises ValueError before authority is
accepted.

## 9. Exact production scope

```text
se/src/infrastructure/storage/repositories/agent.py
se/src/runtimes/agent/persistence.py
```

No schema/model/migration change is owned in R12-C; those remain R12-B.

## 10. Required invariants

```text
C-I01 competing acquire -> exactly one winner
C-I02 winner generation = previous + 1
C-I03 no steal of any still-owned row
C-I04 renew requires exact owner + generation + unexpired lease
C-I05 stale generation cannot renew
C-I06 old owner cannot release newer owner's lease
C-I07 release retains generation
C-I08 reacquire increments generation again
C-I09 lease-only mutations do not increment execution revision
C-I10 lease-only mutations preserve state/checkpoint/budget/lineage
C-I11 fence fails for wrong owner/stale generation/expired/non-RUNNING
C-I12 no RUNNING -> WAITING(RECOVERY) transition in R12-C
```

## 11. Authority kept CLOSED

R12-C does not implement:
- stale-RUNNING discovery/index/scanner — R12-D;
- expired-owner takeover/recovery ownership — R12-E;
- WAITING(RECOVERY) transition — R12-E;
- invocation reconciliation/recovered activation — R12-F;
- provider/tool pre-dispatch/pre-commit runtime wiring — later release;
- recovery-vs-user-resume arbitration — R12-G;
- final restart/multi-worker fault matrix — R12-G/H.

Especially:

```text
lease expiry != R12-C takeover permission
```

## 12. Exit gate

R12-C may become candidate FINAL GREEN only when:
1. repository methods are single-statement atomic predicates;
2. real SQLite competing acquire has exactly one winner;
3. generation monotonicity is proven across release/reacquire;
4. stale owner/generation cannot renew or release;
5. renew-after-expiry fails closed;
6. exact UTC extension is proven;
7. semantic execution revision remains unchanged;
8. checkpoint/budget/lineage/state remain unchanged;
9. fence predicate matrix passes;
10. no runtime/scanner/recovery authority enters the production diff;
11. Linux + Windows Architecture GREEN;
12. independent audit finds no blocking P0/P1.
