# R6-E — Real TCP WebSocket Reconciliation / Fault-Injection Exit Gate

**Transition baseline:** `bc91a1987d49af63e175d6b01ed468594631ad1a`

**Dependencies:** R6-A COMPLETE, R6-B COMPLETE, R6-C COMPLETE, R6-D COMPLETE

**Status:** COMPLETE

**Preflight:** R6-E0 COMPLETE

## Goal

Prove the complete R6 contract through real network and process-generation
boundaries:

```text
No automatic duplicate side effect after connection loss.
```

The canonical R6-E gate must exercise:

```text
CapabilityRuntime
    ->
RemoteClientDriver
    ->
RealtimeMultiplexer
    ->
REAL TCP WebSocket
    ->
REAL GatewayRealtimeClient receiver thread
    ->
REAL CapabilityRuntime / CapabilityDispatcher
    ->
REAL local capability
    ->
ClientInvocationLedger
```

## E0 — Mandatory async ownership preflight — COMPLETE

Before accepting the R6-E exit gate, remove:

```text
Future exception was never retrieved
```

from the realtime send-failure path.

Observed shape:

```text
socket.send_json raises
    ->
pending future is rejected with RemoteConnectionLost
    ->
invoke raises RemoteConnectionLost directly
    ->
rejected future exception may remain unobserved
```

Required invariant:

```text
creator/owner of every pending asyncio Future/Task
    must
    resolve it,
    await/drain it,
    cancel+drain it,
    or explicitly transfer ownership.
```

Fault-injection tests must not pass while emitting an unobserved Future
exception.

Evidence:

```text
dedicated E0: 5 passed
focused transport/R6: 33 passed
broad repository: 642 passed, 9 warnings
unobserved Future diagnostics: 0
```

## Canonical R6-E scenarios

### E1 — Disconnect before dispatch

Expected:

```text
remote_outcome_state = NOT_DISPATCHED
same invocation may be routed according to normal policy
no reconciliation ambiguity
```

### E2 — Disconnect after dispatch while tool is RUNNING

Expected:

```text
remote_outcome_state = OUTCOME_UNKNOWN
NON_IDEMPOTENT / UNKNOWN
    -> no automatic fallback/replay
```

### E3 — Side effect completes but result-send is lost

Same process reconnect:

```text
tool executes exactly once
terminal retained
new connection generation reconciles same invocation_id
exact terminal returned
```

### E4 — Client process restart after TERMINAL persistence

Expected:

```text
new client process
same stable client_id
same principal
same ledger database
    ->
TERMINAL reconciliation
    ->
exact outcome reuse
    ->
no second side effect
```

### E5 — Client process crash with durable RUNNING

Expected:

```text
ledger state = RUNNING
restart
reconciliation status = UNKNOWN

NON_IDEMPOTENT / UNKNOWN
    -> blocked
    -> no automatic duplicate execution
```

### E6 — IDEMPOTENT controlled replay

Expected:

```text
OUTCOME_UNKNOWN / restart UNKNOWN
idempotency = IDEMPOTENT
policy permits controlled replay
same invocation_id
same request_fingerprint
```

### E7 — DEDUPLICATED stable-invocation replay

Expected:

```text
idempotency = DEDUPLICATED
same invocation_id preserved
dedupe authority observes stable logical key
no duplicate external effect
```

### E8 — Semantic conflict

Expected:

```text
same invocation_id
different arguments/version/fingerprint
    ->
REMOTE_INVOCATION_CONFLICT
    ->
no execution
no terminal replay
```

### E9 — Foreign principal/client reconciliation

Expected:

```text
different user_id
or
different client_id
    ->
rejected
```

### E10 — Late old-connection terminal

Expected:

```text
old connection K1 terminal arrives after K2 reconciliation/commit
    ->
cannot mutate K2 pending transport
    ->
cannot resurrect/overwrite terminal durable invocation
```

### E11 — Cancel/timeout is not rollback proof

Expected:

```text
cancel request sent
or server timeout occurs
    ->
must not infer external side effect did not happen
    ->
must not authorize blind replay
```

## Canonical E2E restrictions

The R6-E authoritative tests must not use:

```text
FakeSocket
manual RealtimeMultiplexer.handle_inbound()
manual capability.result/error/cancelled injection
direct CapabilityDispatcher.dispatch() from the test
process-local terminal cache as the only restart authority
new invocation_id for reconciliation
```

Allowed:

```text
real localhost TCP WebSocket
Uvicorn/FastAPI gateway
real GatewayRealtimeClient receiver thread
real CapabilityRuntime registration
real CapabilityDispatcher
temporary SQLite ledger on disk
controlled socket/process-generation fault hooks
deterministic synchronization Events/barriers
```

## Required assertions

Every scenario must assert as applicable:

```text
tool side-effect count
invocation_id stability
request_fingerprint stability
CapabilityInvocation lifecycle
RemoteOutcomeState
CapabilityInvocationAttempt count/order
ClientInvocationLedger state
reconciliation status
connection generation change
no terminal resurrection
no leaked pending future/task
```

## Focused gate target

R6-E should introduce a dedicated canonical E2E module, for example:

```text
se/tests/e2e/test_r6_e_remote_reconciliation_faults.py
```

and keep existing:

```text
test_phase6_10_true_websocket_agent_client_loop.py
```

as a regression dependency rather than overloading all R6-E scenarios into
the historical Phase 6.10 test file.

## Broad gate

After the dedicated R6-E gate is green:

```powershell
py -m pytest -q se/tests tools cl/tests
```

MCP ownership remains post-R14 and is not an R6-E implementation dependency.

## Completion evidence

```text
focused correlation/E11 gate:
    26 passed in 2.59s
    repeated: 26 passed in 2.54s

canonical real-TCP E1->E11:
    12 passed in 4.86s

transport/R6 regression:
    50 passed in 6.65s

repository-wide:
    660 passed
    9 warnings
    78.36s
```

No unobserved-Future or pending-task diagnostic was emitted by the final
gates.

The canonical run also closed the late execution-terminal vs reconciliation
correlation race by separating execution and reconciliation pending domains.

## R6 exit gate

R6 may become COMPLETE only when:

```text
E0 async ownership is clean
AND
all canonical real-TCP fault scenarios are green
AND
repository-wide regression is green
AND
no scenario authorizes an automatic duplicate side effect after ambiguous
remote delivery
```

## Phase status

```text
R5      COMPLETE
R6-A    COMPLETE
R6-B    COMPLETE
R6-C    COMPLETE
R6-D    COMPLETE
R6-E0   COMPLETE
R6-E    COMPLETE
R6      COMPLETE
```

