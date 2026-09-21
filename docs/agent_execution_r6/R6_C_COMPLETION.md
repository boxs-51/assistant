# R6-C Completion — Reconciliation Protocol / Same-Process Client Replay

**Status:** COMPLETE

**Repository baseline:** `43ce4c66f39501e4cc38c748177a63c9ad32613c`

## Delivered contract

R6-C introduces an explicit query-only reconciliation protocol:

```text
SE -> CL
    capability.reconcile

CL -> SE
    capability.reconciliation
```

The query does not grant execution permission and is distinct from:

```text
capability.invoke
```

The client can report:

```text
RUNNING
TERMINAL
NOT_FOUND
CONFLICT
```

with `UNKNOWN` reserved for the durable client restart authority introduced by
R6-D.

R6-C also enforces:

```text
same authenticated user
same stable client installation
same invocation_id
same capability_id
same capability_version
same request_fingerprint
```

before an old remote invocation can be reconciled on a new connection
generation.

## Terminal recovery

When the same client process retained the terminal outcome after result-send
loss, the new connection generation can return that exact outcome without
executing the capability a second time.

The server commits the recovered terminal into the existing durable
`CapabilityInvocation`:

```text
WAITING
    ->
COMPLETED / FAILED / CANCELLED

remote_outcome_state
    ->
TERMINAL_COMMITTED
```

No new logical invocation ID is allocated.

## Evidence

```text
compatibility regression:
    6 passed in 1.44s

focused R6-C:
    41 passed in 3.99s

repository-wide:
    627 passed, 9 warnings in 119.18s
```

No failing test remains.

## Exit decision

The R6-C exit gate is satisfied:

```text
same-process result-send loss can be recovered across a connection generation
change without duplicate capability execution.
```

## Next phase

R6-D owns the missing process-restart authority:

```text
durable SQLite ClientInvocationLedger
PREPARED -> RUNNING -> TERMINAL ordering
exact terminal replay after CL restart
RUNNING-after-crash -> UNKNOWN
principal/client partitioning
semantic fingerprint conflict rejection
safe GC semantics
NON_IDEMPOTENT/UNKNOWN fail-safe behavior
```

R6-E remains the canonical real-TCP fault-injection exit gate after R6-D.

