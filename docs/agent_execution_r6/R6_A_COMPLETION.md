# R6-A Completion — Domain / Persistence Reconciliation Contracts

**Status:** COMPLETE

**Implementation commit baseline:** `6770ab09badf4c3f7ab3ccb0abab1bd065047eb3`

## Delivered

R6-A established the durable representation required before changing remote
dispatch behavior:

```text
CapabilityIdempotency:
    IDEMPOTENT
    DEDUPLICATED
    NON_IDEMPOTENT
    UNKNOWN

RemoteOutcomeState:
    NOT_DISPATCHED
    IN_FLIGHT
    OUTCOME_UNKNOWN
    TERMINAL_COMMITTED
```

`UNKNOWN` is the fail-safe default for legacy capabilities.

`CapabilityInvocation` now snapshots:

```text
capability_version
idempotency
request_fingerprint
owner_user_id
origin_client_id
remote_outcome_state
```

The semantic request fingerprint is:

```text
SHA256(
    canonical JSON {
        capability_id,
        capability_version,
        arguments
    }
)
```

The durable SQL store now supports canonical invocation readback and ordered
attempt lookup in addition to existing revision/CAS writes.

Migration:

```text
12a_r6_remote_reconciliation
down_revision = 11a_r5_task_budget
```

Historical rows receive only:

```text
idempotency = UNKNOWN
```

R6-A deliberately does not invent historical:

```text
remote delivery certainty
request fingerprint
origin client identity
owner provenance
```

## Stable R6 error contracts introduced

```text
REMOTE_OUTCOME_UNKNOWN
REMOTE_RESULT_RECONCILIATION_REQUIRED
REMOTE_INVOCATION_CONFLICT
```

## Evidence

Focused:

```text
12 passed
2 warnings
12.21s
```

The first repository-wide run produced:

```text
608 passed
1 failed
9 warnings
```

The sole failure was a pre-existing timing-sensitive assertion in the true
WebSocket Phase 6.10 E2E.  The remote tool flow itself completed through the
second inference; only the assertion that two independently rebuilt temporal
prompts must have different wall-clock strings failed.

After injecting a deterministic advancing temporal clock, the same E2E file
passed four consecutive runs:

```text
2 passed in 6.56s
2 passed in 5.03s
2 passed in 5.27s
2 passed in 6.22s
```

The next R6-B broad gate will provide the next full repository-wide regression
confirmation.

## Boundary

R6-A does not:

```text
change remote dispatch/fallback
drive live RemoteOutcomeState transitions
add reconciliation WebSocket messages
add ClientInvocationLedger
implement R7 ResumeClaim
```

Those responsibilities remain:

```text
R6-B live certainty + safe fallback
R6-C reconciliation protocol
R6-D durable client ledger
R6-E true-TCP fault-injection exit gate
R7   durable resume
```

## Phase status

```text
R5      COMPLETE
R6-A    COMPLETE
R6-B    ACTIVE
R6-C    PENDING
R6-D    PENDING
R6-E    PENDING
R6      NOT COMPLETE
```
