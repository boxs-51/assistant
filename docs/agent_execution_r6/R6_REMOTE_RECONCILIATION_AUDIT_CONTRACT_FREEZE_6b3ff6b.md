# R6 Audit & Contract Freeze — Remote Invocation Reconciliation / Idempotency

**Repository:** `boxs-51/assistant`  
**Audited HEAD:** `6b3ff6bb0edfedb985401fa86ea7a9924e3cf73d`  
**Commit:** `cap nhat cuoi R5`  
**Audit date:** 2026-09-20  
**Previous phase:** R5 COMPLETE  
**Document status:** AUDIT / CONTRACT FREEZE CANDIDATE — REVIEW BEFORE CODE  
**Scope:** R6 only — remote invocation reconciliation and idempotency  
**Explicit non-scope:** R7 ResumeClaim/checkpoint resume, R8 TaskBranch/FORK, R9 retry scheduler, post-R14 MCP ownership integration.

---

# 1. Executive conclusion

R5 is complete. R6 is not a greenfield implementation: the repository already has
most of the transport and durable invocation foundations needed for
reconciliation.

Existing reusable foundations:

```text
SE:
    CapabilityInvocation + Attempt lifecycle
    durable SQL capability_invocations
    revision/CAS
    hard connection affinity
    RealtimeMultiplexer correlation
    RemoteClientDriver
    real TCP WebSocket E2E
    Agent WAITING(CONNECTION) checkpoint path

CL:
    stable client_id
    rotating connection_id generations
    single receiver thread
    CapabilityDispatcher RUNNING dedupe
    in-memory TerminalOutcome replay
    result-send-loss retention inside same process
```

However, the current disconnect/fallback behavior is not safe for an arbitrary
side-effecting remote capability.

The key R6 correctness problem is:

```text
SE sends I1
CL may execute side effect
network disappears
SE does not receive terminal result
```

At that point SE cannot infer:

```text
"tool did not execute"
```

from:

```text
"socket disconnected"
"timeout"
"cancel request was sent"
"server implementation is available"
```

R6 must make that uncertainty explicit and prevent automatic duplicate side
effects.

---

# 2. Authoritative R6 goal

From the main roadmap:

```text
Phase R6 — Remote Invocation Reconciliation / Idempotency

Goal:
    solve remote side-effect ambiguity before general continuation
    and branching

Exit gate:
    no automatic duplicate side effect after connection loss
```

R7 explicitly depends on R6 and states:

```text
server_continuation_available alone is never proof of replay safety
```

Therefore R6 must produce a durable reconciliation decision that R7 can later
consume.

---

# 3. Current remote invocation flow

Today:

```text
AgentRuntime
    |
    v
CapabilityToolExecutionAdapter
    |
    v
CapabilityRuntime
    |
    v
RemoteClientDriver
    |
    v
RealtimeMultiplexer
    |
    v
ConnectionMultiplexer
    |
    v
WebSocket K1
    |
    v
ClientRuntime
    |
    v
CapabilityDispatcher
    |
    v
LocalCapabilityExecutor
```

Normal success is already correct:

```text
capability.invoke(I1)
    ->
CL execute once
    ->
capability.result(I1)
    ->
SE resolve pending future
    ->
CapabilityInvocation COMPLETED
```

The unsafe area begins when the connection disappears before SE commits the
terminal outcome.

---

# 4. What is already correct and should be kept

## 4.1 Stable invocation identity

`invocation_id` already survives the server execution path and is propagated
to the client.

R6 freezes:

```text
same logical remote invocation
    =
same invocation_id
```

A reconnect/reconcile operation must never allocate a replacement
`invocation_id` for the same logical call.

## 4.2 Hard transport affinity

The issued remote attempt is bound to its original `connection_id`.

Cross-connection result injection is already rejected by the
RealtimeMultiplexer.

Keep this invariant.

## 4.3 Durable SE invocation authority

The repository already has:

```text
capability_invocations
capability_invocation_attempts
```

with revision/CAS and immutable lifecycle terminal states.

Do not create a second server-side invocation ledger.

Extend the existing durable invocation authority.

## 4.4 Client same-process duplicate suppression

`CapabilityDispatcher` already guarantees:

```text
duplicate while RUNNING:
    do not execute again

duplicate after TERMINAL:
    replay cached terminal outcome
```

This is the correct R6 same-process foundation.

## 4.5 Client connection generations

`ClientRuntime` already preserves stable:

```text
client_id
```

while rotating:

```text
connection_id
```

on reconnect.

This is exactly the identity split R6 needs.

---

# 5. P0 findings

## P0-R6-1 — connection loss currently permits unsafe automatic fallback

Current `CapabilityRuntime` handles `RemoteConnectionLost` by optionally:

```text
attempt 1 CLIENT fails
    ->
RETRYING
    ->
attempt 2 SERVER
```

with the same invocation ID.

An existing real WebSocket E2E test explicitly proves this fallback.

This is safe only when replay/re-execution is proven safe.

For a side-effecting capability:

```text
CL effect completed
result packet lost
server fallback executes again
```

can duplicate the effect.

### Required R6 correction

Automatic fallback after a remote send has become outcome-unknown is allowed
only when the capability idempotency contract says replay is safe.

---

## P0-R6-2 — server implementation availability is treated as replay safety

`AgentRuntime` currently computes:

```text
server_continuation_available =
    tool_execution.can_continue_server_side(capability_id)
```

`AgentContinuationService.checkpoint_disconnect()` then uses this boolean to
choose:

```text
WAITING
vs
RUNNING
```

This is incorrect for R6.

These are different questions:

```text
Q1: is another implementation routable?
Q2: is replay/re-execution of this logical invocation safe?
```

R6 requires Q2.

### Frozen rule

```text
server_continuation_available != replay_safe
```

`AgentRuntime` must not continue merely because a server implementation exists.

---

## P0-R6-3 — SE has no durable remote-outcome certainty state

`CapabilityInvocation` knows lifecycle state but cannot currently represent:

```text
not dispatched
in flight
outcome unknown after transport loss
terminal outcome committed
```

`ConnectionMultiplexer` only knows process-local pending futures.

After disconnect it removes the pending entry.

A late result is simply rejected because no future exists.

That protects against resurrection but does not solve reconciliation.

---

## P0-R6-4 — no reconciliation wire protocol exists

Current protocol has:

```text
capability.invoke
capability.progress
capability.result
capability.error
capability.cancel
capability.cancelled
```

There is no explicit:

```text
capability.reconcile
capability.reconciliation
```

or equivalent.

Re-sending `capability.invoke` as a reconciliation query is forbidden because
an invoke message carries execution permission and may cause a duplicate
non-idempotent side effect.

---

## P0-R6-5 — CL terminal replay is not durable across process restart

`CapabilityDispatcher._terminal` is:

```text
in memory
bounded by TTL
bounded by max_terminal
```

Same-process reconnect works.

CL process restart loses the terminal outcome.

For:

```text
NON_IDEMPOTENT
UNKNOWN
```

this creates an unsafe ambiguity.

R6 requires a durable client invocation ledger for these outcomes, or an
equivalent persistence boundary.

---

## P0-R6-6 — duplicate invocation ID is not semantically fingerprinted on CL

Client dedupe currently keys only by:

```text
invocation_id
```

If the same invocation ID is accidentally or maliciously reused with different:

```text
capability_id
arguments
capability version
```

the dispatcher can replay the old terminal result.

### Frozen rule

One `invocation_id` has one immutable semantic request.

Reuse with a different semantic fingerprint is a conflict and must never
execute or replay.

---

## P0-R6-7 — invocation does not durably snapshot reconciliation identity

SE `CapabilityInvocation` currently persists `connection_id` but does not
persist sufficient stable reconciliation identity such as:

```text
origin_client_id
owner_user_id
idempotency class snapshot
request fingerprint
remote outcome certainty
```

After the original connection is gone, R6 must authorize a new connection by:

```text
same authenticated principal
same stable client installation by default
```

not merely by a new connection ID.

---

## P0-R6-8 — timeout/cancel is not proof that the remote effect did not occur

Current timeout path removes the pending future and sends:

```text
capability.cancel
```

Client cancellation is cooperative.

A running thread or external side effect may already have completed even if:

```text
cancel requested
server timed out
client later emits/records cancelled
```

