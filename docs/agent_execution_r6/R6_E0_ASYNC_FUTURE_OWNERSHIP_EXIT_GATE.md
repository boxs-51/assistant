# R6-E0 — Realtime Pending Future Ownership

**Phase:** R6-E preflight

**Status:** COMPLETE

## Audit finding

`RealtimeMultiplexer.invoke()` and `reconcile()` create one correlation Future
through:

```text
ConnectionMultiplexer.register()
```

The pre-E0 send-failure path then did:

```text
socket.send_json raises
    ->
ConnectionMultiplexer.reject(future, RemoteConnectionLost)
    ->
RealtimeMultiplexer raises RemoteConnectionLost directly
```

This creates two exception-delivery paths for one failure:

```text
1. direct raise to the caller
2. exception stored in the Future
```

Only path 1 has an awaiter.  Path 2 can therefore produce:

```text
Future exception was never retrieved
```

## Ownership decision

`ConnectionMultiplexer.reject()` remains correct and unchanged for inbound
remote terminal/error/disconnect delivery when the registered Future is the
authoritative exception-delivery path.

For a local `send_json()` failure, however, `RealtimeMultiplexer` is already
delivering `RemoteConnectionLost` directly to its caller.

Therefore E0 changes only the direct-failure owner:

```text
send failure
    ->
remove/cancel local pending correlation
    ->
drain a concurrently completed Future if necessary
    ->
raise RemoteConnectionLost directly
```

No remote-execution certainty is inferred by local Future cancellation.

```text
local Future cancelled
    !=
remote side effect rolled back
```

R6-B `OUTCOME_UNKNOWN` semantics remain unchanged.

## Additional ownership hole found by audit

The same `asyncio.shield(future)` boundary also allowed caller cancellation
while waiting to leave the underlying correlation Future pending.

E0 freezes:

```text
invoke caller cancellation
    -> best-effort capability.cancel
    -> deterministic local pending cleanup
    -> re-raise CancelledError

reconcile caller cancellation
    -> deterministic local pending cleanup
    -> NO capability.cancel
    -> re-raise CancelledError
```

Reconciliation remains query-only.

## Timeout semantics

```text
invoke timeout
    -> best-effort remote capability.cancel
    -> deterministic local Future cleanup
    -> preserve TimeoutError

reconcile timeout
    -> local Future cleanup only
    -> preserve TimeoutError
```

A secondary transport failure while sending the cancellation frame must not
replace the caller's original timeout/cancellation.

## Required regression coverage

```text
invoke send failure:
    no unobserved Future exception
    pending_count == 0

reconcile send failure:
    no unobserved Future exception
    pending_count == 0

send failure racing connection fail:
    rejected Future is drained
    no unobserved Future exception

invoke caller cancellation:
    pending_count == 0
    one capability.cancel frame

reconcile caller cancellation:
    pending_count == 0
    no capability.cancel frame
```

## Focused gate

```powershell
py -m pytest -q `
  se/tests/architecture/test_r6_e0_realtime_future_ownership.py `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_phase6_3_realtime_multiplex.py `
  se/tests/architecture/test_phase6_4_remote_client_driver.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Exit gate

R6-E0 is complete only when:

```text
no focused or broad test failure
no "Future exception was never retrieved" diagnostic
pending correlation count returns to zero for send failure, timeout and caller
cancellation
reconcile cancellation/timeout never emits capability.cancel
```

## Completion evidence

```text
Dedicated E0 gate:
    5 passed in 0.68s

Focused transport/R6 regression gate:
    33 passed in 2.72s

Repository-wide gate:
    642 passed
    9 warnings
    98.12s
```

No:

```text
Future exception was never retrieved
```

diagnostic was emitted by the focused or broad gate.

The nine broad-gate warnings remain dependency/configuration deprecations and
are not E0 correctness failures.

## Exit decision

The E0 async ownership gate is satisfied:

```text
send failure
timeout
caller cancellation
disconnect/send race
```

all return local correlation ownership to zero without leaving an
exception-bearing Future unobserved.

R6-E1 through R6-E11 real-TCP fault-injection harness work may now begin.

