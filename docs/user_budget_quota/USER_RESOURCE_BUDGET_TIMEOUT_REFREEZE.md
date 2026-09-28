# User Resource Budget + Timeout Re-freeze

**Namespace:** `UBQ-*`  
**Repository:** `boxs-51/assistant`  
**Contract baseline:** `main@206334044308a532f70384a1eeb4860cc9439f86` (2026-09-28)  
**Policy:** Issue #85 v2.5 / FOLLOW-LATEST for future stages  
**State:** CONTRACT / COORDINATION FREEZE ONLY  
**Production/schema/migration/runtime authority:** CLOSED until stage-specific CLAIM + audit + CI release

---

## 1. Superseding decision

This document supersedes **future budget semantics** without rewriting historical AE evidence.

The repository currently uses the word `budget` for multiple independent concepts:

- AE-R4 active execution time;
- AE-R5/R8/R9 durable `TaskBudget` counters and reservations;
- AE-R10 `ProviderCallBudget` deadline/retry control;
- task horizon / periodic allowance planning in TBO;
- token, cost, inference and tool-call resource limits.

Future architecture MUST separate these concerns:

```text
RESOURCE BUDGET     = renewable resource quota owned by authenticated user
EXECUTION GUARDS    = structural/concurrency bounds for Task/Execution runtime
TIMEOUTS/DEADLINES  = bounded waiting and wall/active-time safety controls
RETRY GUARDS        = bounded retry/fallback control
ATTRIBUTION         = client/session/task/execution/capability dimensions
```

Historical documents, migrations, completion evidence, finding IDs and checkpoint hashes remain valid evidence of the implementation that existed at their phase. They are not edited retroactively merely to adopt the new terminology.

---

## 2. Canonical ownership

### 2.1 Budget owner

The canonical renewable resource-budget domain is still **user-owned**, but raw `Identity.user_id` is not a universal authentication invariant. Current API-key authentication may produce a trusted `Identity` with `api_key_id` / `organization_id` and no `user_id`.

UBQ therefore freezes a server-resolved owner:

```text
budget_owner_user_id = resolve_budget_owner(identity)
```

Resolution MUST be fail-closed and MUST NOT trust client-supplied owner identifiers:

1. If trusted `Identity.user_id` is present, it is the `budget_owner_user_id`.
2. For a standard user API key without `Identity.user_id`, resolve the authenticated `api_key_id -> application -> organization -> Organization.owner_id` relationship from server-side durable data. The result is the `budget_owner_user_id`. The authenticated organization/application relation must match the resolved chain.
3. For `admin_key`, guest, service, or any other principal that has no uniquely resolvable user owner, UBQ-governed resource admission MUST fail closed with `USER_BUDGET_OWNER_UNRESOLVED` until a separately frozen non-user/service-principal budget contract exists.
4. `api_key_id`, `organization_id`, `application_id`, client/session/task/branch/execution IDs, and the literal value `null` MUST NOT silently become independent renewable user-budget owners.
5. An organization/application/API-key policy may impose an additional higher-level ceiling, but it is additive policy and does not silently replace the resolved user owner.

This rule preserves user ownership while keeping API-key authentication compatible through trusted server-side owner resolution.

### 2.2 Attribution dimensions

```text
budget_owner_user_id = canonical renewable resource-quota owner
Identity.user_id     = direct trusted owner candidate when present
api_key_id           = authenticated credential attribution / owner-resolution input
organization_id      = trusted organization attribution / optional higher-level ceiling
application_id       = application attribution / optional higher-level ceiling
client_id            = consumer / attribution / optional sub-limit
connection_id        = transport affinity only
session_id           = conversation attribution
task_id              = workload attribution
branch_id            = branch attribution
execution_id         = runtime attribution
invocation_id        = logical tool/inference idempotency attribution
capability_id        = canonical per-tool quota key
```

Opening another client, connection, session, Task, branch or Execution MUST NOT mint a fresh user resource allowance.

### 2.3 Task ownership after re-freeze

A Task does not own renewable user quota. A Task may own execution/lifecycle policy and structural guards.

```text
UserResourceBudget
       |
       +--> Task A
       |      +--> Execution(s)
       |
       +--> Task B
       |      +--> Execution(s)
       |
       +--> Agent/AAT/AIC activation
```

All admitted work for the same authenticated user consumes the same active user budget window unless an explicit higher-level policy says otherwise.

---

## 3. Canonical separation of concerns

### 3.1 UserResourceBudget

Renewable dimensions may include:

```text
max_compute_units
max_inference_calls
max_input_tokens
max_output_tokens
max_total_tokens
max_tool_calls_total
max_tool_calls_by_capability
max_cost_usd                  optional
```

