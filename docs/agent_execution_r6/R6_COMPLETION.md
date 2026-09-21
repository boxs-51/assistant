# R6 Completion — Remote Invocation Reconciliation / Idempotency

**Status:** COMPLETE

## Phase objective

R6 existed to solve remote side-effect ambiguity before general durable resume,
retry and branching.

Frozen exit gate:

```text
No automatic duplicate side effect after connection loss.
```

That exit gate is satisfied.

## Completed slices

```text
R6-A COMPLETE
    domain/persistence reconciliation contracts
    idempotency declaration
    semantic request fingerprint
    RemoteOutcomeState

R6-B COMPLETE
    outcome-safe routing/fallback
    NOT_DISPATCHED vs OUTCOME_UNKNOWN
    replay-safe vs replay-unsafe behavior

R6-C COMPLETE
    capability.reconcile / capability.reconciliation protocol
    server reconciliation authority
    same-process recovery

R6-D COMPLETE
    durable ClientInvocationLedger
    PREPARED -> RUNNING -> TERMINAL ordering
    client restart recovery
    principal/client partition
    Windows SQLite handle ownership

R6-E0 COMPLETE
    pending Future ownership
    send-failure/cancel/timeout cleanup

R6-E COMPLETE
    canonical real-TCP E1->E11 fault harness
    reconciliation correlation namespace isolation
    final broad regression evidence
```

## Final evidence

```text
R6-E focused correlation/E11:
    26 passed
    repeated successfully

Canonical real-TCP E1->E11:
    12 passed

Transport/R6 regression:
    50 passed

Repository-wide:
    660 passed
    9 warnings
```

No async ownership diagnostic remained in the final gates.

## Final invariant matrix

```text
Stable invocation identity                 PASS
Stable request fingerprint                 PASS
Capability idempotency declaration         PASS
Connection hard affinity per remote attempt PASS
connection_id not global Agent lock        PASS
NOT_DISPATCHED proof boundary               PASS
OUTCOME_UNKNOWN after ambiguous dispatch    PASS
NON_IDEMPOTENT unknown replay blocked       PASS
UNKNOWN unknown replay blocked              PASS
IDEMPOTENT controlled replay                PASS
DEDUPLICATED stable-key replay              PASS
Durable client TERMINAL recovery            PASS
Durable client RUNNING -> UNKNOWN           PASS
Principal/client reconciliation auth        PASS
Semantic conflict fail-closed               PASS
Late old-connection terminal isolation      PASS
Timeout/cancel not rollback proof           PASS
Execution/reconciliation correlation split  PASS
Async Future ownership                      PASS
Real TCP WebSocket evidence                 PASS
Broad regression                            PASS
```

## P0/P1 audit result

```text
P0 remaining against R6 contract: 0
P1 remaining against R6 contract: 0
```

Operational/persistence hardening that does not violate the R6 safety invariant
is carried forward to later roadmap phases rather than extending R6.

## R7 handoff

R7 is unblocked.

R7 owns:

```text
durable checkpoint persistence
ResumeClaim
stale checkpoint validation
WAITING -> RUNNING CAS
resume authorization
runtime/supervisor handoff
server restart while WAITING
```

R7 must not reopen or bypass R6 replay safety.

For any pending remote invocation:

```text
durable terminal
    -> reuse exact terminal

known NOT_DISPATCHED
    -> R6 policy may authorize dispatch

OUTCOME_UNKNOWN
    -> reconcile first

OUTCOME_UNKNOWN + NON_IDEMPOTENT/UNKNOWN
    -> no blind replay
```

R6 is now a prerequisite contract for R7, not work to be reimplemented inside
R7.