### Frozen rule

```text
cancel request != rollback proof
timeout != not-dispatched proof
```

Late remote terminal outcomes must never resurrect an already-terminal server
invocation, but uncertainty must remain auditable and must never authorize
blind replay.

---

# 6. P1 findings

## P1-R6-1 — idempotency vocabulary is inconsistent in historical docs

Old R0 document mentions:

```text
IDEMPOTENT
RECONCILABLE
NON_IDEMPOTENT
```

The current main roadmap and R7 contract use:

```text
IDEMPOTENT
DEDUPLICATED
NON_IDEMPOTENT
UNKNOWN
```

Repository search found `RECONCILABLE` only in historical documentation, not in
production code.

### Frozen R6 vocabulary

Use the current four-state contract:

```text
IDEMPOTENT
DEDUPLICATED
NON_IDEMPOTENT
UNKNOWN
```

Do not introduce `RECONCILABLE` into new production code.

---

## P1-R6-2 — reconciliation error naming differs between R6 and R7 docs

Main roadmap:

```text
REMOTE_RESULT_RECONCILIATION_REQUIRED
```

R7 v2 draft:

```text
REMOTE_RECONCILIATION_REQUIRED
```

### Frozen R6 canonical code

Use:

```text
REMOTE_RESULT_RECONCILIATION_REQUIRED
```

because R6 owns this error taxonomy.

R7 should consume the R6 code rather than define a competing spelling.

Also retain:

```text
REMOTE_CONNECTION_LOST
REMOTE_OUTCOME_UNKNOWN
```

Recommended new semantic-conflict code:

```text
REMOTE_INVOCATION_CONFLICT
```

---

## P1-R6-3 — SQL invocation store has CAS but no reconciliation read API

`SqlCapabilityInvocationStore` currently exposes write/CAS/attempt methods but
not a canonical:

```text
get(invocation_id)
get_attempts(invocation_id)
```

R6 needs durable lookup independent of the process-local runtime object.

---

## P1-R6-4 — no client-side persisted principal partition

The current in-memory dispatcher resets outcomes on principal change.

A durable ledger must preserve the same security property:

```text
outcome from principal A
must not replay for principal B
```

Records may remain for GC/audit, but lookup/replay must match both:

```text
client_id
principal_id
```

---

# 7. Frozen capability idempotency contract

Add first-class:

```python
CapabilityIdempotency:
    IDEMPOTENT
    DEDUPLICATED
    NON_IDEMPOTENT
    UNKNOWN
```

Recommended default:

```text
UNKNOWN
```

This is fail-safe for legacy capabilities.

Do not infer replay safety merely from:

```text
READ
WRITE
EXECUTE
EXTERNAL_SIDE_EFFECT
```

because effect classification and idempotency are related but not equivalent.

Existing `CapabilityEffect` remains useful for policy/HITL.

## Semantics

### IDEMPOTENT

```text
repeating identical request is safe
```

If outcome is unknown, controlled same-invocation re-execution is allowed.

### DEDUPLICATED

The logical capability contract guarantees stable deduplication using:

```text
invocation_id
```

as the idempotency identity.

Every eligible implementation of that logical capability must honor that
contract.

If it cannot, the definition must not declare DEDUPLICATED.

### NON_IDEMPOTENT

```text
duplicate execution may cause a duplicate effect
```

Unknown outcome never auto-replays.

### UNKNOWN

No replay-safety guarantee.

Treat like NON_IDEMPOTENT after an unknown outcome.

---

# 8. Frozen semantic request fingerprint

R6 defines:

```text
request_fingerprint =
SHA256(
    canonical JSON {
        capability_id,
        capability_version,
        arguments
    }
)
```

Canonical JSON:

```text
UTF-8
sorted keys
compact separators
JSON-safe values only
```

The fingerprint is persisted on SE and CL.

Same `invocation_id` + different fingerprint:

```text
REMOTE_INVOCATION_CONFLICT
```

No execution and no terminal replay.

`message_id` is never an idempotency key.

---

# 9. Frozen remote outcome certainty model

Do not add outcome ambiguity as a new top-level CapabilityInvocation lifecycle
state.

Keep lifecycle:

```text
CREATED
DISPATCHING
RUNNING
WAITING
RETRYING
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

Add a separate remote outcome field for remote attempts:

```text
RemoteOutcomeState:
    NOT_DISPATCHED
    IN_FLIGHT
    OUTCOME_UNKNOWN
    TERMINAL_COMMITTED
```

## Meaning

### NOT_DISPATCHED

SE has proof the remote execution was not sent.

Examples:

```text
routing rejected before send
no active socket before transport send begins
authorization rejected before send
```

This is the only state where replay/fallback is safe independent of the
capability idempotency class.

### IN_FLIGHT

The remote invocation has entered transport execution scope and may execute.

Once SE attempts the actual transport send, a send failure is treated
conservatively unless transport can prove zero delivery.

### OUTCOME_UNKNOWN

SE cannot determine whether the remote effect occurred.

Typical transition:

```text
IN_FLIGHT
    +
connection loss before server terminal commit
    ->
OUTCOME_UNKNOWN
```

A `socket.send_json()` exception after send was attempted is also treated as
unknown unless the transport proves the peer received no bytes.

### TERMINAL_COMMITTED

SE durably accepted one terminal remote outcome for the invocation.

Exactly one terminal outcome may become authoritative.

---

# 10. Server durable invocation additions

Extend the existing `CapabilityInvocation` authority rather than adding a
parallel SE ledger.

Recommended durable snapshot fields for R6:

```text
idempotency
request_fingerprint

owner_user_id
origin_client_id

remote_outcome_state
```

Existing:

```text
connection_id
output
error
revision
attempts
```

remain.

For non-client/server-only capabilities:

```text
remote_outcome_state = null
origin_client_id = null
```

## Migration

Recommended migration:

```text
12a_r6_remote_reconciliation
down_revision = 11a_r5_task_budget
```

No fake historical certainty should be invented.

For existing historical remote rows:

```text
remote_outcome_state = null
idempotency = UNKNOWN
```

R6 reconciliation must fail closed when the durable certainty required for a
decision is absent.

---

# 11. Frozen reconciliation wire protocol

Introduce dedicated messages.

## SE -> CL

```text
capability.reconcile
```

Envelope:

```text
connection_id = new active connection generation
invocation_id = original stable invocation ID

payload:
    capability_id
    capability_version
    request_fingerprint
```

This message is a query.

It never grants permission to execute the capability.

## CL -> SE

```text
capability.reconciliation
```

Payload status:

```text
RUNNING
TERMINAL
UNKNOWN
NOT_FOUND
CONFLICT
```

### RUNNING

Same CL process still owns the invocation.

Do not execute again.

### TERMINAL

CL has an exact terminal outcome.

Payload includes:

```text
terminal_type:
    result
    error
    cancelled

terminal_payload
capability_id
capability_version
request_fingerprint
```

SE validates identity/fingerprint and commits/reuses it idempotently.

### UNKNOWN

Ledger proves the invocation existed but the client process lost authority
before a terminal outcome was recorded.

Example:

```text
durable client row was RUNNING
process crashed
```

### NOT_FOUND

The current client ledger has no record for the invocation.

Policy decision then depends on the server's remote outcome state and
idempotency class.

### CONFLICT

Same invocation ID exists but semantic fingerprint differs.

Fail closed.

---

# 12. Why reconciliation must not reuse capability.invoke

`capability.invoke` means:

```text
execute if not already known
```

`capability.reconcile` means:

```text
tell me what is already known;
do not create a side effect
```

Combining these meanings would make the safety boundary ambiguous.

Therefore R6 must keep them distinct.

---

# 13. ClientInvocationLedger contract

R6 introduces durable CL persistence for reconciliation.

Recommended implementation:

```text
cl/src/core/client_invocation_ledger.py
```

A lightweight local SQLite store is sufficient.

## Record identity

Logical key:

```text
(client_id, principal_id, invocation_id)
```

`invocation_id` may be the physical primary key only if the implementation
still validates the stored client/principal fields on lookup.

## Required fields

```text
invocation_id
client_id
principal_id

capability_id
capability_version
request_fingerprint
idempotency

state:
    PREPARED
    RUNNING
    TERMINAL

terminal_type
terminal_payload

