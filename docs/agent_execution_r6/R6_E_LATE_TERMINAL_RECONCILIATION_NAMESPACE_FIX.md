# R6-E — Late Execution Terminal vs Reconciliation Correlation Namespace

**Status:** COMPLETE

## Fault discovered by canonical E11

The real-TCP E11 gate exposed this race:

```text
server capability invocation times out
    ->
server sends capability.cancel
    ->
client later emits capability.cancelled

meanwhile

server starts capability.reconcile
for the same logical invocation_id
```

Before this fix both operations used:

```text
ConnectionMultiplexer._pending[invocation_id]
```

The late execution terminal could therefore cancel or resolve the newer
reconciliation waiter before `capability.reconciliation` arrived.

## Correct correlation model

`invocation_id` remains the stable logical remote invocation identity.

It is not, by itself, the correlation namespace. R6 has two message families:

```text
execution:
    capability.result
    capability.error
    capability.cancelled
    capability.progress

reconciliation:
    capability.reconciliation
```

R6-E freezes two independent pending domains:

```text
RealtimeMultiplexer.multiplexer
    -> execution correlation

RealtimeMultiplexer.reconciliation_multiplexer
    -> reconciliation correlation
```

A frame from one family cannot resolve or cancel the other family's waiter.

## Disconnect ownership

A real connection loss owns both domains.

```text
RealtimeMultiplexer.disconnect(connection_id)
    ->
fail execution waiters
    +
fail reconciliation waiters
```

The return value is the combined number of failed pending operations.

## E0 ownership compatibility

Reconciliation timeout, send failure and caller cancellation remove pending
state from the reconciliation namespace only.

They still:

```text
do not emit capability.cancel
do not imply remote rollback
do not leave an exception-bearing Future unobserved
```

Execution timeout/cancellation semantics remain unchanged.

## E11 expected flow

After the namespace fix:

```text
server invocation
    -> TIMED_OUT
    -> OUTCOME_UNKNOWN

client late capability.cancelled
    -> execution namespace has no waiter
    -> ignored for reconciliation

client capability.reconciliation TERMINAL/cancelled
    -> reaches RemoteInvocationReconciliationService

server invocation is not WAITING
    -> recovered terminal cannot resurrect it
    -> REMOTE_RESULT_RECONCILIATION_REQUIRED

server invocation remains TIMED_OUT
```

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_e_reconciliation_correlation_namespace.py `
  se/tests/e2e/test_r6_e_remote_reconciliation_faults.py::test_r6_e11_timeout_is_not_rollback_proof_and_late_terminal_cannot_resurrect `
  se/tests/architecture/test_r6_e0_realtime_future_ownership.py `
  se/tests/architecture/test_phase6_3_realtime_multiplex.py
```

Then rerun:

```text
canonical R6-E1->E11
transport regression
broad repository gate
```

Final evidence:

```text
focused namespace/E11:
    26 passed in 2.59s
    repeated: 26 passed in 2.54s

canonical R6-E1->E11:
    12 passed

transport regression:
    50 passed

broad repository:
    660 passed, 9 warnings
```

The namespace race is closed and R6-E is COMPLETE.

