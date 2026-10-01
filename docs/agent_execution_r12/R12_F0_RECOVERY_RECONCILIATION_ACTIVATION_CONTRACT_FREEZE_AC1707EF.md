# AE-R12-F0 — Recovery Reconciliation + Activation Contract Freeze

Baseline: `main@ac1707ef0e94ca80f9cf517d0d3c4714c3568aff`  
Primary workspace: Issue #107  
Policy: Issue #85 v2.5  
Class: CONTRACT / EVIDENCE / ARCHITECTURE-TEST ONLY

## Status

```text
R12-E = CANONICAL / HEALTHY
R12-F0 = ZERO-PRODUCTION CONTRACT CANDIDATE
R12-F1/F2/F3 production = NOT CLAIMED
R12-G/H = NOT CLAIMED
UBQ-2 PR #171 = LANDED / CANONICAL @ main 40ccd4bf48e710fa74fa2b91add75ae92600e3d6
UBQ-2 post-merge Architecture #1647 = GREEN/GREEN
R12-F production CLAIM = HOLD
```

This freeze records the exact recovery-planning and activation authority boundary
needed after R12-E has published one normalized `WAITING(RECOVERY)` safe point.

It changes no production runtime, persistence, schema, migration, capability,
transport, TaskBudget or UBQ behavior.

## 1. Inherited authority

R12-F MUST reuse, not redefine:

- **R12-E** — exact current `WAITING(RECOVERY)` checkpoint, recovery fingerprint,
  immutable frozen active-batch identity and checkpoint pending-invocation snapshots.
- **R7** — durable checkpoint/ResumePlan/ResumeClaim substrate, atomic claim
  consumption transaction and supervisor handoff semantics.
- **R6** — CapabilityInvocation remote-outcome state machine, stable-client
  reconciliation, idempotency/dedup replay safety and terminal commitment.
- **R9/R12-E + canonical UBQ-2 TaskBudget** — active-capacity accounting,
  immutable TaskBudget incarnation authority and incarnation-bound structural
  reservation semantics.
- **R12-C** — fresh execution lease acquire/renew/release authority and exact
  active-fence predicate over owner + generation + unexpired expiry.
- **R12-A invariants I14-I16** — active lease/fence validation immediately before
  external provider/tool dispatch and durable active-owner commits; fence loss stops
  new external dispatch.
- **R11** — checkpoint/transcript/pending-invocation physical persistence and GC roots.

R12-F MUST NOT mint a second reconciliation engine, a second execution-claim CAS,
a second active-batch ordering source, or a second durable recovery lifecycle.

Important current-main limitation:

> Existing R7 claim consumption is CLIENT_RECONNECT-specific and cannot be reused
> unchanged for SERVER_RECOVERY.

R12-F2 therefore owns a trigger-aware extension of the same ResumeClaim consume
transaction, not a parallel recovery transaction.

## 2. R12-E checkpoint is the recovery cut

The current R12-E checkpoint is authoritative only when all of the following remain
true at planning time:

```text
AgentExecution.state == WAITING
AgentExecution.wait_reason == RECOVERY
AgentExecution.current_checkpoint_id == checkpoint.checkpoint_id
AgentExecution.revision == checkpoint.execution_revision
checkpoint.wait_reason == RECOVERY
checkpoint carries valid R12-E recovery fingerprint
```

The frozen recovery checkpoint owns:

- the committed transcript prefix;
- the frozen recovery iteration identity when present;
- the frozen ordered active tool-call IDs;
- the exact checkpoint pending-invocation watermark rows.

R12-F may not reconstruct a different active order from current mutable
`AgentIteration.tool_call_ids`, SQL row order, timestamps, invocation query order,
or late writes by the expired owner.

A valid R12-E iteration-zero cut remains valid:

```text
iteration = 0
ordered_tool_call_ids = ()
pending invocations = ()
invocation_actions = ()
```