created_at
updated_at
expires_at
```

Optional diagnostic field:

```text
side_effect_committed
```

must never be inferred as `False` merely because cancellation/timeout occurred.

## Write ordering

Before local execution begins:

```text
persist PREPARED
```

Immediately before calling the target:

```text
persist RUNNING
```

After one terminal client outcome is known:

```text
persist TERMINAL
```

then send/replay it.

## Restart interpretation

After CL process restart:

```text
TERMINAL
    -> exact replay

RUNNING
    -> UNKNOWN

PREPARED
    -> no side-effect execution has begun if and only if the PREPARED->RUNNING
       write occurs before target invocation
```

The write order is therefore correctness-sensitive.

---

# 14. Client dispatcher integration

Keep current in-memory structures for performance:

```text
_invocations
_terminal
```

Add durable ledger as the recovery authority.

## Duplicate live invoke

Before executing/replaying:

```text
verify request_fingerprint
```

Same ID, different fingerprint:

```text
REMOTE_INVOCATION_CONFLICT
```

## Same-process reconnect

Current behavior already supports:

```text
result send fails
terminal remains cached
new connection generation
same invocation reconciliation
exact terminal replay
```

R6 should use the explicit reconcile message instead of requiring a duplicate
invoke.

## Process restart

Dispatcher consults the durable ledger.

It never guesses from an empty in-memory cache.

---

# 15. Principal/client authorization contract

Reconciliation on connection K2 is accepted only when:

```text
authenticated user == invocation.owner_user_id
connection K2 belongs to authenticated user
connection K2 client_id == invocation.origin_client_id by default
```

A new connection generation is expected:

```text
K2 != K1
```

Cross-installation reconciliation requires a future explicit policy.

Do not use `session_id` as client identity.

---

# 16. Frozen reconciliation decision model

Server-side R6 should expose a typed internal result usable by R7.

Recommended:

```text
RemoteReconciliationDisposition:

    REUSE_TERMINAL
    WAIT_REMOTE_RUNNING
    REDISPATCH_SAME_INVOCATION
    REQUIRE_RECONCILIATION
    OUTCOME_UNKNOWN
    CONFLICT
```

## REUSE_TERMINAL

SE already has a terminal outcome or CL returned one.

Use exact same outcome.

No dispatch.

## WAIT_REMOTE_RUNNING

CL reports the original invocation is still running.

Do not dispatch.

## REDISPATCH_SAME_INVOCATION

Allowed only when:

```text
remote outcome == NOT_DISPATCHED
```

or:

```text
idempotency in {IDEMPOTENT, DEDUPLICATED}
AND reconciliation cannot recover a terminal result
AND policy permits controlled re-execution
```

The invocation ID remains unchanged.

## REQUIRE_RECONCILIATION

Server cannot decide until the originating client is available.

Stable error:

```text
REMOTE_RESULT_RECONCILIATION_REQUIRED
```

## OUTCOME_UNKNOWN

For:

```text
NON_IDEMPOTENT
UNKNOWN
```

when prior dispatch may have occurred and no terminal result can be recovered:

```text
REMOTE_OUTCOME_UNKNOWN
```

No automatic replay/fallback.

## CONFLICT

Identity/fingerprint mismatch.

Recommended stable error:

```text
REMOTE_INVOCATION_CONFLICT
```

---

# 17. Automatic retry/fallback matrix

| Server certainty | IDEMPOTENT | DEDUPLICATED | NON_IDEMPOTENT | UNKNOWN |
|---|---|---|---|---|
| NOT_DISPATCHED | allow | allow | allow | allow |
| IN_FLIGHT + connection alive | wait | wait | wait | wait |
| OUTCOME_UNKNOWN | controlled replay allowed | controlled replay allowed with stable invocation key | block | block |
| terminal recovered | reuse | reuse | reuse | reuse |
| CL says RUNNING | wait | wait | wait | wait |
| CL restart + ledger RUNNING/UNKNOWN | controlled replay by policy | controlled replay by policy | block | block |
| CL ledger NOT_FOUND after possible dispatch | controlled replay by policy | controlled replay by policy | block | block |

"allow" always means:

```text
same logical invocation_id
same request_fingerprint
Task/Execution budget still valid
normal authorization still valid
```

---

# 18. Required CapabilityRuntime correction

Current disconnect fallback must become policy-aware.

Forbidden after R6:

```text
RemoteConnectionLost
    ->