The domain MUST support `used` and `reserved` accounting where concurrent work can oversubscribe a quota.

### 3.2 TaskExecutionGuards

The following are NOT renewable user-resource budget:

```text
max_total_executions
max_active_executions
max_active_branches
max_parallel_agents
max_parallel_tools
max_delegation_depth
max_iterations_per_execution
```

These are structural/runtime safety guards. Their lifecycle may be Task- or Execution-scoped and is owned by Agent execution contracts.

### 3.3 Timeouts and deadlines

Time limits are not resource budget.

Canonical timeout taxonomy is defined in section 9 and in `docs/agent_timeout_contract.md`.

### 3.4 Provider retry/fallback

AE-R10 retry/fallback semantics remain authoritative. A logical provider call may use deadline and retry controls, but these are not user resource budget. Future naming should migrate away from `ProviderCallBudget` toward a guard/control name while preserving behavior and compatibility until explicitly released.

---

## 3A. Exact-head implementation inventory

**Inventory baseline:** `main@206334044308a532f70384a1eeb4860cc9439f86`  
**Scope:** executable production/client surfaces plus immutable SQL migrations. Historical docs/tests are evidence but are not counted as live authority in this inventory.

This inventory freezes where the existing TaskBudget and timeout/deadline semantics are consumed before any UBQ production migration. A later stage MUST re-run this inventory against its exact claimed main.

### 3A.1 TaskBudget authority and consumers

| Surface | Exact current-head paths | UBQ-0 disposition |
|---|---|---|
| Domain representation | `se/src/domain/schemas/task_budget.py` | **MIGRATE later**; current TaskBudget representation remains executable compatibility authority |
| SQL model/repository | `se/src/infrastructure/storage/models/sql/agent/task_budget.py`, `se/src/infrastructure/storage/models/sql/agent/__init__.py`, `se/src/infrastructure/storage/repositories/agent.py` | **MIGRATE later**; no destructive change in UBQ-0 |
| Immutable migrations | `se/src/infrastructure/storage/migrations/sql/versions/11a_r5_task_budget.py`, `12a_r6_remote_reconciliation.py`, `14b_r8_root_branch_accounting.py` | **KEEP** immutable history |
| Configuration / composition root | `se/config/default.yaml`, `se/src/infrastructure/config/schemas.py`, `se/src/infrastructure/config/__init__.py`, `se/src/application/container.py`, `se/src/main.py` | **MIGRATE later**; split live `agent.task_budget` resource defaults from Task execution guards during dual accounting/DI |
| TaskBudget service/runtime | `se/src/runtimes/agent/task_budget.py`, `se/src/runtimes/agent/runtime.py`, `se/src/runtimes/agent/coordinator.py`, `se/src/runtimes/agent/persistence.py`, `se/src/runtimes/agent/waiting_checkpoint.py`, `se/src/runtimes/agent/__init__.py` | **MIGRATE later**; existing execution semantics remain active |
| Fork/retry/branch/aggregate contracts | `se/src/runtimes/agent/contracts/fork.py`, `retry.py`, `branch_resolution.py`, `aggregate.py`, `se/src/runtimes/agent/fork_planning.py`, `retry_planning.py` | **KEEP semantics / MIGRATE resource authority later** |
| Capability / nested Agent path | `se/src/runtimes/capability/drivers/agent_driver.py` | **MIGRATE later**; nested work cannot mint UBQ |
| GC / retention | `se/src/runtimes/agent/gc_dry_run.py`, `se/src/runtimes/agent/gc_executor.py` | **KEEP until durable UBQ retention contract exists** |
| Gateway multi-Agent path | `se/src/transport/gateway/api/v1/multi_agent_router.py` | **MIGRATE later** if it projects TaskBudget state/resource data |

The current TaskBudget system therefore remains a cross-cutting compatibility authority. UBQ-0 does not rename, remove, rewrite, or reinterpret persisted TaskBudget rows.

### 3A.2 Timeout/deadline authority and call sites