R12-F1 MUST permit that empty recovery plan. It MUST NOT synthesize a fake tool call,
fake invocation or fake pending row merely to satisfy CONNECTION-resume assumptions.

## 3. Recovery planner is not the current connection planner

Current `AgentResumePlanningService.build_resume_plan(...)` is a CONNECTION-only
entry point:

- it requires `target_connection_id`;
- it verifies the execution/checkpoint wait reason is `CONNECTION`;
- it requires the reconnecting stable client to match checkpoint origin identity;
- its current connection path rejects shapes that are valid for an empty R12-E
  recovery cut.

R12-F therefore needs a recovery-specific read-only planning entry point/adaptor for
`WAITING(RECOVERY)`.

That future entry point may share internal R7 planning helpers, contracts and action
classification, but MUST NOT change a recovery wait reason to CONNECTION merely to
pass the existing connection planner.

## 4. Durable recovery principal authority

Automatic SERVER_RECOVERY has no authenticated request principal. Recovery principal
identity MUST therefore come from durable Agent authority, not from whichever
connection happens to be available.

Canonical F0 rule:

```text
execution.session_id
  -> AgentSessionRecord.id
  -> AgentSessionRecord.owner_user_id = recovery principal
```

`AgentSessionRecord.owner_user_id` is non-null durable Agent-session authority.

For task-scoped recovery all of these MUST agree:

```text
AgentTaskRecord.session_id == AgentExecution.session_id
AgentTaskRecord.created_by == AgentSessionRecord.owner_user_id
resolved recovery principal == AgentSessionRecord.owner_user_id
```

For taskless recovery:

```text
AgentExecution.session_id must resolve to AgentSessionRecord
resolved recovery principal = AgentSessionRecord.owner_user_id
```

If AgentSession is missing, principal is empty, or task/session ownership disagrees,
planning fails closed.

For every frozen pending CapabilityInvocation:

```text
invocation.owner_user_id == resolved recovery principal
```

A missing/mismatched invocation principal fails closed. Recovery MUST NOT synthesize
a placeholder principal, derive ownership from a live connection, or treat chat
Session nullable user identity as execution recovery authority.

This execution-authorization principal is not automatically the same thing as future
UBQ budget-owner resolution. Equality requires a separately frozen contract.

## 5. R6 remote-outcome matrix remains canonical

For each pending invocation frozen by the R12-E checkpoint:

```text
TERMINAL_COMMITTED
  -> verify exact committed projection
  -> REUSE_COMMITTED

NOT_DISPATCHED
  -> candidate DISPATCH_NOT_DISPATCHED
  -> executable only after continuation-affinity proof

IN_FLIGHT / OUTCOME_UNKNOWN
  -> R6 CapabilityRuntime.reconcile_remote_invocation()

reconcile TERMINAL
  -> exact terminal commitment
  -> reload durable invocation
  -> REUSE_COMMITTED

reconcile RUNNING
  -> DEFER

reconcile UNKNOWN / NOT_FOUND
  + IDEMPOTENT or DEDUPLICATED
  -> candidate REPLAY_SAFE
  -> executable only after continuation-affinity proof

reconcile UNKNOWN / NOT_FOUND
  + NON_IDEMPOTENT or UNKNOWN
  -> DEFER / fail closed

null / unsupported remote outcome
  -> REJECT / fail closed

semantic conflict / foreign principal / foreign stable client
  -> REJECT / fail closed
```

R12-F does not invent a broader replay rule or infer remote certainty from lifecycle
state alone.

## 6. Reconciliation and continuation affinity

`RemoteInvocationReconciliationService` requires a live connection snapshot whose:

```text
snapshot.user_id == invocation.owner_user_id
snapshot.metadata["client_id"] == invocation.origin_client_id
```

Therefore automatic server recovery cannot treat lack of a compatible connection as
proof that a remote side effect did not happen.

For `IN_FLIGHT` or `OUTCOME_UNKNOWN`:

- if the originating stable client is available and authorized, R6 reconciliation
  may run;
