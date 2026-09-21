# R7-E Atomic Continuation Audit — Existing CapabilityInvocation, New Attempt

## Baseline and decision

- Repository: `boxs-51/assistant`
- R7-D closure baseline: `8fd48c19d2a3e2d66b807fc981dd3cff0abb0490`
- Verified R7-E code HEAD: `9dec364d7497fd4df5e19041128b04a7c71d70b7`
- Branch: `r7-e-existing-invocation-continuation`
- PR: #3
- Status: **CLOSED / VERIFIED**
- Schema migration: **not required**

R7-E keeps one durable logical `CapabilityInvocation` and creates a new
`CapabilityInvocationAttempt` for an execution-safe continuation. It does not
recreate the invocation and does not grant AgentExecution resume authority.

## Ownership boundary

R7-E owns:

```text
CapabilityInvocation
CapabilityInvocationAttempt
R6 remote-outcome certainty
target K2 capability binding
same-invocation continuation execution
```

R7-E does not own:

```text
ResumeClaim
AgentExecution WAITING -> RUNNING
TaskBudget resume reacquire
AgentExecution K2 binding
execution.resume accepted ACK
AgentToolResult commitment
```

Those remain R7-F/G concerns.

## Public runtime contract

```python
async def continue_invocation(
    invocation_id: str,
    *,
    target_connection_id: str | None,
    mode: ExistingInvocationContinuationMode,
    expected_revision: int,
    expected_request_fingerprint: str,
    cancellation_event: asyncio.Event | None = None,
) -> CapabilityResult:
    ...
```

Continuation modes:

```text
DISPATCH_NOT_DISPATCHED
REPLAY_SAFE
```

The method loads the existing durable row. It never calls
`CapabilityInvocationLifecycle.create()` and never accepts replacement
arguments, capability identity, version, execution lineage, or tool_call_id
from the caller.

## Semantic fences

Before any attempt row is created, continuation requires:

```text
current revision == expected_revision
durable request_fingerprint == expected_request_fingerprint
recomputed fingerprint(durable capability/version/arguments)
    == durable request_fingerprint

execution_id exists
tool_call_id exists

state == WAITING
wait_reason == CONNECTION
```

Current capability definition must still match the durable invocation on:

```text
version
kind
execution_mode
idempotency
```

Any drift is a semantic conflict.

## Mode matrix

### DISPATCH_NOT_DISPATCHED

Allowed only when:

```text
remote_outcome_state == NOT_DISPATCHED
```

It is safe regardless of idempotency classification because R6 has durable
proof that the prior attempt did not cross the send boundary.

### REPLAY_SAFE

Allowed only when:

```text
remote_outcome_state in {IN_FLIGHT, OUTCOME_UNKNOWN}
AND
idempotency in {IDEMPOTENT, DEDUPLICATED}
```

`NON_IDEMPOTENT` and `UNKNOWN` never replay an ambiguous outcome.

## K2 target authority

R7-E requires a new active connection generation:

```text
K2 != prior invocation.connection_id
K2 is ACTIVE/usable
K2.user_id == invocation.owner_user_id
K2.client_id == invocation.origin_client_id
```

Exactly one matching routable implementation must exist with:

```text
location == CLIENT
owner_type == CLIENT
driver_kind == REMOTE_CLIENT
owner_id == invocation.owner_user_id
connection_id == K2
metadata.client_id == invocation.origin_client_id
version == invocation.capability_version
```

There is no synthetic Identity and no server fallback in R7-E continuation.

## Attempt-history invariant

For a WAITING invocation with `attempt=N`:

```text
durable attempt numbers == [1, 2, ..., N]
every prior attempt is terminal
```

A missing, duplicated, non-contiguous, RUNNING, or DISPATCHING prior attempt is
an integrity conflict. This is checked in the runtime and again inside the
atomic store begin operation.

## Atomic SQL design

### Transaction A — begin continuation attempt

Input:

```text
invocation_id
expected_revision = R
current attempt = N
target implementation / K2
continuation mode
```

One SQL UoW performs:

```text
1. CAS CapabilityInvocation:
       invocation_id == I
       revision == R
       state == WAITING
       attempt == N

   update:
       state = DISPATCHING
       wait_reason = NULL
       attempt = N+1
       max_attempts = max(old, N+1)
       implementation_id = target
       driver_kind = REMOTE_CLIENT
       connection_id = K2
       revision = R+1

2. Read durable attempt history in the same transaction.

3. Require:
       attempt numbers == [1..N]
       every prior attempt terminal

4. INSERT CapabilityInvocationAttempt:
       same invocation_id I
       attempt_number = N+1
       state = DISPATCHING
       implementation_id = target
       connection_id = K2
       metadata.continuation_mode
       metadata.source_revision = R
       metadata.source_remote_outcome_state

5. COMMIT
```

Any CAS miss, history mismatch, or uniqueness failure rolls back both the
invocation update and attempt insertion.

### Transaction B — start continuation attempt

Before driver execution, one UoW performs:

```text
CapabilityInvocation:
    DISPATCHING@R+1 -> RUNNING@R+2

CapabilityInvocationAttempt N+1:
    DISPATCHING -> RUNNING
```

Both updates commit together. If the attempt row is missing or does not match
the invocation attempt number, the invocation RUNNING update is rolled back.

This prevents a durable state where the logical invocation says RUNNING while
the matching continuation attempt remains DISPATCHING.

## Concurrency proof

Two workers starting from the same snapshot:

```text
worker A: continue(I, expected_revision=R)
worker B: continue(I, expected_revision=R)
```

produce:

```text
exactly one CAS winner
one logical CapabilityInvocation row
one new attempt N+1
no attempt N+2
no duplicate attempt N+1
no orphan attempt row
loser returns continuation stale/conflict
```

The SQL uniqueness constraint is defense-in-depth; correctness is established
by invocation revision/state/attempt CAS plus same-UoW attempt insertion.

## Runtime execution boundary

After the atomic begin/start transitions, R7-E uses the shared capability
attempt executor and the normal `RemoteClientDriver`.

For continuation:

```text
allow_internal_retry = False
```

Therefore one `continue_invocation()` call authorizes exactly one new attempt.
A second disconnect/error returns durable WAITING/error state and requires a
new reconciliation/plan; it does not silently choose another implementation or
open attempt N+2.

## Remote-outcome preservation

### DISPATCH_NOT_DISPATCHED

```text
before send: NOT_DISPATCHED
dispatch boundary: -> IN_FLIGHT
terminal: -> TERMINAL_COMMITTED
pre-send connection rejection: -> NOT_DISPATCHED
```

### REPLAY_SAFE

Existing ambiguity is never downgraded:

```text
IN_FLIGHT / OUTCOME_UNKNOWN
    remains uncertain before replay terminal result
```

A pre-send failure during a replay does not rewrite prior uncertainty to
`NOT_DISPATCHED`.

## Client crash-ledger behavior

The client ledger stable key remains:

```text
(client_id, principal_id, invocation_id)
```

with semantic checks for capability/version/fingerprint/idempotency.

Durable `TERMINAL` returns the exact previous terminal outcome without a
second local effect.

Durable `RUNNING` after process restart behaves as:

```text
NON_IDEMPOTENT / UNKNOWN
    -> duplicate invoke blocked

IDEMPOTENT / DEDUPLICATED
    -> same invocation may re-enter local execution
    -> no duplicate mark_running transition
    -> terminal commits into the same ledger row
```

This closes the R6 reconciliation `UNKNOWN/RUNNING` -> R7-D
`REPLAY_SAFE` -> R7-E same-ID replay path.

## Failure windows

A process crash after Transaction A but before Transaction B leaves exactly:

```text
one CapabilityInvocation in DISPATCHING
one attempt N+1 in DISPATCHING
no remote send
```

R7-E intentionally does not invent an Agent recovery claim around this window.
Post-claim recovery and resume-ticket semantics remain R7-G/H scope.

## Non-goal verification

PR #3 does not modify Agent resume transport/runtime files. In particular it
does not wire `continue_invocation()` into `execution.resume`.

No R7-E change creates or consumes a ResumeClaim, changes AgentExecution
WAITING->RUNNING, reacquires TaskBudget, or emits `execution.resume.accepted`.