| Surface | Exact current-head paths | Frozen boundary |
|---|---|---|
| Server DTO/request limits | `se/src/domain/schemas/agent_execution.py`, `se/src/domain/schemas/request.py`, `se/src/domain/schemas/capability.py`, `se/src/infrastructure/config/schemas.py`, `se/src/main.py` | current `AgentExecutionLimits` and `timeout_seconds` fields remain compatibility API |
| Agent runtime/context | `se/src/runtimes/agent/contracts/context.py`, `policy.py`, `inference.py`, `se/src/runtimes/agent/runtime.py`, `coordinator.py`, `persistence.py`, `resume_planning.py`, `fork_planning.py`, `retry_planning.py`, `task_budget.py`, `system_prompt.py` | execution/iteration/task time controls are guards/deadlines, not renewable quota |
| Agent adapters/tool errors | `se/src/runtimes/agent/adapters/inference.py`, `se/src/runtimes/agent/adapters/tool.py`, `se/src/runtimes/agent/tool_execution/errors.py`, `se/src/runtimes/agent/tool_execution/__init__.py` | legacy timeout/error codes remain compatibility surfaces until UBQ-5 |
| Provider logical-call deadline | `se/src/provider/retry_contracts.py`, `se/src/provider/executor.py`, `se/src/provider/policies/retry.py`, `se/src/provider/handlers/base.py`, `se/src/provider/handlers/chat_handler.py` | AE-R10 remains owner of retry/fallback and `ProviderCallBudget` behavior |
| Capability runtime/drivers | `se/src/runtimes/capability/runtime.py`, `composition.py`, `contracts/context.py`, `drivers/agent_driver.py`, `drivers/skill_driver.py` | current capability hard timeout remains executable compatibility behavior |
| Workflow/Gateway response | `se/src/runtimes/workflow/runtime.py`, `se/src/transport/gateway/api/v1/chat_router.py`, `se/src/transport/gateway/api/v1/capability_router.py` | current Agent timeout projection and `GATEWAY_RESPONSE_TIMEOUT` remain public compatibility behavior |
| Client DTO/runtime/UI | `cl/src/schemas/agent_execution.py`, `cl/src/schemas/request.py`, `cl/src/core/agent_engine.py`, `cl/src/core/tool_executor.py`, `cl/src/mcp_client/mcp_adapter.py`, `cl/src/ui/bridge.py` | client fields remain compatible until server/client migration is released together |

Known executable timeout/error vocabulary at this baseline includes `AGENT_TASK_TIMEOUT`, `AGENT_EXECUTION_TIMEOUT`, `AGENT_ITERATION_TIMEOUT`, `AGENT_INFERENCE_TIMEOUT`, `AGENT_TOOL_TIMEOUT`, `AGENT_CONTEXT_TIMEOUT`, `CAPABILITY_TIMEOUT`, and `GATEWAY_RESPONSE_TIMEOUT`.

### 3A.3 Inventory gate

UBQ-1/UBQ-5 claims MUST fail closed if exact-main introduces a new TaskBudget consumer, timeout/deadline owner, or public error projection not dispositioned here. This inventory is evidence for UBQ-0 only; it is not permission to edit these production paths.

---

## 4. Usage-window reset contract

User resource quota resets through a durable **anchored usage window**.

### 4.1 Anchor semantics

The first admitted resource-consuming operation when no active window exists creates:

```text
window_started_at = authoritative_server_now
window_expires_at = window_started_at + policy.window_duration
epoch              = previous_epoch + 1
```

The reset is therefore anchored to actual use, not to a fixed calendar boundary unless a future policy explicitly selects calendar windows.

Example:

```text
first admitted use: 2026-09-28 15:42 +07
duration:           24h
expires:            2026-09-29 15:42 +07
```

### 4.2 Lazy rollover

No cron job is required merely to zero counters.

On admission:

```text
if no active window:
    atomically create first window
elif now < expires_at:
    use current window
else:
    atomically close expired window
    atomically create next window
```

Two concurrent callers at expiry MUST converge to one new epoch.

### 4.3 Preserve history

Do not erase historical usage by updating the same row back to zero.

Preferred representation:

```text
UserBudgetAccount
  +-- UserBudgetWindow epoch N-2 CLOSED
  +-- UserBudgetWindow epoch N-1 CLOSED
  +-- UserBudgetWindow epoch N   ACTIVE
```

Retention/aggregation policy may compact historical windows later, but active admission must never depend on destructive history reset.

---

## 5. Conceptual durable representation

Exact SQL names are frozen only at the implementation stage after a fresh migration-head audit. The semantic model is:

### 5.1 UserBudgetPolicy

Policy records referenced by an active or historical window are immutable/versioned authority. A user may have a mutable pointer to the policy selected for the **next** window, but recovery/admission for an existing epoch must resolve the exact durable policy identity captured by that window.

```text
policy_id
owner_user_id
policy_version
policy_fingerprint
window_duration_seconds

max_compute_units

max_inference_calls
max_input_tokens
max_output_tokens
max_total_tokens

max_tool_calls_total
default_per_tool_limit
tool_limits[capability_id]

max_cost_usd optional

optional user-level concurrency ceilings
revision
```

### 5.2 UserBudgetWindow

