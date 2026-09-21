# R6-C Reconciliation Protocol / Same-Process Client Replay

**Baseline:** `35b885fbe95d0bfebeddcaef9b200fd74b9f0031`

**Dependencies:** R6-A COMPLETE, R6-B COMPLETE

**Status:** COMPLETE

## Scope

```text
C1 capability.reconcile query
C2 capability.reconciliation response
C3 RUNNING same-process detection
C4 exact TERMINAL replay
C5 NOT_FOUND response
C6 semantic fingerprint CONFLICT
C7 same-principal + same-client-installation authorization
C8 recovered terminal commit into existing CapabilityInvocation
```

## Safety boundary

`capability.reconcile` is query-only:

```text
reconcile != invoke
reconcile does not grant execution permission
reconcile timeout does not send capability.cancel
```

The original logical invocation keeps its original:

```text
invocation_id
capability_id
capability_version
request_fingerprint
```

## Client statuses

```text
RUNNING
    same process still owns the invocation
    do not execute again

TERMINAL
    return exact stored result/error/cancelled payload

NOT_FOUND
    no same-process invocation/outcome is retained

CONFLICT
    invocation_id exists with different request semantics

UNKNOWN
    reserved for R6-D durable restart ledger
```

## Authorization

The new connection must satisfy:

```text
connection.user_id == invocation.owner_user_id
connection.metadata.client_id == invocation.origin_client_id
```

`connection_id` is a rotating generation identifier and is deliberately not
used as the stable reconciliation identity.

## Durable SE terminal recovery

A terminal client reconciliation may move:

```text
CapabilityInvocation WAITING
    ->
COMPLETED / FAILED / CANCELLED
```

and atomically record:

```text
remote_outcome_state = TERMINAL_COMMITTED
```

Terminal CapabilityInvocation states remain immutable after that commit.

## Non-scope

```text
client process restart persistence  -> R6-D
automatic redispatch after NOT_FOUND/UNKNOWN -> later R6 policy integration
Agent ResumeClaim / WAITING->RUNNING ownership -> R7
```

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  se/tests/architecture/test_phase6_3_realtime_multiplex.py `
  cl/tests/test_r6_c_reconciliation_contract.py `
  cl/tests/test_realtime_dispatcher_contract.py `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Exit gate

Same-process result-send loss must be recoverable on a new connection
generation without executing the capability a second time.

## Completion evidence

```text
Compatibility regression gate:
    6 passed in 1.44s

Focused R6-C gate:
    41 passed in 3.99s

Repository-wide gate:
    627 passed
    9 warnings
    119.18s
```

The broad-gate warnings are existing dependency/configuration deprecations.
No R6-C correctness failure remains.

## Phase status

```text
R5      COMPLETE
R6-A    COMPLETE
R6-B    COMPLETE
R6-C    COMPLETE
R6-D    ACTIVE
R6-E    PENDING
R6      NOT COMPLETE
```
