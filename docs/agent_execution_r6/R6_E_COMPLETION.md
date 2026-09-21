# R6-E Completion — Canonical Real-TCP Reconciliation / Fault Injection

**Status:** COMPLETE

## Exit decision

R6-E is complete.

The canonical real-TCP harness proves the R6 remote invocation safety contract
through the actual gateway/client WebSocket path rather than a FakeSocket or
manual inbound injection.

The governing invariant is satisfied:

```text
No automatic duplicate side effect after connection loss.
```

## Canonical execution path exercised

```text
CapabilityRuntime
    ->
RemoteClientDriver
    ->
RealtimeMultiplexer
    ->
real localhost TCP WebSocket
    ->
FastAPI / Uvicorn gateway
    ->
GatewayRealtimeClient receiver thread
    ->
ClientCapabilityRuntime
    ->
CapabilityDispatcher
    ->
real local Python capability
    ->
ClientInvocationLedger
```

## Canonical E1 -> E11 evidence

The dedicated real-TCP module collected 12 pytest cases because E9 contains
separate foreign-principal and foreign-client cases.

```text
E1  disconnect before dispatch
E2  disconnect while NON_IDEMPOTENT target is RUNNING
E3  side effect finished / result lost / same-process reconnect
E4  fresh client process-generation reopens durable TERMINAL ledger
E5  restart from durable RUNNING -> UNKNOWN and no re-execution
E6  IDEMPOTENT controlled replay with stable invocation_id
E7  DEDUPLICATED replay with stable invocation_id / single external effect
E8  semantic fingerprint conflict
E9  foreign principal and foreign client rejection
E10 late K1 terminal cannot mutate K2 committed reconciliation
E11 timeout/cancel is not rollback proof and cannot resurrect terminal server state
```

Evidence:

```text
canonical E1->E11:
    12 passed in 4.86s

transport/R6 regression:
    50 passed in 6.65s

repository-wide:
    660 passed
    9 warnings
    78.36s
```

## Correlation race discovered and closed

The E11 suite exposed a production race that was not visible when the
canonical test ran in isolation:

```text
capability.cancelled(invocation_id=X)
    raced
capability.reconcile(invocation_id=X)
```

Execution terminal correlation and reconciliation correlation previously shared
one pending map keyed only by `invocation_id`.

R6-E now maintains independent correlation ownership:

```text
RealtimeMultiplexer.multiplexer
    -> execution terminal/progress correlation

RealtimeMultiplexer.reconciliation_multiplexer
    -> capability.reconciliation correlation
```

The focused race gate was run twice:

```text
26 passed in 2.59s
26 passed in 2.54s
```

and the complete canonical and broad gates remained green.

## Async ownership

The final test output contains no:

```text
Future exception was never retrieved
Task was destroyed but it is pending
```

diagnostic.

E0 therefore remains satisfied after the reconciliation namespace split.

## R6 safety conclusions

The following are now demonstrated:

```text
NOT_DISPATCHED
    -> retry/fallback may be considered

possible dispatch + transport loss
    -> OUTCOME_UNKNOWN

NON_IDEMPOTENT / UNKNOWN + OUTCOME_UNKNOWN
    -> no automatic replay

durable TERMINAL
    -> exact outcome reuse

durable RUNNING after restart
    -> UNKNOWN
    -> no blind duplicate execution

same invocation_id + changed semantic fingerprint
    -> REMOTE_INVOCATION_CONFLICT

foreign principal/client
    -> reconciliation denied

late old-generation terminal
    -> cannot overwrite committed new-generation state

timeout/cancel
    -> never treated as rollback proof
```

## Non-blocking hardening carry-over

No P0/P1 remains against the frozen R6 exit gate.

The following are deliberately not used to keep R6 open:

```text
1. explicit SQLite durability tuning/fault injection under disk-full/I/O-error
2. process-kill style client restart harness beyond fresh process-generation +
   reopening the same SQLite authority
3. dependency/configuration deprecation warnings
```

These are resilience/release-hardening concerns appropriate for later
persistence/fault-injection phases, especially R11/R14.

They do not currently create an automatic duplicate side-effect path:
ledger failure paths fail closed before emitting an authoritative durable
terminal, and ambiguous remote outcomes remain UNKNOWN.

## Next phase

```text
R7 — Durable Checkpoint + ResumeClaim + Resume Protocol
```

R7 may now consume R6 reconciliation evidence.

R7 must still obey:

```text
server_continuation_available != replay safety

TERMINAL_COMMITTED
    -> reuse exact result

known NOT_DISPATCHED
    -> dispatch may be authorized

OUTCOME_UNKNOWN + NON_IDEMPOTENT/UNKNOWN
    -> remain WAITING / reconciliation / HITL / fail-safe
```
