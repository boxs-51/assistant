# R6-E0 Completion — Realtime Pending Future Ownership

**Status:** COMPLETE

## Problem closed

The pre-E0 realtime send-failure path could create two delivery paths for one
transport failure:

```text
ConnectionMultiplexer Future
    <- set_exception(RemoteConnectionLost)

RealtimeMultiplexer.invoke/reconcile
    <- raise RemoteConnectionLost directly
```

The direct caller consumed the second path while the first could be dropped
without retrieving its exception, producing:

```text
Future exception was never retrieved
```

## Final ownership contract

For local send failure:

```text
register correlation Future
    ->
send_json attempted
    ->
send fails
    ->
remove/cancel local correlation ownership
    ->
drain a concurrently completed Future if necessary
    ->
raise RemoteConnectionLost directly
```

For inbound remote error/disconnect:

```text
ConnectionMultiplexer.reject/fail_connection
    ->
Future remains the authoritative exception-delivery path
    ->
awaiting invoke/reconcile coroutine consumes it
```

`ConnectionMultiplexer.reject()` therefore remains unchanged.

## Cancellation contract

```text
invoke caller cancellation
    -> best-effort capability.cancel
    -> local pending cleanup
    -> CancelledError preserved

reconcile caller cancellation
    -> local pending cleanup
    -> no capability.cancel
    -> CancelledError preserved
```

Local Future cancellation is transport bookkeeping only:

```text
cancelled local Future
    !=
remote side effect rolled back
```

R6-B `OUTCOME_UNKNOWN` semantics remain intact.

## Evidence

```text
5 passed in 0.68s
33 passed in 2.72s
642 passed, 9 warnings in 98.12s
```

No unobserved-Future diagnostic remains.

## Repository hygiene discovered during E0

The broad run exposed an unrelated tracked test artifact:

```text
.client-identity-test-*/
```

containing:

```text
installation.json
client-invocations.sqlite3
```

This directory is ephemeral test state and must not be version-controlled.
The E0 formal-close patch adds:

```text
.client-identity-test-*/
```

to `.gitignore`.

Any already tracked instance should be removed from the index/repository as
part of the same cleanup commit.

## Next gate

```text
R6-E1 -> R6-E11
```

must now prove the R6 reconciliation model through real localhost TCP
WebSocket transport, real client receiver/dispatcher, durable SQLite ledger,
connection generation changes and client process-generation restart
simulation.
