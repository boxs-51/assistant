# R6-D Completion — Durable ClientInvocationLedger / Client Restart Reconciliation

**Status:** COMPLETE

**Repository transition baseline:** `bc91a1987d49af63e175d6b01ed468594631ad1a`

## Delivered authority

R6-D introduces a durable client-side SQLite authority keyed by:

```text
(client_id, principal_id, invocation_id)
```

with immutable request identity:

```text
capability_id
capability_version
request_fingerprint
idempotency
```

and correctness-sensitive lifecycle:

```text
PREPARED
    ->
RUNNING
    ->
TERMINAL
```

## Ordering guarantee

The target call boundary is:

```text
persist PREPARED
    ->
validate / policy / HITL
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

Therefore process restart semantics are deterministic:

```text
TERMINAL
    -> exact durable terminal replay

RUNNING
    -> outcome may have occurred
    -> reconciliation returns UNKNOWN
    -> no blind duplicate execution

PREPARED
    -> target call was not entered by the recorded generation
    -> state remains explicit rather than fabricated as terminal
```

## Security and identity boundaries

The durable ledger is partitioned by both:

```text
stable client installation
authenticated principal
```

so a record from another:

```text
client_id
principal_id
```

cannot be read/replayed through the current dispatcher identity.

Same `invocation_id` with different request semantics fails closed.

## GC boundary

TTL/GC may delete expired:

```text
TERMINAL
```

rows.

It does not erase:

```text
RUNNING
```

crash evidence, because that evidence is necessary to prevent unsafe
NON_IDEMPOTENT/UNKNOWN replay.

## Windows resource ownership correction

R6-D testing exposed a real Windows resource-ownership defect:

```text
PermissionError [WinError 32]
client-invocations.sqlite3 is being used by another process
```

The cause was relying on:

```python
with sqlite3.Connection(...)
```

as though it closed the SQLite connection.  The sqlite3 context manager owns
transaction commit/rollback semantics but does not close the connection.

R6-D now owns each SQLite handle explicitly:

```text
open
    ->
operation
    ->
commit / rollback
    ->
close in finally
```

and has a Windows-relevant regression test that unlinks the database after
ledger operations.

## Test evidence

```text
Windows SQLite ownership:
    9 passed in 1.49s

Focused R6-D:
    31 passed in 4.96s

Compatibility/regression:
    26 passed in 4.21s

Repository-wide:
    637 passed, 9 warnings in 103.68s
```

No repository-wide test failure remains.

## Known carry-over into R6-E

The focused fault path emitted:

```text
Future exception was never retrieved
RemoteConnectionLost(...)
```

when `RealtimeMultiplexer.invoke()` rejected a pending future after
`socket.send_json()` failed and then raised the transport failure directly.

This is not a ClientInvocationLedger correctness failure, so it does not block
R6-D completion.

It is nevertheless a mandatory R6-E preflight requirement:

```text
every pending Future created by the realtime multiplexer must have deterministic
ownership and must be resolved/cancelled/drained without an unobserved
exception.
```

## Exit decision

R6-D exit gate is satisfied:

```text
unsafe client-side remote outcome evidence survives client process restart,
and RUNNING-after-crash cannot silently become permission to re-execute a
NON_IDEMPOTENT/UNKNOWN capability.
```

## Next phase

```text
R6-E — canonical real TCP WebSocket reconciliation/fault-injection gate
```

R6-E must prove the combined R6-A through R6-D contract using real transport,
real client receiver/dispatcher, durable ledger restart scenarios and no
test-side terminal injection.