"server implementation exists"
    ->
automatic fallback
```

Required shape:

```text
RemoteConnectionLost
    ->
load durable invocation certainty
    ->
R6 reconciliation policy
    |
    +-- safe -> controlled same-invocation retry/fallback
    |
    +-- terminal recovered -> reuse
    |
    +-- unsafe/unknown -> WAITING / reconciliation required
```

Existing fallback tests must be reclassified by idempotency policy.

---

# 19. Required AgentRuntime correction

Current logic:

```text
can_continue_server_side(capability_id)
```

is not sufficient.

R6 target:

```text
reconciliation disposition
```

drives the continuation decision.

Conceptually:

```text
REUSE_TERMINAL
    -> inject exact tool result

REDISPATCH_SAME_INVOCATION
    -> controlled execution path

WAIT_REMOTE_RUNNING
REQUIRE_RECONCILIATION
OUTCOME_UNKNOWN
    -> AgentExecution WAITING(CONNECTION)
       or fail-safe/HITL according to policy

CONFLICT
    -> fail closed
```

R7 later owns durable ResumeClaim and canonical same-execution resume.

R6 must not implement R7 here.

---

# 20. Required continuation-service correction

Remove replay-safety authority from:

```text
server_continuation_available: bool
```

Availability may remain as routing metadata, but it cannot decide whether the
checkpoint remains RUNNING.

R6 should pass a typed reconciliation safety result, or make AgentRuntime
choose WAITING before calling the current checkpoint API.

Do not let a boolean named "server available" encode side-effect certainty.

---

# 21. Timeout and cancellation semantics

## Cancellation request

```text
SE -> capability.cancel
```

means:

```text
request local cancellation
```

It does not mean:

```text
external effect rolled back
```

## capability.cancelled

It means the CL invocation reached its client-side cancelled terminal outcome.

It still does not prove an external effect was rolled back.

R6 invariant:

```text
cancelled invocation must never be automatically re-executed merely because
the caller did not receive a normal result
```

## Server timeout

A server-side timeout may terminalize the caller-facing invocation according
to existing lifecycle policy.

A later remote outcome must never resurrect that terminal lifecycle state.

If remote outcome certainty is still unknown, preserve it as reconciliation
diagnostic state and never use timeout as permission to replay.

---

# 22. Server multi-worker boundary

`ConnectionMultiplexer` remains process-local.

It is not the R6 durable authority.

A reconnect/reconciliation request may reach a different SE worker.

Therefore reconciliation must load:

```text
CapabilityInvocation
CapabilityInvocationAttempt
remote outcome fields
```

from durable storage.

Correctness must not depend on:

```text
_pending future still existing in the original worker
```

---

# 23. Exact server call-sites to modify

## Domain

```text
se/src/runtimes/capability/contracts/definition.py
    add CapabilityIdempotency

se/src/runtimes/capability/contracts/invocation.py
    add remote outcome snapshot fields / enum
```

## Persistence

```text
se/src/infrastructure/storage/models/sql/capability/invocation.py
se/src/infrastructure/storage/repositories/capability_invocations.py
se/src/infrastructure/storage/migrations/sql/versions/12a_r6_remote_reconciliation.py
```

Add durable read APIs and reconciliation CAS.

## Dispatch

```text
se/src/runtimes/connection/realtime.py
se/src/runtimes/capability/drivers/remote_client_driver.py
se/src/runtimes/capability/runtime.py
```

Mark remote outcome certainty and gate unsafe retry/fallback.

## Protocol/transport

```text
se/src/runtimes/connection/protocol.py
se/src/transport/gateway/api/v1/events_router.py
se/src/runtimes/connection/runtime.py
```

Add reconcile request/response handling.

## Agent bridge

```text
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/continuation.py
```

Replace server-availability-as-safety with typed reconciliation disposition.

---

# 24. Exact client call-sites to modify

```text
cl/src/core/capability_runtime.py
    advertise idempotency contract

cl/src/core/capability_dispatcher.py
    semantic fingerprint validation
    reconciliation query handling
    durable ledger integration