- if no compatible connection exists, recovery planning remains DEFERRED;
- no blind replay, capability rerouting, alternate-client reconciliation, or
  `OUTCOME_UNKNOWN -> NOT_DISPATCHED` inference is authorized by R12-F.

The same pre-claim affinity rule applies to non-REUSE continuation actions.

A final `DISPATCH_NOT_DISPATCHED` or `REPLAY_SAFE` action is claimable only after
planning proves an executable continuation target.

For the current client-affine continuation substrate this requires:

```text
continuation runtime catalog is available
continuation connection registry is available
continuation realtime multiplexer is available
connection is ACTIVE / usable
connection.user_id == resolved recovery principal
connection stable client_id == invocation.origin_client_id
target_connection_id != invocation.connection_id
checkpoint snapshot origin_client_id == invocation.origin_client_id
matching capability_id + capability_version is ready on that connection
exactly one matching client-owned `REMOTE_CLIENT` implementation exists on target
implementation.location == CLIENT
implementation.owner_type == CLIENT
implementation.driver_kind == REMOTE_CLIENT
implementation.connection_id == target_connection_id
implementation.owner_id == invocation.owner_user_id
implementation stable client_id == invocation.origin_client_id
implementation.version == invocation.capability_version
definition.version == invocation.capability_version
definition.kind == invocation.kind
definition.execution_mode == invocation.execution_mode
definition.idempotency == invocation.idempotency
```

Capability/version readiness alone is insufficient continuation authority. The
pre-claim proof MUST mirror the full canonical
`CapabilityRuntime._resolve_continuation_target(...)` predicate that F3 will use.
Before any connection or implementation match is considered, the continuation
runtime MUST prove all three transport-resolution dependencies are available:
the capability catalog, connection registry and realtime multiplexer. Missing any
of these dependencies means the continuation target is unavailable and MUST DEFER
before claim.

After that guard passes, the target connection must resolve to exactly one matching
client-owned `REMOTE_CLIENT` implementation, and the current capability definition
must still match the durable invocation's version, kind, execution mode and
idempotency.

A zero matching implementation is continuation-unavailable and MUST DEFER before
claim. Multiple matching implementations are ambiguous/conflicting authority and
MUST REJECT before claim. A changed definition contract MUST REJECT before claim.
Recovery MUST NOT consume ResumeClaim or publish `RUNNING` first and defer this
predicate to F3.

Current `CapabilityToolExecutionAdapter.continue_invocation(...)` also requires a
claimed non-null execution connection. Canonical
`CapabilityRuntime._resolve_continuation_target(...)` additionally rejects
`target_connection_id == invocation.connection_id`: continuation of an existing
logical invocation requires a **fresh connection generation**. Therefore every
non-REUSE client-affine continuation MUST prove before claim that the target
connection is a new generation for the same authorized stable client. A stale or
same-generation target means DEFER before claim for `DISPATCH_NOT_DISPATCHED` and
`REPLAY_SAFE`.

Fresh connection generation is not alternate-client rerouting: the new connection
must still carry the same `origin_client_id`, resolved principal and matching
capability/version authority.

R12-F0 does not authorize silent rerouting of the same logical invocation to another
client or implementation.

If a future stage wants connectionless server-side continuation of an existing
logical invocation, it requires a separately frozen same-invocation routing and
continuation contract. Existing availability of a server implementation is not
sufficient authority.

`REUSE_COMMITTED` itself does not require a remote continuation target because its
result is already durably committed, but all normal plan/checkpoint/principal fences
still apply.

## 7. SERVER_RECOVERY extends the existing ResumeClaim consume transaction

`ResumeTriggerType.SERVER_RECOVERY` already exists in the canonical vocabulary and
ResumeClaim records already permit nullable client/connection fields.

However current canonical consume behavior is explicitly CLIENT_RECONNECT-only:

```text
_claim_matches_plan:
  wait_reason == CONNECTION
  trigger_type == CLIENT_RECONNECT

_consume_resume_claim_once:
  requires target_user_id
  requires target_client_id
  requires target_connection_id
  execution wait_reason == CONNECTION
  final compare_and_set_waiting_execution(..., "CONNECTION", ...)
```

R12-F2 MUST generalize this existing transaction with a trigger-aware
SERVER_RECOVERY branch while preserving one ResumeClaim substrate and one final
WAITING -> RUNNING execution CAS authority.

Required SERVER_RECOVERY consume semantics:

```text
claim creation owns no execution authority
claim binds exact execution/checkpoint/revision/plan fingerprint
claim.wait_reason == RECOVERY
claim.trigger_type == SERVER_RECOVERY
claim.user_id == resolved durable recovery principal
execution.state == WAITING
execution.wait_reason == RECOVERY
execution.current_checkpoint_id == plan.checkpoint_id
execution.revision == plan.expected_execution_revision
checkpoint.wait_reason == RECOVERY
same invocation/action snapshot revalidation
same task/branch/TaskBudget atomic fences
claim consume + execution CAS commit atomically
one semantic revision winner
stale plan / stale claim / stale checkpoint fails closed
CLIENT_RECONNECT behavior remains unchanged
```

SERVER_RECOVERY client/connection optionality is trigger-aware:

- `target_user_id` is always the resolved durable recovery principal;
- `target_client_id` and `target_connection_id` MAY be null only when the frozen
  plan has no action requiring client-affine reconciliation/continuation;
- any `IN_FLIGHT`, `OUTCOME_UNKNOWN`, `DISPATCH_NOT_DISPATCHED`, or
  `REPLAY_SAFE` client-affine action requires compatible stable-client connection
  authority before claim;
- a valid empty recovery cut may therefore carry null client/connection and still
  proceed to claim after all other gates pass;
- no connection identity may be fabricated solely to satisfy legacy
  CLIENT_RECONNECT assertions.

This common durable claim authority is the basis for the later R12-G proof that
automatic recovery and user/manual resume cannot both activate the same execution.

### 7.1 Recovered activation must acquire a fresh durable lease atomically

R12-E intentionally publishes the normalized recovery safe point as unowned:

```text
state = WAITING
wait_reason = RECOVERY
owner_instance_id = NULL
lease_expires_at = NULL
lease_generation = observed expired-owner generation + 1
```

That shape is a safe point, not active runtime ownership.

R12-F2 MUST NOT externally commit or publish an activatable `RUNNING` execution
while `owner_instance_id` or `lease_expires_at` is null. SERVER_RECOVERY activation
must compose the following inside **one caller-owned durable UoW and one commit
boundary**:

```text
revalidate exact recovery plan + ResumeClaim
revalidate exact WAITING(RECOVERY) execution/checkpoint/revision
revalidate invocation/principal/TaskBudget fences
stage claim consumption
stage WAITING -> RUNNING semantic execution CAS
fresh R12-C lease acquire on that just-staged RUNNING row
commit once
```

The fresh lease request MUST bind:

```text
activation_owner_instance_id = non-empty durable worker identity
activation_now_utc = timezone-aware UTC
activation_lease_expires_at > activation_now_utc
pre-acquire lease_generation == plan.expected_unowned_lease_generation
post-acquire lease_generation == plan.expected_unowned_lease_generation + 1
post-acquire owner_instance_id == activation_owner_instance_id
post-acquire lease_expires_at == activation_lease_expires_at
```

R12-C acquire authority remains canonical: the row must be `RUNNING`, unowned and
have null expiry; successful acquire increments lease generation by exactly one
without changing the semantic execution revision.

The existing convenience
`DurableAgentStore.acquire_execution_lease(...)` owns a separate UoW/commit and
therefore MUST NOT be invoked as an independently committing second step after the
SERVER_RECOVERY claim transaction. F2 must call the caller-UoW repository lease
primitive (or a future equivalent caller-UoW helper) before the claim transaction's
single commit.