Every window is durably bound to the policy that governed creation of that epoch. A later policy edit MUST NOT make crash recovery consult mutable current configuration and reinterpret an existing window.

```text
owner_user_id
epoch
state = ACTIVE | CLOSED

governing_policy_id
governing_policy_version
governing_policy_fingerprint

started_at
expires_at

compute_used
compute_reserved

inference_used
inference_reserved

input_tokens_used
output_tokens_used
total_tokens_used
tokens_reserved

tool_calls_used
tool_calls_reserved

cost_used
cost_reserved

revision
created_at
updated_at
closed_at
```

### 5.3 Per-tool usage

Per-tool accounting is keyed by canonical `capability_id`:

```text
UserToolBudgetUsage
-------------------
user_id
window_epoch
capability_id
used_calls
reserved_calls
revision
```

Display name, provider-native function name, alias, UI label or connection-local handle MUST NOT be the quota key.

Policy/window invariants:
- `owner_user_id` on policy/window MUST equal the resolved canonical budget owner;
- a window captures `governing_policy_id/version/fingerprint` atomically with epoch creation;
- a policy record referenced by a window is immutable or content-addressed/versioned so its fingerprint remains reproducible;
- normal policy changes apply to the next created epoch by default; changing limits of an already-active epoch requires a separately frozen explicit override/revocation contract;
- R12 recovery, retry/reconnect and reservation settlement always use the window's captured governing policy identity, never a mutable default policy.

### 5.4 Reservation receipt

Resource reservation and settlement need an idempotent receipt carrying attribution:

```text
reservation_id
owner_user_id
window_epoch

client_id optional
session_id optional
task_id optional
branch_id optional
execution_id optional
invocation_id optional
capability_id optional

resource_kind
reserved_amount
settled_amount
state
idempotency_key
revision
```

---

## 6. Admission, reservation and settlement

### 6.1 Admission before side effect

Resource-consuming execution MUST be admitted before dispatch.

```text
resolve authenticated principal
  -> resolve canonical budget_owner_user_id or fail closed
  -> resolve immutable governing policy
  -> resolve/create active budget window bound to policy identity
  -> evaluate user quota
  -> evaluate optional client sub-limit
  -> evaluate Task/Execution guards
  -> atomically reserve
  -> dispatch
  -> settle actual usage
  -> release unused reservation
```

### 6.2 Concurrency invariant

A read-only "remaining" check is insufficient. Concurrent callers must reserve under a transaction/CAS/lock boundary so several Agents cannot all consume the same remaining allowance.

### 6.3 Unknown outcome

Budget exhaustion or timeout does not imply an external side effect did not happen.

Unknown tool outcome MUST reuse AE-R6 reconciliation before replay. A reservation may remain pending/unknown until canonical reconciliation resolves it. Recovery must never create a duplicate logical charge merely because an ACK was lost.

---

## 7. Tool-call quota contract

### 7.1 Two independent limits

Every logical tool invocation is constrained by both:

```text
max_tool_calls_total
max_tool_calls_by_capability[capability_id]
```

Effective availability is the minimum remaining authority.

### 7.2 Atomic dual reservation

For a tool with a configured per-capability limit, one admission atomically reserves:

```text
TOTAL_TOOL_CALL +1
CAPABILITY_TOOL_CALL(capability_id) +1
```

Both succeed or neither succeeds.

### 7.3 Logical invocation charging

Retry/reconnect/reconciliation of the same logical `invocation_id` MUST NOT consume an additional user tool-call unit.

A new Agent-created logical invocation consumes a new unit.

### 7.4 Client tools

A CLIENT-located capability still consumes the authenticated user's quota. `origin_client_id` is attribution/hard-affinity context, not a new budget owner.

---

## 8. Inference, token and compute quota

### 8.1 Logical inference call

One logical inference consumes one `inference_call` reservation even when AE-R10 executes provider retry/fallback attempts.

Provider attempts are separately observable metrics and do not automatically equal user-visible inference-call units.

### 8.2 Token accounting

Canonical accounting should support:

```text
input_tokens_used
output_tokens_used
cached_input_tokens_used optional
reasoning_tokens_used optional
total_tokens_used
```

Providers that cannot report a dimension may leave it unknown/unsupported according to a versioned normalization policy. Unknown must not silently become zero where that would weaken quota safety.

### 8.3 Compute units

`compute_units` is a provider/model-normalized resource abstraction. The domain contract stores/limits normalized units; provider/model adapters own the mapping from actual usage to compute units.

The exact formula is deliberately NOT frozen here.

A future policy may weight:

```text
input tokens
output tokens
reasoning units
multimodal units
model/provider class
```

