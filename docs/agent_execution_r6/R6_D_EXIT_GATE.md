# R6-D Durable ClientInvocationLedger / Client Restart Reconciliation

**Baseline:** `43ce4c66f39501e4cc38c748177a63c9ad32613c`

**Dependencies:** R6-A COMPLETE, R6-B COMPLETE, R6-C COMPLETE

**Status:** COMPLETE

## Scope

```text
D1 SQLite ClientInvocationLedger
D2 (client_id, principal_id, invocation_id) partition
D3 immutable capability/version/fingerprint/idempotency snapshot
D4 PREPARED -> RUNNING -> TERMINAL write ordering
D5 TERMINAL exact recovery after client process restart
D6 RUNNING-after-restart -> UNKNOWN
D7 PREPARED restart remains explicit and never fabricated as terminal
D8 fingerprint/idempotency conflict fail-closed
D9 terminal-only TTL/GC; RUNNING crash evidence is retained
D10 ClientRuntime wires ledger to stable installation identity
```

## Correctness-sensitive ordering

```text
accept invocation
    ->
persist PREPARED
    ->
policy/input/HITL
    ->
persist RUNNING
    ->
call target
    ->
derive terminal outcome
    ->
persist TERMINAL
    ->
send terminal frame
```

`RUNNING` is persisted immediately before the target call, not merely before
queueing the worker.

Therefore:

```text
PREPARED after restart
    target was not entered by this ledger generation

RUNNING after restart
    target may have produced an effect
    -> UNKNOWN

TERMINAL after restart
    exact stored terminal payload is authoritative
```

## Fail-safe rules

```text
RUNNING + NON_IDEMPOTENT
    never auto-executes from a duplicate capability.invoke

RUNNING + UNKNOWN
    never auto-executes from a duplicate capability.invoke

reconcile never executes a tool

same invocation_id + different fingerprint
    -> CONFLICT

different principal or client installation
    cannot read/replay another ledger partition
```

R6-D deliberately does not reinterpret `UNKNOWN` as replay permission.
Controlled replay policy remains server-owned.

## Persistence location

Default:

```text
Windows:
    %LOCALAPPDATA%\AssistantClient\client-invocations.sqlite3

Linux:
    $XDG_STATE_HOME/AssistantClient/client-invocations.sqlite3
```

Override:

```text
ASSISTANT_CLIENT_INVOCATION_LEDGER_PATH
```

When `ClientRuntime` uses an injected/persistent `InstallationIdentityStore`,
the default ledger is created beside that installation identity file so both
stable `client_id` and durable invocation history survive process restart.

## Focused gate

```powershell
py -m pytest -q `
  cl/tests/test_r6_d_client_invocation_ledger.py `
  se/tests/architecture/test_r6_d_reconciliation_contract.py `
  cl/tests/test_r6_c_reconciliation_contract.py `
  cl/tests/test_realtime_dispatcher_contract.py `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py
```

## Regression gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_phase6_4_remote_client_driver.py `
  se/tests/architecture/test_phase6_6_compatibility_migration.py `
  se/tests/architecture/test_phase6_3_realtime_multiplex.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Exit gate

```text
An unsafe remote side-effect result is not forgotten merely because the
client process restarted.

If the client crashed after entering the target but before durably recording
a terminal result, reconciliation reports UNKNOWN and does not authorize a
blind duplicate execution.
```

## Completion evidence

```text
Windows SQLite ownership regression:
    9 passed in 1.49s

Focused R6-D gate:
    31 passed in 4.96s

R6 compatibility/regression gate:
    26 passed in 4.21s

Repository-wide gate:
    637 passed
    9 warnings
    103.68s
```

The nine repository-wide warnings are existing dependency/configuration
deprecations and are not R6-D correctness failures.

One transport cleanup diagnostic was observed during the focused gate:

```text
Future exception was never retrieved
RemoteConnectionLost(...)
```

This originates from the existing realtime send-failure path after a pending
future is rejected.  It does not invalidate the durable client ledger exit
gate, but it is a mandatory R6-E preflight item because R6-E owns canonical
transport fault injection and async task/future ownership validation.

## Phase status

```text
R5      COMPLETE
R6-A    COMPLETE
R6-B    COMPLETE
R6-C    COMPLETE
R6-D    COMPLETE
R6-E    ACTIVE
R6      NOT COMPLETE
```