If semantic execution CAS, TaskBudget fencing, lease acquire, or any other
activation predicate loses, the entire activation transaction rolls back:

```text
ResumeClaim remains unconsumed
externally committed RUNNING-unowned state = NONE
active lease authority = NONE
new external dispatch = PROHIBITED
```

R12-D1 observes only expired **owned** RUNNING rows. Avoiding a committed
RUNNING-unowned window is therefore a recovery-discoverability requirement, not
merely an optimization.

## 8. Recovery plan identity

A future recovery plan must freeze at least:

```text
execution_id
checkpoint_id
expected_execution_revision
R12-E recovery fingerprint / frozen checkpoint authority
expected unowned recovery lease_generation
agent/session/task/branch lineage
resolved durable recovery principal
canonical iteration
frozen ordered_tool_call_ids
committed-only transcript snapshot
remaining active budget
task-scoped TaskBudget incarnation_generation when task_id is present
target recovery trigger = SERVER_RECOVERY
optional target stable client/connection authority
fresh connection-generation proof for every non-REUSE client-affine continuation
catalog + connection-registry + realtime availability for every client-affine continuation
full continuation-target predicate proof for every non-REUSE client-affine action
exactly-one implementation identity + definition version/kind/execution_mode/idempotency match
post-reconciliation invocation revision/state/outcome snapshots
safe action per invocation
continuation-affinity proof for every non-REUSE action
plan_fingerprint
```

A plan is read-only evidence. It owns no execution or TaskBudget authority.

Any invocation reconciliation performed during planning must be followed by a reload
of current durable invocation state before the plan freezes action authority.

For an empty R12-E cut, the plan has:

```text
ordered_tool_call_ids = ()
invocation_actions = ()
```

and remains valid without synthetic invocation evidence.

## 9. Activation boundary

After a future exact production release, R12-F activation may execute only safe,
still-current planned actions:

- `REUSE_COMMITTED`;
- `DISPATCH_NOT_DISPATCHED` with frozen executable continuation affinity;
- `REPLAY_SAFE` with frozen executable continuation affinity.

It MUST reject or defer any action whose durable revision/state/outcome/request
fingerprint, principal, client affinity or capability readiness no longer matches the
plan snapshot.

No provider inference may continue until all active-batch pending invocations are
either safely committed/reused or otherwise resolved according to canonical R6/R7
semantics.

A valid empty recovery cut has no invocation action barrier and may continue only
after SERVER_RECOVERY claim, principal, checkpoint, budget and supervisor activation
gates all succeed.

Recovered activation additionally requires the exact fresh R12-C active lease/fence
created by Section 7.1. This applies even to an empty iteration-zero recovery cut:
model/provider continuation is active-owner work and is not exempt from lease
authority.

Before **every** externally visible provider/model/tool dispatch and before every
durable active-owner commit, F3 MUST validate the exact active fence:

```text
execution.state == RUNNING
execution.owner_instance_id == activated owner
execution.lease_generation == activated lease_generation
execution.lease_expires_at > validation_now_utc
```

Execution revision equality alone is not an external-side-effect fence.

Lease renewal loss, owner mismatch, stale generation or expiry after activation
requires fail-closed behavior: stop new external dispatch immediately and do not
perform a durable active-owner commit under stale authority. Later parking/recovery
state transitions remain separately released authority.

## 10. TaskBudget / UBQ fence

UBQ-2 is now canonical on main `40ccd4bf48e710fa74fa2b91add75ae92600e3d6`.
R12-F therefore freezes the landed TaskBudget incarnation authority instead of
deferring its mutation shape.

For task-scoped SERVER_RECOVERY, F1 recovery-plan identity MUST include the exact
observed:

```text
task_id
TaskBudget.incarnation_generation
TaskBudget.revision snapshot as planning evidence
```

The `incarnation_generation` is immutable identity authority. A recreated
TaskBudget for the same task_id is a different incarnation even when its counters or
revision resemble the prior row.