without changing user-budget ownership.

### 8.4 Estimate then settle

Where actual usage is unknowable before dispatch:

```text
reserve bounded estimate
-> execute
-> settle actual usage
-> release unused reservation
```

The implementation contract must define overrun behavior before production release.

---

## 9. Canonical timeout contract

Timeout semantics are independent from resource quota.

### 9.1 Provider timeouts

```text
provider_first_response_timeout_seconds
    maximum wait from dispatch until first valid provider response/progress

provider_stream_idle_timeout_seconds
    maximum interval with no meaningful provider progress after streaming begins

provider_call_timeout_seconds
    hard deadline for one logical provider inference including retry/fallback
```

Transport heartbeat alone MUST NOT count as provider progress.

### 9.2 Tool timeouts

```text
tool_call_timeout_seconds
    hard bound for one logical tool invocation

tool_idle_timeout_seconds
    maximum interval without meaningful tool progress where supported
```

Timeout does not prove side-effect absence.

### 9.3 Response timeouts

```text
response_idle_timeout_seconds
    maximum interval without meaningful user-visible/runtime progress

response_hard_timeout_seconds
    hard wall-clock ceiling for one synchronous response lifecycle
```

Long-running durable Task lifetime is not required to equal response lifetime.

```text
Task lifetime != synchronous HTTP/SSE response lifetime
```

A long Task may checkpoint/WAIT and continue through later activation/reconnect while the original response has already closed.

### 9.4 Iteration/execution safety guards

Existing:

```text
timeout_seconds
iteration_timeout_seconds
inference_timeout_seconds
tool_timeout_seconds
remaining_active_budget_seconds
```

remain compatibility fields until migration. New documentation/code must not describe them as renewable user quota.

---

## 10. Budget reset vs timeout

Budget window rollover:

- replenishes only resource dimensions authorized by the new user window;
- does not extend a running provider/tool/response timeout;
- does not reset retry counters inside an already-started logical provider call;
- does not resurrect a terminal Task/Execution;
- does not imply unknown tool/provider side effects may be replayed;
- does not grant a new Task structural guard allowance unless that guard's own lifecycle says so.

Timeout expiry:

- does not reset or renew user budget;
- settles/refunds reservations only according to the resource accounting rule and known execution outcome;
- does not mint a new window;
- may trigger WAITING/checkpoint/reconciliation according to AE ownership.

---

## 11. R12 crash/recovery invariants

AE-R12 remains owner of execution lease/recovery. UBQ must be consumed, not redefined, by recovery.

Crash recovery MUST NOT:

```text
reset a user budget window
create a new window before normal rollover eligibility
refund committed usage
double-charge an already admitted logical invocation
mint resource authority because a lease expired
```

Stale lease/recovery may reconstruct reservation state only from durable canonical accounting and reconciliation evidence.

Any UBQ production change touching R12 recovery paths is MATERIAL drift for Issue #107 and requires cross-issue notice/audit under Policy #85.

---

## 12. TBO/AAT/AIC boundaries

### UBQ

Owns:

- user resource policy;
- anchored renewable window/epoch;
- resource reservation/settlement;
- total/per-capability tool quotas;
- inference/token/compute/cost accounting;
- optional client sub-limits;
- budget exhaustion response fields.

### TBO

After this re-freeze, TBO means **Task Orchestration**, not user resource budget.

TBO may own:

- finite/recurring Task policy;
- Task horizon/review horizon;
- Task lifecycle admission coordination;
- WAITING/activation orchestration where separately released.

TBO consumes UBQ admission; it does not mint UBQ quota.

### AAT

Owns timers/events/automation delivery. It requests activation; it cannot mint UBQ quota.

### AIC

Owns Agent-to-Agent communication. Communication that activates work goes through applicable AE/TBO/UBQ admission.

---

## 13. Historical compatibility and file disposition

The labels below describe future treatment, not immediate deletion of historical evidence.

