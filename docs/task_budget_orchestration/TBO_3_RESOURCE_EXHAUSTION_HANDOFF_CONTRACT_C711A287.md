# TBO-3 — UBQ resource-exhaustion handoff contract freeze

**Canonical workspace:** Issue #333  
**Repository policy:** Issue #85 v2.5  
**Independent zero-production PRE-CLAIM:** #6033478126 / PASS / RELEASED  
**Owner zero-production CLAIM:** #6033482232 / ACTIVE  
**Development baseline:** `main@c711a2870d6d68be6cafc3d8c29a53fb834b96eb`  
**Baseline Architecture:** #2267 / 37586851733 / GREEN-GREEN  
**Class:** CONTRACT + ARCHITECTURE EVIDENCE ONLY  
**Production/runtime/schema/migration/repository/API/client delta:** ZERO  
**Production PRE-CLAIM:** HOLD / NOT RELEASED

## 1. Purpose

TBO-3 freezes the contract by which canonical UBQ resource exhaustion can be
consumed by Task orchestration without transferring renewable resource authority
into TBO.

The roadmap deliverable remains:

```text
TBO-3
= resource-exhaustion handoff from UBQ into Task continuation policy

gate
= UBQ compatibility + no-mint tests
```

This freeze does not implement resource continuation. It defines the only
contract a later production PRE-CLAIM may consume.

Canonical predecessor state:

```text
TBO-0 / #150 = COMPLETE / LANDED / CANONICAL / HEALTHY
TBO-1 / #315 = COMPLETE / LANDED / CANONICAL / HEALTHY
TBO-2 / #321 = COMPLETE / LANDED / CANONICAL / HEALTHY
TBO-2 merge / current baseline = c711a2870d6d68be6cafc3d8c29a53fb834b96eb

UBQ-3 / #145 = COMPLETE / CANONICAL
UBQ-4 / #146 = COMPLETE / CANONICAL
UBQ-6 / #148 = COMPLETE / LANDED / CANONICAL / HEALTHY
UBQ-7 / #149 = RESERVED / HOLD
```

## 2. Authority separation

The authority order remains:

```text
UBQ
  owns renewable user resource admission, reservation, settlement,
  window/policy authority and exhaustion truth

TBO
  consumes a resource-exhaustion disposition and decides Task continuation
  eligibility/policy only

AE
  owns AgentExecution state transitions, durable WAITING, checkpoints,
  ResumeClaim, recovery, lease/fencing and execution activation

AAT
  owns generic timer/event delivery when a later TBO stage requests scheduling
```

TBO-3 MUST NOT:

- create or select a UBQ policy;
- create, roll, reset, refill, refund, settle or close a UBQ window;
- create or mutate a UBQ reservation;
- reinterpret saturated TaskBudget compatibility counters as renewable quota;
- create an AE durable WAITING checkpoint;
- issue or consume ResumeClaim;
- schedule an AAT timer;
- acquire TBO-4+ authority by adjacency.

## 3. Canonical UBQ exhaustion vocabulary consumed by TBO-3

TBO-3 recognizes only the following canonical UBQ exhaustion codes as
resource-exhaustion handoff inputs:

```text
USER_TOOL_QUOTA_EXHAUSTED
USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED
USER_INFERENCE_QUOTA_EXHAUSTED
USER_TOKEN_QUOTA_EXHAUSTED
USER_COMPUTE_QUOTA_EXHAUSTED
USER_COST_QUOTA_EXHAUSTED
```

No other error is resource exhaustion merely because its message mentions
budget, quota, limits, timeout, retry, cost, or capacity.

In particular, policy-authority conflicts, owner-resolution failures,
idempotency conflicts, estimator failures, provider timeouts, TaskBudget
structural guard failures, execution-local tool budgets, and AE active-time
budgets remain separate taxonomies.

The original UBQ code is immutable provenance and MUST survive every TBO
projection. TBO may classify it but MUST NOT replace it with a generic code that
loses the original UBQ disposition.

## 4. Canonical handoff representation

A future production bridge must normalize a canonical immutable handoff with
these semantic fields:

```text
ResourceExhaustionHandoff
  source = UBQ
  original_code
  resource_scope = TASK | CAPABILITY
  resource_kind = TOOL_CALL | INFERENCE_CALL | TOKEN | COMPUTE | COST
  capability_id = optional; required for CAPABILITY scope
  owner_user_id = trusted resolved owner reference
  governing_policy_id = optional immutable reference
  governing_policy_version = optional immutable reference
  governing_policy_fingerprint = optional immutable reference
  window_epoch = optional immutable observation
  next_eligibility = KNOWN | UNKNOWN
  next_eligible_at = optional immutable timestamp; present only when KNOWN
  logical_operation_id = stable logical call/invocation identity when available
```