Before claim consumption or resume-capacity reacquire, F2 MUST reload the current
TaskBudget inside the activation UoW and require:

```text
current TaskBudget exists
current TaskBudget.incarnation_generation
  == plan.task_budget_incarnation_generation
TaskBudget is OPEN
```

A missing/recreated/different generation fails closed **before activation**. Recovery
must not silently adopt the new incarnation.

Canonical `prepare_resume_capacity_in_uow(...)` already fences its current
transactional writes by the generation it reads:

- `RESUME_EXECUTION` reservation lookup uses
  `expected_incarnation_generation`;
- TaskBudget CAS uses `expected_incarnation_generation`;
- saved reservation persists `task_budget_incarnation_generation`.

For R12-F, this current helper is insufficient by itself to prove F1->F2 identity
continuity because it samples the durable generation when called. Production F2 MUST
either extend it with an expected plan generation or explicitly compare the durable
generation with the frozen plan generation in the same caller UoW before invoking
the existing staging logic. It MUST NOT re-sample a recreated generation and
continue as though the F1 plan remained current.

The existing logical `RESUME_EXECUTION` reservation key may remain:

```text
execution_id:source_revision
```

but lookup/save/replay authority is scoped to the exact
`task_budget_incarnation_generation`. Same logical key on a different TaskBudget
incarnation is not recovery replay authority.

Canonical TaskBudget mutation/replay requirements:

```text
TaskBudget CAS -> expected_incarnation_generation = frozen plan generation
reservation lookup -> expected_incarnation_generation = frozen plan generation
reservation save -> task_budget_incarnation_generation = frozen plan generation
generation mismatch/recreation -> fail closed
```

R12-E `RELEASE_EXECUTION` remains structural and generation-bound. R12-F does not
reinterpret that release as UBQ credit/refund authority.

Recovery MUST NOT reset, mint, refill, refund or create renewable
UserResourceBudget quota merely because recovery occurs. UserResourceBudget charging
and TaskBudget structural capacity remain distinct authorities.

The UBQ-2 PostgreSQL V7 executable parity/deployment gate remains OPEN and mandatory.
SQLite Architecture GREEN does not prove PostgreSQL V7 deployment parity and R12-F
must not weaken or bypass that gate.

## 11. Fail-closed race rules

Planning/activation must fail closed when any of these changes after observation:

- execution state/revision/current checkpoint;
- R12-E recovery checkpoint identity/fingerprint;
- frozen pending-invocation identity or ordinal;
- resolved durable recovery principal;
- invocation owner/revision/state/remote outcome/request fingerprint;
- stable-client reconciliation or continuation affinity;
- fresh target connection-generation authority for non-REUSE continuation;
- capability/version readiness needed for a continuation action;
- task/branch terminalization;
- expected unowned recovery lease_generation before activation;
- activated owner_instance_id / lease_generation / lease expiry active fence;
- TaskBudget incarnation/revision authority;
- frozen plan TaskBudget incarnation_generation versus current durable generation;
- durable ResumeClaim revision/state.

Terminal executions are never resurrected.

## 12. Staged successor split

```text
R12-F0  contract/evidence freeze                        [THIS CANDIDATE]
R12-F1  recovery-specific read-only plan construction  [PRODUCTION CLOSED]
R12-F2  atomic claim + RUNNING + fresh-lease handoff    [PRODUCTION CLOSED]
R12-F3  safe continuation action execution              [PRODUCTION CLOSED]
R12-G   multi-worker + recovery-vs-resume race matrix
R12-H   full fault/exit matrix + final AE-R12 freeze
```

R12-F1/F2/F3 require their own exact CLAIM after the dependency gate is released.

## 13. R12-F0 evidence requirements

The architecture test accompanying this document freezes only facts already true on
the baseline plus future contract constraints:

- `SERVER_RECOVERY` exists in canonical trigger vocabulary;
- existing R7 connection planning remains CONNECTION-only;
- existing ResumeClaim consume is CLIENT_RECONNECT-only and therefore must be
  generalized rather than reused unchanged;
- ResumeClaim creation remains non-authoritative;
- AgentSession has non-null durable `owner_user_id`;
- task-scoped durable ownership includes `AgentTask.created_by` and `session_id`;
- R6 reconciliation requires matching user + stable client;
- current existing-invocation continuation requires a non-null claimed connection;
- current capability runtime requires a fresh target connection generation
  (`target_connection_id != invocation.connection_id`) for continuation;
- R12-E recovery safe point is WAITING(RECOVERY), unowned, null-expiry and
  generation-bumped;
- R12-C fresh acquire requires RUNNING + unowned/null-expiry and increments
  lease_generation; its active fence requires exact owner/generation + unexpired expiry;
- R12-A I14-I16 require active-fence validation before external dispatch and
  durable active-owner commits and require dispatch stop on fence loss;
- R12-D1 expired scanner observes only owned RUNNING rows;
- canonical resume-capacity/recovery TaskBudget paths are incarnation-fenced;
- durable-principal nullability evidence is bound directly to
  `AgentSessionRecord.owner_user_id` and `AgentTaskRecord.created_by`;
- R12-E leaves unresolved invocation snapshots for R12-F and permits iteration-zero
  recovery;
- this candidate contains no production/runtime/schema/migration delta.

## 14. Auditor finding disposition in this replacement candidate

```text
P1-R12-F0-SERVER-RECOVERY-CONSUME-1
  -> addressed by Sections 7-8 + architecture evidence

P1-R12-F0-RECOVERY-PRINCIPAL-2
  -> addressed by Section 4 + architecture evidence

P1-R12-F0-CONTINUATION-AFFINITY-3
  -> addressed by Sections 5-6 + architecture evidence

P2-R12-F0-EMPTY-RECOVERY-CUT-4
  -> addressed by Sections 2, 8, 9 + architecture evidence

P1-R12-F0-FRESH-CONNECTION-GENERATION-1
  -> addressed by Sections 6, 8, 11, 13 + architecture evidence

P2-R12-F0-PRINCIPAL-NULLABILITY-EVIDENCE-2
  -> addressed by Sections 4, 13 + direct mapped-column nullability evidence

P1-R12-F0-RECOVERED-ACTIVATION-LEASE-FENCE-4
  -> addressed by Sections 7.1, 8, 9, 11, 13 + architecture evidence

P1-R12-F0-POST-UBQ2-INCARNATION-FREEZE-5
  -> addressed by Sections 8, 10, 11, 13 + architecture evidence
```

These are candidate-level repairs only until independent replacement audit accepts
them on the exact repaired HEAD.

## 15. Current gate

```text
R12-F0 candidate branch = allowed
R12-F0 class = docs + architecture test only
UBQ-2 PR #171 = LANDED / CANONICAL / post-merge GREEN
fresh exact-main R6/R7/R12/TaskBudget/incarnation overlap audit = COMPLETE
external UBQ-2 dependency gate = CLEARED
R12-F production CLAIM = HOLD
current release condition =
  close P1 lease/fence activation atomicity
  + close P1 post-UBQ2 TaskBudget incarnation freeze
  + fresh exact-head Architecture GREEN/GREEN
  + independent replacement FINAL GREEN
  + F0 integration under applicable Wave #174 authority
  + fresh no-new-material-drift pre-claim check
merge authority = NONE
```

This F0 replacement remains a zero-production contract/evidence candidate. It does
not itself implement the atomic lease handoff, TaskBudget plan-generation parameter,
pre-dispatch active-fence wiring or any R12-F production stage.

If another active track changes recovery safe-point, invocation reconciliation,
ResumeClaim, TaskBudget incarnation, lease/fence authority, principal, continuation
routing or activation authority, this freeze must be re-audited before any later
production CLAIM.