cl/src/core/local_capability_executor.py
    preserve correctness-sensitive PREPARED/RUNNING boundary

cl/src/core/client_runtime.py
    keep generation reconnect
    wire reconciliation after new READY generation

cl/src/core/realtime_client.py
    send capability.reconciliation

ADD:
cl/src/core/client_invocation_ledger.py
```

Do not merge this work with MCP ownership changes.

---

# 25. R6 implementation waves

## R6-A — Domain + durable SE certainty

Implement:

```text
CapabilityIdempotency
RemoteOutcomeState
request fingerprint
origin user/client snapshot
stable R6 errors
SQL migration 12a
store read/CAS APIs
```

Tests:

```text
enum/default UNKNOWN
fingerprint stability
migration
durable round-trip
CAS conflict
historical null/UNKNOWN fail-closed behavior
```

Exit:

```text
SE can durably distinguish certainty from lifecycle state
```

---

## R6-B — Dispatch safety / stop unsafe fallback

Implement:

```text
remote dispatch certainty transitions
policy-aware RemoteConnectionLost handling
block unsafe fallback
replace can_continue_server_side-as-safety
```

Tests:

```text
disconnect before send -> NOT_DISPATCHED
disconnect after send -> OUTCOME_UNKNOWN
IDEMPOTENT fallback allowed
DEDUPLICATED controlled same-ID fallback allowed
NON_IDEMPOTENT fallback blocked
UNKNOWN fallback blocked
server availability alone does not continue Agent
```

Exit:

```text
no blind second execution after uncertain remote send
```

---

## R6-C — Reconciliation protocol + same-process CL

Implement:

```text
capability.reconcile
capability.reconciliation
server reconciliation service
CL dispatcher RUNNING/TERMINAL/NOT_FOUND/CONFLICT responses
same-client/new-connection authorization
```

Tests:

```text
RUNNING duplicate does not execute again
terminal cached result replays exactly
new connection generation can query old invocation
foreign client/user rejected
fingerprint conflict rejected
```

Exit:

```text
same-process result-send loss is recoverable without duplicate execution
```

---

## R6-D — Durable ClientInvocationLedger

Implement local SQLite ledger.

Tests:

```text
terminal survives CL process restart
RUNNING row after crash becomes UNKNOWN
PREPARED row semantics are deterministic
principal isolation
client_id isolation
TTL/GC does not violate active reconciliation
non-idempotent unknown remains blocked
```

Exit:

```text
unsafe side-effect result is not forgotten merely because CL restarted
```

---

## R6-E — End-to-end reconciliation gate

Real TCP WebSocket fault-injection scenarios:

```text
disconnect before dispatch
disconnect during local tool
side effect completes but result-send fails
same-process reconnect
CL process restart
IDEMPOTENT controlled replay
DEDUPLICATED stable invocation replay
NON_IDEMPOTENT unknown blocked
UNKNOWN unknown blocked
late old-connection result cannot resurrect
```

No:

```text
FakeSocket
manual realtime.handle_inbound()
manual capability.result injection
```

for the canonical E2E gate.

Exit:

```text
No automatic duplicate side effect after connection loss.
```

---

# 26. Required test matrix

## Race R6-1 — disconnect before remote send

Expected:

```text
remote outcome NOT_DISPATCHED
alternate implementation may be selected
```

No reconciliation needed for side-effect ambiguity.

## Race R6-2 — disconnect after send while tool running

Expected:

```text
OUTCOME_UNKNOWN
no automatic replay for NON_IDEMPOTENT/UNKNOWN
```

## Race R6-3 — tool completed, result lost

Expected:

```text
CL terminal ledger contains exact result
new K2 reconcile(I1)
CL returns terminal
SE commits/reuses exact outcome
tool executes once
```

## Race R6-4 — duplicate reconcile

Expected:

```text
same terminal response
no revision resurrection
no second execution
```

## Race R6-5 — old K1 late result vs K2 reconciliation

Expected:

```text
K1 cannot mutate K2-bound pending transport
durable invocation accepts at most one authoritative terminal outcome
```

## Race R6-6 — same invocation ID different arguments

Expected:

```text
REMOTE_INVOCATION_CONFLICT
no execution
no replay
```

## Race R6-7 — CL crashes mid-tool

Ledger:

```text
RUNNING
```

After restart:

```text
UNKNOWN
```

For NON_IDEMPOTENT:

```text
REMOTE_OUTCOME_UNKNOWN
no replay
```

## Race R6-8 — cancel vs side-effect completion

Cancellation must not be treated as rollback proof.

Late terminal result may be recorded for audit/reconciliation safety, but cannot
resurrect a terminal server lifecycle.

---

# 27. Compatibility / migration

## Capability definitions

Legacy capability with no idempotency declaration:

```text
UNKNOWN
```

This may reduce aggressive fallback after disconnect, which is intentional
fail-safe behavior.

## Protocol

R6 adds messages; it should not reinterpret existing:

```text
capability.invoke
capability.result
capability.error
capability.cancelled
```

Old clients that do not support reconcile cannot prove a safe terminal replay.

For unknown non-idempotent outcomes:

```text
fail safe / remain waiting
```

rather than silently replay.

Protocol-version migration/legacy cleanup remains R13 if broader wire-version
negotiation is needed.

---

# 28. Interaction with R7

R6 must export a narrow reconciliation authority that R7 can call.

R7 precondition:

```text
R6 decision says one of:

REUSE_TERMINAL
REDISPATCH_SAME_INVOCATION
WAIT_REMOTE_RUNNING
OUTCOME_UNKNOWN
CONFLICT
```

R7 then owns:

```text
checkpoint validation
ResumeClaim
WAITING -> RUNNING CAS
runtime supervisor handoff
execution.resume.accepted
```

R6 must not create ResumeClaim or own AgentExecution resume CAS.

---

# 29. MCP scope decision

Current `POST_R14_MCP_CLIENT_INTEGRATION_NOTE.md` explicitly supersedes the
older addendum that tried to make MCP ownership a prerequisite for R6/R7.

Therefore:

```text
cl/tests/test_mcp_client_ownership.py
```

remains outside R6.

R6 must not be delayed or redesigned around MCP ownership work.

Future post-R14 MCP reconciliation consumes the finished R6 contract.

---

# 30. R6 stop conditions

Stop coding and return to contract review if implementation requires any of:

```text
new invocation_id to reconcile the same logical call

automatic NON_IDEMPOTENT replay after OUTCOME_UNKNOWN

treating server implementation availability as replay safety

treating capability.cancel as rollback proof

treating timeout as proof the remote effect did not happen

using connection_id as stable client installation identity

using only process-local ConnectionMultiplexer state after reconnect

reusing cached terminal result for a different semantic fingerprint

making a terminal CapabilityInvocation RUNNING again
```

Any one of these violates the R6 architecture.

---

# 31. Recommended R6 focused gates

After R6-A/B:

```text
capability invocation lifecycle
SQL invocation migration/store
remote driver
realtime multiplex
disconnect/fallback
Agent continuation regressions
```

After R6-C/D:

```text
CL dispatcher
ClientRuntime reconnect generation
client durable ledger
principal/fingerprint reconciliation
```

After R6-E:

```powershell
py -m pytest -q se/tests tools cl/tests `
  --ignore e:\assistant\cl\tests\test_mcp_client_ownership.py
```

plus canonical true-TCP R6 reconciliation E2E.

---

# 32. Final audit decision

R6 should proceed, but not as a simple reconnect patch.

The current repository has enough foundation to implement R6 without changing
the R0-R5 identity/state ownership model.

The highest-priority correction is:

```text
RemoteConnectionLost
must no longer mean
"safe to route somewhere else"
```

It means:

```text
transport ownership was lost
```

and, after a send may have occurred:

```text
remote outcome may be unknown
```

The authoritative R6 chain should become:

```text
durable CapabilityInvocation
    +
remote outcome certainty
    +
capability idempotency contract
    +
stable client identity
    +
ClientInvocationLedger
    +
semantic request fingerprint
        |
        v
RemoteReconciliationDisposition
        |
        +-- reuse terminal
        +-- wait
        +-- safe same-invocation redispatch
        +-- reconciliation required
        +-- outcome unknown / block
```

Only after this exit gate is green should R7 implement durable
checkpoint-directed resume.