| Surface | Disposition | Contract |
|---|---|---|
| `docs/agent_execution_r4/**` historical completion/freeze docs | **KEEP** | immutable phase evidence; terminology historical |
| `docs/agent_execution_r5/**` historical TaskBudget docs | **KEEP** | immutable phase evidence; future authority superseded only by explicit migration |
| `docs/agent_execution_r8/**`, `r9/**` TaskBudget evidence | **KEEP** | preserve fork/retry/accounting evidence |
| `docs/agent_execution_r10/**` | **KEEP** | preserve deadline/retry/fallback semantics |
| `docs/task_budget_orchestration/TBO_ROADMAP_CONTRACT_FREEZE.md` | **SUPERSEDE/REWRITE** | TBO becomes Task Orchestration; UBQ owns user resource quota |
| `docs/agent_timeout_contract.md` | **SUPERSEDE/REWRITE** | timeout-only taxonomy + compatibility mapping |
| `docs/ROADMAP_NAMESPACE_REGISTRY.md` | **MIGRATE** | add UBQ namespace; redefine future TBO name |
| `docs/agent_automation/AAT_ROADMAP.md` | **MIGRATE** | AAT uses TBO for Task eligibility/orchestration and UBQ for resource admission; no TBO quota minting |
| `docs/agent_interconnect/AIC_ROADMAP.md` | **MIGRATE** | AIC activation uses AE/TBO plus UBQ resource admission; TBO does not own renewable quota |
| `se/src/domain/schemas/task_budget.py` | **MIGRATE** | remain compatibility authority during dual-accounting; resource fields later demoted |
| `se/src/runtimes/agent/task_budget.py` | **MIGRATE** | dual-accounting/admission handoff before resource authority removal |
| `se/src/infrastructure/storage/models/sql/agent/task_budget.py` | **MIGRATE** | no destructive removal until compatibility stage |
| `11a_r5_task_budget.py` migration | **KEEP** | immutable migration history |
| `AgentTaskBudgetSettings` | **MIGRATE** | split user resource policy from Task execution guards |
| `se/config/default.yaml::agent.task_budget` | **MIGRATE** | current live defaults include tool/inference/token/cost and structural ceilings; UBQ stages must split resource defaults from Task guards without leaving duplicate authority |
| `AgentExecutionLimits` | **MIGRATE** | retain structural guards/timeouts; remove resource-budget terminology |
| `ProviderCallBudget` | **MIGRATE NAME/KEEP SEMANTICS** | retain R10 deadline/retry behavior; future compatibility alias |
| current `agent.budget.configure` | **SUPERSEDE** | future interface must not let Agent mint user quota |
| historical tests proving old contracts | **KEEP** | add migration/compat tests rather than deleting evidence |
| obsolete compatibility shims after final migration | **DELETE LATER ONLY** | deletion requires explicit exit gate and reference scan |

**Immediate DELETE set:** none.

No historical document, migration or compatibility code is deleted in the contract stage.

---

## 14. Migration strategy

No Big Bang replacement is permitted.

### UBQ-0 — Exact-head audit + contract freeze

Docs/architecture tests only:

- inventory TaskBudget consumers;
- inventory identity/client attribution;
- inventory timeout/deadline fields;
- freeze schema/API/error names;
- freeze cross-track ownership;
- prove zero production delta.

### UBQ-1 — Durable user budget representation

Add user budget policy/window/per-tool/reservation representation and migration.

No production admission switch yet.

### UBQ-2 — Dual accounting

Existing TaskBudget remains effective admission authority while eligible resource-consuming paths also write UBQ accounting.

Required reconciliation proves no missing/double charge.

### UBQ-3 — Tool quota authority

UBQ becomes canonical authority for:

- total logical tool calls;
- per-`capability_id` logical tool calls;
- idempotent invocation reservation/reconciliation.

TaskBudget tool counters remain compatibility/read model where required.

### UBQ-4 — Inference/token/compute authority

Move logical inference/token/compute/cost resource admission to UBQ.

AE-R10 provider retry/fallback remains unchanged and consumes one logical inference admission.

### UBQ-5 — Timeout semantic migration

Introduce explicit provider/tool/response timeout names and compatibility aliases. Add idle vs hard-deadline tests.

No resource reset semantics belong here.

### UBQ-6 — TaskBudget demotion

TaskBudget becomes Task execution/branch/delegation guard compatibility surface. Remove its authority over renewable user-resource quota after dual-accounting evidence is complete.

### UBQ-7 — Cross-track integration and exit

Fault/restart/concurrency matrix across:

- AE-R6 unknown tool outcome;
- R7 resume;
- R8 fork;
- R9 retry;
- R10 fallback;
- R12 crash recovery;
- AAT/AIC future activation;
- multiple clients for one user.

Only then may obsolete resource compatibility shims be proposed for deletion.

---

## 15. Required acceptance matrix

Before UBQ can close:

1. Same user, two Tasks, both consume one shared quota window.
2. Same user, two clients, both consume one shared quota window.
3. New connection/session does not create quota.
4. Two concurrent requests at rollover create exactly one next epoch.
5. Total tool and per-capability counters reserve atomically.
6. Retry/reconnect of the same `invocation_id` does not double-charge.
7. New logical tool invocation does charge again.
8. Provider retry/fallback remains one logical inference charge.
9. Token/compute estimate is settled once and unused reservation is released.
10. Crash after reservation but before settlement is recoverable without free or duplicate usage.
11. R12 stale lease recovery does not reset/mint/refund budget incorrectly.
12. Provider first-response timeout is distinct from provider stream-idle timeout.
13. Provider hard-call timeout bounds all retries/fallback.
14. Tool timeout does not claim unknown side effect is absent.
15. Response idle timeout detects a hung response.
16. Response hard timeout bounds a progressing but unbounded synchronous response.
17. Long-running Task can outlive an HTTP/SSE response through checkpoint/WAITING.
18. Expired resource window does not extend operation timeout.
19. Timeout does not automatically create a new resource window.
20. Historical TaskBudget data remains readable through migration/rollback policy.

---

## 16. Frozen public/internal error vocabulary

UBQ-0 freezes the vocabulary boundary below. These decisions are semantic/API authority for later stages; **UBQ-0 itself changes no executable error behavior**.

### 16.1 Canonical future public quota codes

Before resource-dimension admission, failure to resolve a unique canonical user owner projects:

| Public `error_code` | Exact meaning |
|---|---|
| `USER_BUDGET_OWNER_UNRESOLVED` | the authenticated principal cannot be mapped by trusted server authority to exactly one user-owned UBQ account; admission fails before a quota window is created or consumed |

When UBQ becomes the admitting authority for the named resource, the public `error_code` MUST be one of:

| Public `error_code` | Exact meaning |
|---|---|
| `USER_BUDGET_EXHAUSTED` | generic fallback only when a more specific exhausted dimension cannot be projected safely |
| `USER_COMPUTE_QUOTA_EXHAUSTED` | normalized compute-unit admission failed |
| `USER_TOKEN_QUOTA_EXHAUSTED` | token admission failed; API may add a structured token dimension without inventing another top-level code |
| `USER_INFERENCE_QUOTA_EXHAUSTED` | logical inference-call admission failed |
| `USER_TOOL_QUOTA_EXHAUSTED` | total logical tool-call quota admission failed |
| `USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED` | per-`capability_id` logical tool-call quota admission failed |
| `USER_COST_QUOTA_EXHAUSTED` | optional normalized cost admission failed |

The canonical public code is determined by the authority that actually denied admission. A Task, client, session, branch, execution, retry, reconnect, or lease expiry MUST NOT manufacture one of these codes unless UBQ admission rejected the resource.

### 16.2 Canonical future public timeout codes

When the corresponding explicit timeout is implemented under UBQ-5, public `error_code` is frozen as:

| Public `error_code` | `timeout_scope` |
|---|---|
| `PROVIDER_FIRST_RESPONSE_TIMEOUT` | `provider_first_response` |
| `PROVIDER_STREAM_IDLE_TIMEOUT` | `provider_stream_idle` |
| `PROVIDER_CALL_TIMEOUT` | `provider_call` |
| `TOOL_IDLE_TIMEOUT` | `tool_idle` |
| `TOOL_CALL_TIMEOUT` | `tool_call` |
| `RESPONSE_IDLE_TIMEOUT` | `response_idle` |
| `RESPONSE_HARD_TIMEOUT` | `response_hard` |

For timeout projections, `timeout_seconds` names the configured bound for the expired scope. Heartbeats do not change the semantic scope.

### 16.3 Existing executable vocabulary disposition