This is a handoff/projection contract, not a new durable schema claim. TBO-3
production must reuse an already authorized transport/domain shape or obtain a
separate schema/API PRE-CLAIM before adding persistence or wire fields.

The handoff is observational. Reading it MUST NOT advance UBQ state.

## 5. Exhaustion scope classification

TBO-3 freezes deterministic scope classification:

| UBQ code | TBO scope | Resource kind |
|---|---|---|
| `USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED` | `CAPABILITY` | `TOOL_CALL` |
| `USER_TOOL_QUOTA_EXHAUSTED` | `TASK` | `TOOL_CALL` |
| `USER_INFERENCE_QUOTA_EXHAUSTED` | `TASK` | `INFERENCE_CALL` |
| `USER_TOKEN_QUOTA_EXHAUSTED` | `TASK` | `TOKEN` |
| `USER_COMPUTE_QUOTA_EXHAUSTED` | `TASK` | `COMPUTE` |
| `USER_COST_QUOTA_EXHAUSTED` | `TASK` | `COST` |

Capability-scoped exhaustion denies that capability admission only. It does not
by itself make the whole Task resource-ineligible and does not manufacture Task
or Execution WAITING.

Task-scoped exhaustion means the requested logical operation cannot proceed
under the current UBQ authority. It requests TBO continuation-policy
evaluation; it does not itself dictate a state transition.

## 6. Current asymmetry and normalization target

Current production behavior is intentionally not changed by this freeze:

- tool quota exhaustion is converted by CapabilityRuntime into a QUOTA
  CapabilityError and then normalized by the Agent tool adapter into a failed
  ToolExecutionResult;
- inference quota exhaustion is raised before provider-owned work and is outside
  ProviderError/httpx fallback families; in ordinary Agent execution it reaches
  the generic Agent failure path.

These are different current runtime outcomes.

TBO-3 freezes the future semantic target:

```text
canonical UBQ exhaustion
  -> preserve original UBQ code
  -> classify CAPABILITY vs TASK scope
  -> create one TBO handoff disposition
  -> apply Task continuation policy exactly once
```

A future production implementation MUST NOT normalize these outcomes by
silently swallowing inference exhaustion, repeatedly retrying the same
resource-denied operation, or converting all capability-specific denial into
whole-Task suspension.

## 7. Task continuation disposition

TBO-3 defines these orchestration dispositions:

```text
CAPABILITY_DENIED
RESOURCE_DEFERRED
RESOURCE_CONTINUATION_UNAVAILABLE
```

### 7.1 CAPABILITY_DENIED

Used only for `USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED`.

Rules:

- preserve the failed logical tool-call result and original UBQ code;
- do not mutate Task status merely because one capability is exhausted;
- do not create AE WAITING;
- do not reserve another quota unit for the same logical replay;
- Agent continuation, if otherwise legal, may choose a different already
  authorized capability or finish without the denied capability.

### 7.2 RESOURCE_DEFERRED

Used for Task-scoped exhaustion only when a separately authorized continuation
path can preserve durable work safely.

`RESOURCE_DEFERRED` is a TBO continuation disposition. It is not a new
AgentTaskStatus or AgentExecutionState.

A production implementation may project this disposition into durable state
only under the state-owner authority described below.

### 7.3 RESOURCE_CONTINUATION_UNAVAILABLE

Used when Task-scoped exhaustion occurs but the required durable continuation
authority/evidence is unavailable.

This is fail-closed. TBO must not invent a WAITING checkpoint, a fake next
eligibility timestamp, a new Execution, a new quota window, or a retry loop to
avoid the missing authority.

## 8. Task and Execution state semantics

TBO-3 freezes two lifecycle lanes.

### Lane A — exhaustion before a new Execution is activated

When Task-scoped UBQ exhaustion is known at a pre-activation admission seam:

```text
Task stays in its current legal nonterminal pre-activation state
no AgentExecution is created
no Task revision/status mutation is required by the handoff itself
TBO returns RESOURCE_DEFERRED or RESOURCE_CONTINUATION_UNAVAILABLE
```

TBO-3 does not use Task `WAITING` merely to encode an eligibility denial.

### Lane B — exhaustion while an AgentExecution is already RUNNING

A running Execution may become durable `WAITING/RESOURCE` only through
AE-owned authority and only after a separately released AE/TBO bilateral
production gate.

Until such a gate exists:

```text
TBO-3 handoff alone cannot transition AgentExecution RUNNING -> WAITING
TBO-3 handoff alone cannot publish a Task WAITING projection
TBO-3 handoff alone cannot create a checkpoint or ResumeClaim
```

A later production bridge must either:

1. obtain AE bilateral authority and atomically publish canonical
   `AgentExecutionState.WAITING` with
   `AgentExecutionWaitReason.RESOURCE`, checkpoint/revision and Task
   projection; or
2. use a separately audited non-WAITING continuation boundary.

There is no third implicit state-machine path.

## 9. AE RESOURCE-WAITING bilateral boundary

`AgentExecutionWaitReason.RESOURCE` already exists. Its existence is not TBO
authority.

If a later TBO-3 production PRE-CLAIM proposes `WAITING/RESOURCE`, the fresh
bilateral release must freeze at least:

- exact RUNNING -> WAITING transition owner;
- one atomic durable revision/checkpoint boundary;
- Task WAITING projection semantics;
- remaining active-time budget persistence;
- ResumeClaim ownership and claim idempotency;
- retry/fork/resume lineage behavior;
- cancellation and terminal winner precedence;
- stale owner/lease fencing;
- unknown external tool outcome reconciliation before replay;
- no duplicate Execution creation on resource wake/resume.

TBO cannot write these semantics into AE production paths under the current
zero-production claim.

## 10. Committed-result authority

Resource exhaustion never invalidates already committed work.

Rules:

- a committed ToolExecutionResult remains authoritative;
- a committed inference result/usage settlement remains authoritative;
- unknown external tool outcome remains an AE-R6/R7 reconciliation concern;
- TBO must not replay a committed logical operation merely because a later
  resource admission was denied;
- TBO must not refund prior UBQ usage merely because the Task is deferred;
- idempotent replay must resolve to the same durable reservation/result where
  UBQ/AE contracts already require that behavior.

## 11. Next-eligibility contract

TBO may consume next-eligibility information only as an immutable observation
from UBQ authority.

Two outcomes are canonical:

```text
next_eligibility = KNOWN
  next_eligible_at = trusted immutable UBQ-provided timestamp

next_eligibility = UNKNOWN
  next_eligible_at = absent
```

TBO MUST NOT calculate or force next eligibility by:

- rolling a UBQ window;
- creating a new window;
- resetting counters;
- adding window_duration_seconds to a locally guessed anchor;
- substituting Task horizon, response timeout, provider timeout, retry backoff,
  connection timeout, or AE wait TTL;
- minting a new session/client/task/execution identity.

If UBQ cannot expose an immutable next-eligible observation without mutation,
TBO records `UNKNOWN`.

## 12. Window and policy reference rules

If UBQ includes window/policy references in the handoff, they are evidence only:

- `owner_user_id` identifies the already resolved renewable budget owner;
- `window_epoch` identifies the observed window;
- policy identity/version/fingerprint identify the governing immutable policy;
- TBO cannot select `next_policy_id`;
- TBO cannot reinterpret a policy reference as permission to roll;
- a later UBQ window may make a future admission eligible, but that fact is
  established by UBQ on a future admission, not by TBO mutating quota state.

A Task retry, fork, resume, different client, different connection, or new
Execution preserves the same applicable user resource authority and MUST NOT
create fresh quota merely because orchestration identity changed.

## 13. Task horizon and review horizon ordering

TBO-2 remains authoritative for Task activation fences.

When both Task policy and resource continuation matter:

```text
TBO Task horizon/review eligibility
  -> UBQ resource admission
  -> AE activation
```

If a deferred Task later reaches a known resource eligibility time, a future
activation must re-evaluate TBO-2 horizons before new UBQ/AE admission.

Known resource eligibility MUST NOT extend `task_horizon_at` or
`review_horizon_at`.

If `next_eligible_at >= task_horizon_at` for a finite Task, the resource
timestamp does not override `TASK_HORIZON_EXPIRED`.

## 14. AAT boundary

TBO-3 does not schedule timers.

A known `next_eligible_at` is only continuation-policy evidence.

Automatic timer/event delivery belongs to TBO-5 / AAT handoff and requires its
own later authority.

Therefore TBO-3 must not:

- create an AAT event;
- register a timer;
- enqueue a wakeup;
- infer TBO-5 authority from a KNOWN timestamp.

## 15. No-mint / no-reset / no-refund acceptance matrix

A later TBO-3 production candidate must prove at least:

| Case | Required invariant |
|---|---|
| tool total quota exhausted | no new UBQ window/reservation; original code preserved |
| capability tool quota exhausted | capability-only denial; no Task WAITING by itself |
| inference-call quota exhausted | no provider dispatch and no quota mutation by TBO |
| token quota exhausted | no provider dispatch and no quota mutation by TBO |
| compute quota exhausted | no provider dispatch and no quota mutation by TBO |
| cost quota exhausted | no provider dispatch and no quota mutation by TBO |
| handoff replay | no duplicate UBQ reservation or continuation transition |
| retry same logical operation | does not mint fresh user quota |
| fork | does not mint fresh user quota |
| resume | does not mint fresh user quota |
| new client/session/connection | does not mint fresh user quota |
| known next eligibility | observation only; no early rollover |
| unknown next eligibility | no guessed timestamp or synthetic timer |
| TaskBudget compatibility counters | remain saturated/read-only for canonical UBQ resources |
| prior committed result | remains authoritative; no refund/replay |
| terminal Task winner | remains terminal; no resource resurrection |

"No mutation by TBO" includes no mint, reset, refill, rollover, refund,
settlement, policy selection or counter decrement.

## 16. Idempotency key boundary

Continuation processing must have a stable identity derived from already
canonical logical identities; it MUST NOT use process attempt number, connection
generation, client id, session reconnect, or worker identity as a new quota
identity.

For a given logical exhaustion handoff and durable Task decision:

```text
same logical handoff
  -> same continuation decision identity
  -> at most one durable continuation transition
  -> zero duplicate UBQ reservation
```

The exact durable idempotency representation is a later production design and
requires its own path-level PRE-CLAIM.

## 17. Failure ordering

The following ordering remains deterministic:

1. terminal Task/Execution winners remain authoritative;
2. TBO-2 horizon/review fences apply at activation boundaries;
3. UBQ owns current resource admission/exhaustion truth;
4. TBO consumes canonical exhaustion and chooses continuation disposition;
5. AE owns any legal Execution state transition;
6. AAT may later deliver a separately authorized timer/event.

No later layer may retroactively mint authority for an earlier layer.

## 18. Production PRE-CLAIM requirements

A later TBO-3 production PRE-CLAIM may be considered only after this contract
candidate reaches independent FINAL PASS.

That production audit must enumerate exact runtime paths and separately prove:

- where tool and inference exhaustion are normalized into the same TBO handoff;
- whether capability-only denial remains a ToolExecutionResult path;
- whether Task-scoped exhaustion uses AE WAITING/RESOURCE or another audited
  continuation boundary;
- any required AE bilateral disposition;
- how immutable UBQ window/policy/next-eligibility evidence is obtained without
  mutation;
- exact idempotency/atomicity seam;
- no AAT scheduling;
- no UBQ mutation;
- no schema/migration/API/client expansion unless separately claimed.

## 19. Explicit non-scope

This zero-production freeze grants no authority for:

- any `se/src/**` or `cl/src/**` production edit;
- SQL/Alembic/schema/model/repository change;
- UBQ policy/window/reservation mutation;
- TaskBudget renewable resource checks;
- AgentExecution state-machine/WAITING/ResumeClaim/recovery edits;
- Task status mutation implementation;
- AAT timer/event scheduling;
- Gateway/UI projection;
- TBO-4 activation service;
- TBO-5 AAT handoff;
- TBO-6 Gateway/UI projection;
- TBO-7 integration matrix;
- TBO-8 rollout/exit;
- UBQ-7 destructive cleanup or rollout authority.

## 20. Exact owned scope

This zero-production claim owns exactly two NEW files:

1. `docs/task_budget_orchestration/TBO_3_RESOURCE_EXHAUSTION_HANDOFF_CONTRACT_C711A287.md`
2. `se/tests/architecture/test_tbo3_resource_exhaustion_handoff_contract.py`

No third path is authorized.

## 21. Contract-freeze exit gate

This contract/evidence slice reaches FINAL GREEN only when:

1. changed scope remains exactly two NEW files;
2. production/runtime/schema/migration/repository/API/client delta remains ZERO;
3. all five Issue #333 readiness P1s are resolved by explicit contract language;
4. architecture evidence binds the canonical UBQ error vocabulary and current
   runtime asymmetry without implementing behavior;
5. architecture evidence binds AE RESOURCE-WAITING as external state authority;
6. architecture evidence binds TaskBudget resource demotion/no-mint boundaries;
7. exact-head Linux + Windows Architecture is GREEN/GREEN;
8. independent contract FINAL finds no blocking P0/P1/P2;
9. current-main drift is classified under Policy #85 v2.5.

Only after that may a fresh independent audit consider releasing a separate
production PRE-CLAIM.

No production authority is implied by a GREEN contract freeze.