| Existing code/family on `main@2063340...` | Disposition | Exact rule |
|---|---|---|
| `TASK_BUDGET_ERROR` | **INTERNAL-ONLY / KEEP COMPAT** | base TaskBudget service exception; never becomes the canonical public UBQ exhaustion code |
| `FORK_TASK_BUDGET_REQUIRED`, `FORK_TASK_BUDGET_CLOSED` and other fork TaskBudget rejection codes | **INTERNAL-ONLY / KEEP** | fork planning/structural rejection; not an alias for user quota exhaustion |
| `RETRY_TASK_BUDGET_REQUIRED` and retry TaskBudget rejection codes | **INTERNAL-ONLY / KEEP** | retry planning/structural rejection; not an alias for user quota exhaustion |
| `AGENT_TOOL_BUDGET_EXCEEDED` | **PUBLIC-COMPAT / KEEP LEGACY MEANING** | legacy Agent execution-local tool-call guard. It MUST NOT be silently redefined as user UBQ exhaustion. UBQ denial projects `USER_TOOL_QUOTA_EXHAUSTED` or `USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED` |
| `CAPABILITY_TIMEOUT` | **PUBLIC-COMPAT** | remains valid for the existing capability runtime. After explicit UBQ-5 migration, newly distinguished hard/idle tool timeout surfaces project `TOOL_CALL_TIMEOUT`/`TOOL_IDLE_TIMEOUT`; compatibility adapters may preserve the legacy code only as a legacy projection |
| `CAPABILITY_EXECUTION_FAILED` | **KEEP** | generic capability execution failure; MUST NOT swallow a known UBQ exhaustion or canonical timeout scope |
| `AGENT_TASK_TIMEOUT` | **PUBLIC-COMPAT / KEEP** | current Task wall-clock/lifecycle deadline projection; it is not a quota error and is not automatically renamed by UBQ |
| `AGENT_EXECUTION_TIMEOUT` | **PUBLIC-COMPAT / KEEP** | current active-execution timeout/guard projection |
| `AGENT_ITERATION_TIMEOUT` | **PUBLIC-COMPAT / KEEP** | current iteration timeout projection |
| `AGENT_INFERENCE_TIMEOUT` | **PUBLIC-COMPAT / KEEP UNTIL UBQ-5** | current Agent inference timeout. Explicit provider first-response/idle/hard scopes supersede only when that migration is released |
| `AGENT_TOOL_TIMEOUT` | **PUBLIC-COMPAT / KEEP UNTIL UBQ-5** | current Agent tool timeout. Explicit tool hard/idle scopes supersede only when released |
| `AGENT_CONTEXT_TIMEOUT` | **PUBLIC-COMPAT / KEEP** | current context/iteration-derived timeout projection |
| `GATEWAY_RESPONSE_TIMEOUT` | **PUBLIC-COMPAT / SUPERSEDE LATER** | current non-streaming Gateway response-wait timeout; explicit response idle/hard codes supersede it only after UBQ-5 response migration |

### 16.4 Public projection invariants

1. **No semantic aliasing across authorities.** Owner-resolution failure, TaskBudget structural rejection, UBQ quota denial, timeout expiry, retry exhaustion and generic capability failure remain distinguishable.
2. **Owner resolution precedes quota mutation.** `USER_BUDGET_OWNER_UNRESOLVED` is not quota exhaustion and MUST be emitted before creating/rolling/reserving a budget window.
3. **Credential IDs are not quota owners.** Public or internal code MUST NOT silently substitute `api_key_id`, `organization_id`, `application_id`, or `null` for `owner_user_id`.
4. **Specific beats generic.** A known per-capability tool quota failure uses `USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED`, not `USER_BUDGET_EXHAUSTED`.
5. **Timeout is not quota.** No timeout code may reset/mint/refund UBQ by itself.
6. **Legacy codes are not silently repurposed.** Compatibility means preserving old meaning while new canonical fields/codes are introduced.
7. **Known canonical failure must not collapse to `CAPABILITY_EXECUTION_FAILED`.** Generic normalization is allowed only when the underlying semantic code is genuinely unavailable/unsafe to expose.
8. **Public identity boundary.** Error projection does not expose `user_id`, `client_id`, session/task/branch/execution identifiers merely to explain quota ownership. Such identifiers remain server-side attribution unless another API contract explicitly authorizes them.
9. **Production switch requires dual compatibility evidence.** A later stage changing the emitted `error_code` must test server, client/UI and durable/retry consumers before removing a legacy projection.

### 16.5 Internal-only accounting/recovery reasons

Reservation state, epoch rollover races, reconciliation reasons, lease-expiry reasons and provider-attempt diagnostics remain internal structured reasons/events unless separately promoted to a public contract. They MUST NOT invent public quota or timeout codes.

This section is the UBQ-0 vocabulary freeze. Later stages may add structured detail fields, but changing the meaning of these codes requires a new contract change and overlap audit.


---

## 17. Immediate governance decision

This document opens a new **coordination namespace** only.

It does NOT by itself authorize:

- SQL migration;
- runtime/accounting changes;
- TaskBudget behavior change;
- timeout behavior change;
- provider/runtime edits;
- Agent execution state-machine edits;
- destructive removal.

Issue #85 v2.5 remains the integration authority. Each production stage requires an exact CLAIM, fresh dependency/overlap audit, applicable CI, independent audit and merge authorization.

Current AE-R12 work may continue on its existing authority unless the specific candidate overlaps a future UBQ production path. UBQ planning must post a MATERIAL-dependency notice before any such production CLAIM.

---

## 18. Canonical future rule

```text
Budget = renewable user-owned resource quota.
Timeout = bounded waiting/deadline safety control.
Guard = structural/concurrency execution bound.
Retry allowance = provider/runtime control.
Task/Client/Session/Execution = attribution, not independent quota minting authority.
```

Any future feature that violates this separation must stop at contract review before implementation.