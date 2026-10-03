# UBQ-5A / T-0 Timeout Semantic Migration PRE-CLAIM Freeze

Status: **PRE-CLAIM PREPARATION / CONTRACT + ARCHITECTURE EVIDENCE ONLY / PRODUCTION CLAIM CLOSED**

Canonical baseline:

```text
main = ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12
source = merged AE-R12-F3-A PR #202 via IW-2026-10-02-06
R12-F3-A certified pre-merge HEAD = 09370d9e539ea32599b3032700b44373e61850d1
post-merge Architecture #1882 / 37021044586 = HEALTH GATE (must be GREEN before T-0 READY)
UBQ-4 / Issue #146 = COMPLETED / LANDED / CANONICAL / HEALTHY
UBQ-4 PR #190 merge commit = 1ceb34f561d73e26775910cd3468c962c17d61cd
Policy = Issue #85 v2.5
```

This document is the UBQ-5A/T-0 inventory and ownership freeze required before
any provider/tool/response timeout production migration. It authorizes **zero**
production/runtime/schema/migration changes. In this T-0 candidate,
production/runtime/schema/migration delta MUST remain ZERO.

## 1. Objective

UBQ-5 separates timeout/deadline semantics from renewable resource quota while
preserving existing provider retry/fallback, Agent recovery, durable task, and
side-effect reconciliation authorities.

Non-negotiable invariant:

```text
TIMEOUT / DEADLINE != RENEWABLE RESOURCE QUOTA
```

A timeout or deadline:

- MUST NOT mint a UBQ window;
- MUST NOT roll/reset/refill a UBQ window;
- MUST NOT refund resource usage merely because an operation timed out;
- MUST NOT prove that an unknown external side effect did not occur;
- MUST NOT create a second retry/fallback or recovery authority.

## 2. Inherited authority matrix

### 2.1 AE-R10 provider authority — inherited unchanged

Issue #14 is CLOSED / FINAL GREEN.

AE-R10 remains canonical owner of:

- one logical `ProviderCallBudget` across probe, attempt, retry, and fallback;
- the logical provider-call monotonic hard deadline;
- cancellation + draining of owned provider/probe children on logical timeout;
- caller cancellation propagation;
- late success/error deadline dominance;
- retry/fallback suppression after logical deadline expiry;
- started-attempt breaker/accounting semantics;
- pre-attempt expiry as a non-provider failure.

UBQ-5 MUST NOT introduce a second provider retry budget or logical-call deadline.

### 2.2 AE-R6 side-effect truth — inherited unchanged

A timeout after external dispatch does not prove "nothing happened".
Unknown/late remote outcomes remain subject to canonical reconciliation and
idempotency authority.

### 2.3 AE-R12 execution/recovery authority — inherited unchanged

Execution lease ownership, recovery activation, stale-owner fencing, and
durable continuation semantics remain AE-R12 authority.

R12-F3-A production PR #202 is now LANDED on the current canonical main.
This T-0 dependency snapshot is frozen at the certified integration result:

```text
PR #202 certified HEAD = 09370d9e539ea32599b3032700b44373e61850d1
PR #202 squash merge / current main = ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12
changed files = 13

se/src/infrastructure/storage/repositories/agent.py
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/recovery_execution.py
se/src/runtimes/agent/tool_execution/coordinator.py
se/src/runtimes/capability/contracts/error.py
se/src/runtimes/capability/runtime.py
se/tests/architecture/test_r12_f3a_recovered_tool_batch.py
se/tests/architecture/test_r7_c_tool_result_commitment.py
se/tests/architecture/test_r7_e_existing_invocation_continuation.py
se/tests/integration/test_r12_c_execution_lease_authority.py
se/tests/unit/test_r12_f3a_continuation_guard.py
se/tests/unit/test_r12_f3a_recovery_execution.py
```

The #202 landing is **MATERIAL to this T-0 dependency evidence baseline** because
T-0 promises an exact-current-main inventory and freezes future UBQ-5C ownership
overlap. The T-0 implementation delta itself remains contract/evidence-only and
changes no production/runtime/schema/migration path.

For future UBQ-5C production, #202 creates **MATERIAL / DIRECT OVERLAP** across
multiple timeout-relevant seams, not coordinator-only overlap:

```text
se/src/runtimes/agent/tool_execution/coordinator.py
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/capability/runtime.py
se/src/runtimes/capability/contracts/error.py
```

Therefore UBQ-5C production CLAIM remains **HOLD / MATERIAL** until a fresh
bilateral PRE-CLAIM against landed R12-F3-A semantics proves the exact timeout
ownership slice is safe (or proves a newly-audited disjoint slice). T-0 MUST NOT
regress this dependency map back to a coordinator-only statement.

### 2.4 TBO task authority — inherited unchanged

Task lifecycle eligibility, orchestration horizon, and any future task-level
hard horizon belong to TBO. Response timeout alone MUST NOT terminalize a
durable Task.

## 3. Exact current-main compatibility inventory

### 3.1 Provider logical-call deadline

Current executable owner paths:

```text
se/src/provider/retry_contracts.py
se/src/provider/handlers/base.py
se/src/provider/executor.py
se/src/provider/policies/retry.py
se/src/provider/handlers/chat_handler.py
```

Current behavior:

- `ProviderCallBudget` stores one monotonic logical deadline and retry count;
- `BaseExecutionHandler` creates one call budget from its current timeout;
- `await_with_provider_deadline(...)` bounds owned awaits by the remaining
  logical deadline;
- `ProviderExecutor` consumes the same logical budget across retry/fallback;
- probe and provider work inherit the same call budget.

Disposition in T-0: **KEEP / CANONICAL COMPATIBILITY AUTHORITY**.

The future UBQ-5 provider taxonomy must refine observable timeout scope without
breaking this one-logical-call authority.

### 3.2 Agent execution compatibility fields

Current server DTO:

```text
se/src/domain/schemas/agent_execution.py
```

Current compatibility fields include:

```text
timeout_seconds
iteration_timeout_seconds
inference_timeout_seconds
tool_timeout_seconds
task_timeout_seconds
```

Durable compatibility state also includes:

```text
remaining_active_budget_seconds
```

with consumers across Agent context/persistence/resume/fork/retry/recovery/
TaskBudget compatibility paths.

Disposition in T-0:

- **KEEP** field names and durable decoding until consumers are explicitly
  migrated;
- **SUPERSEDE terminology only**: these are execution guards/deadlines, not
  renewable user quota;
- **DELETE = PROHIBITED** in T-0.

### 3.3 Inference timeout seam

Current path:

```text
se/src/runtimes/agent/adapters/inference.py
```

Current inference requests carry:

```text
timeout_seconds
deadline_monotonic
```

The adapter derives a caller deadline when needed and bounds the inference
operation by the caller/iteration/execution deadline.

Disposition in T-0: **KEEP compatibility behavior**.
Future provider timeout taxonomy MUST compose beneath/within the stronger
applicable caller deadline; it MUST NOT extend it.

### 3.4 Tool timeout seam

Current Agent tool path:

```text
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/capability/runtime.py
se/src/runtimes/agent/tool_execution/coordinator.py
```

Current ordinary tool calls derive an operation timeout from
`tool_timeout_seconds` and remaining Agent execution/iteration authority.
Long-running/continuation paths have separate semantics.

Disposition in T-0:

- existing `tool_timeout_seconds` = compatibility hard-timeout input;
- canonical future `TOOL_CALL_TIMEOUT` = hard timeout for one logical tool
  call under the owning execution authority;
- `TOOL_IDLE_TIMEOUT` may exist only where trustworthy semantic progress is
  observable;
- heartbeat/transport liveness alone MUST NOT count as meaningful tool
  progress;
- timeout after dispatch MUST preserve R6/R12 outcome-truth requirements.

UBQ-5C production = **HOLD** because landed R12-F3-A semantics now own overlapping
recovery/continuation dispatch and projection seams on canonical main.

### 3.5 Gateway / response timeout seam

Current path includes:

```text
se/src/transport/gateway/api/v1/chat_router.py
se/src/runtimes/workflow/runtime.py
se/src/transport/gateway/api/v1/capability_router.py
```

Current public compatibility projection includes:

```text
GATEWAY_RESPONSE_TIMEOUT
timeout_scope = response_wait
timeout_seconds = <configured bound>
```

Disposition in T-0:

- current response-wait projection = compatibility surface;
- future response timeout taxonomy splits idle vs hard timeout;
- response timeout may stop/close process-local response work;
- response timeout alone MUST NOT terminalize a durable Task/Execution;
- SSE heartbeat/ping MUST NOT count as semantic response progress.

UBQ-5D production requires fresh Gateway + durable AE lifecycle PRE-CLAIM audit.

## 3.6 Exact-main timeout owner / disposition inventory

T-0 freezes every known live timeout owner, projection seam, compatibility
surface, and adjacent internal timer into one of three classes. This table is
descriptive evidence only; it grants no production authority.

| Class | Exact current-main path / surface | Frozen disposition |
|---|---|---|
| IN-SCOPE-LATER | `se/src/provider/handlers/embedding_handler.py` | KEEP current AE-R10 semantics. Embeddings create the same `BaseExecutionHandler._new_call_budget()` and consume `_remaining_timeout(call_budget)`; UBQ-5B must audit this path before introducing provider first-response/idle/call scopes. |
| IN-SCOPE-LATER | `se/src/runtimes/provider/runtime.py` | KEEP current projection of `context.config.provider.timeout` into provider handlers; UBQ-5B must audit before changing timeout taxonomy. |
| IN-SCOPE-LATER | `se/src/main.py` shared `httpx.AsyncClient(timeout=config.provider.timeout)` | KEEP transport-level compatibility projection; it is not a second logical-call deadline authority. UBQ-5B must account for it explicitly. |
| IN-SCOPE-LATER | `se/src/runtimes/capability/drivers/remote_client_driver.py` | KEEP derivation from `CapabilityExecutionContext.remaining_seconds` and forwarding to realtime invocation. Future UBQ-5C must preserve stronger Agent execution authority. |
| IN-SCOPE-LATER | `se/src/runtimes/connection/realtime.py` invocation wait | KEEP actual remote invocation wait, best-effort remote cancellation, and local correlation abandonment semantics. Timeout after dispatch does not prove absence of side effects. |
| IN-SCOPE-LATER | `se/src/transport/gateway/api/v1/capability_router.py` | KEEP `timeout_seconds` pass-through into `CapabilityExecutionRequest`; future UBQ-5C must audit this API seam. |
| COMPATIBILITY-KEEP | `TERMINAL_TIMEOUT` from `tools/v1/terminal_tool.py` and AgentRuntime consumption | KEEP as tool-specific executable/result vocabulary. T-0 does not alias or collapse it into `TOOL_CALL_TIMEOUT`; any later mapping requires an explicit UBQ-5C compatibility contract. |
| COMPATIBILITY-KEEP | `se/src/runtimes/agent/coordinator.py` execution wrapper | KEEP `asyncio.wait_for(..., execution_limits.timeout_seconds)` as an Agent execution compatibility guard. It is not renewable quota and is not silently reclassified as a tool/provider timeout. |
| COMPATIBILITY-KEEP | `se/src/transport/gateway/api/v1/files_router.py`, `se/src/transport/gateway/api/v1/models_router.py`, `se/src/transport/gateway/api/v1/embeddings_router.py` | KEEP raw HTTP 504 compatibility waits using `config.provider.timeout`. They are adjacent Gateway request waits, not new canonical provider first-response/idle scopes in T-0. |
| OUT-OF-SCOPE-INTERNAL | `se/src/runtimes/agent/stale_lease_scanner.py` | KEEP bounded scanner sweep timer as internal R12 maintenance. It is not user/tool/provider/response timeout authority. |
| OUT-OF-SCOPE-INTERNAL | `se/src/transport/gateway/api/v1/chat_router.py` 1-second streaming queue poll | KEEP heartbeat scheduling poll as transport maintenance. The poll expiry and emitted ping are NOT semantic `RESPONSE_IDLE_TIMEOUT` evidence or meaningful response progress. |
| OUT-OF-SCOPE-INTERNAL | `se/src/runtimes/connection/realtime.py` reconciliation wait + `se/src/runtimes/agent/resume_planning.py` defer mapping | KEEP R6/R7 reconciliation truth. Reconciliation timeout abandons the query/correlation only and maps to `RECONCILIATION_UNAVAILABLE`; it does not prove remote outcome absence and is not `TOOL_IDLE_TIMEOUT` or `TOOL_CALL_TIMEOUT`. |

Classification rules:

1. **IN-SCOPE-LATER** means the path is a live seam that a future
   UBQ-5B/5C/5D production stage MUST audit before CLAIM. T-0 changes nothing there.
2. **COMPATIBILITY-KEEP** means the executable/public/durable behavior remains
   unchanged until a separately released migration contract explicitly maps it.
3. **OUT-OF-SCOPE-INTERNAL** means the timer remains owned by its current
   transport/reconciliation/recovery/maintenance authority and MUST NOT be
   reclassified as provider/tool/response timeout semantics by UBQ-5.

Additional frozen boundaries:

- `RealtimeMultiplexer.invoke(..., timeout=...)` and its timeout cleanup are
  material to future UBQ-5C, but remote cancellation is best-effort and cannot
  manufacture side-effect certainty.
- realtime reconciliation timeout is query-only; it never becomes canonical
  tool timeout authority.
- `TERMINAL_TIMEOUT` remains distinct from `CAPABILITY_TIMEOUT`,
  `AGENT_TOOL_TIMEOUT`, and future `TOOL_CALL_TIMEOUT` unless a later
  independently released compatibility stage maps them.
- files/models/embeddings Gateway waits retain their existing HTTP 504 behavior;
  T-0 does not assign them provider first-response/stream-idle semantics.
- the Agent coordinator execution wrapper remains an enclosing execution guard,
  not a UBQ resource budget.
- stale-lease scanner duration remains R12 internal maintenance authority.
- the chat streaming 1-second queue poll is only a heartbeat scheduling
  mechanism and MUST NOT refresh or expire a semantic response-idle timer.

## 4. Current public compatibility vocabulary

Known current executable/public timeout vocabulary includes:

```text
AGENT_TASK_TIMEOUT
AGENT_EXECUTION_TIMEOUT
AGENT_ITERATION_TIMEOUT
AGENT_INFERENCE_TIMEOUT
AGENT_TOOL_TIMEOUT
AGENT_CONTEXT_TIMEOUT
CAPABILITY_TIMEOUT
GATEWAY_RESPONSE_TIMEOUT
```

T-0 freezes these as compatibility names. They MUST NOT be removed or silently
redefined before a separately released compatibility migration maps every
server/client/API/test consumer.

## 5. Future canonical UBQ-5 timeout taxonomy

The canonical future scopes are:

| Public error_code | timeout_scope | Meaning |
|---|---|---|
| PROVIDER_FIRST_RESPONSE_TIMEOUT | provider_first_response | Dispatch occurred but no first valid provider response/progress arrived in time |
| PROVIDER_STREAM_IDLE_TIMEOUT | provider_stream_idle | Streaming began but no meaningful provider progress arrived within the idle interval |
| PROVIDER_CALL_TIMEOUT | provider_call | Hard deadline for one logical provider call including retry/fallback |
| TOOL_CALL_TIMEOUT | tool_call | Hard timeout for one logical tool call |
| TOOL_IDLE_TIMEOUT | tool_idle | No trustworthy semantic tool progress within idle interval |
| RESPONSE_IDLE_TIMEOUT | response_idle | No meaningful response progress within idle interval |
| RESPONSE_HARD_TIMEOUT | response_hard | Hard lifetime bound for one response operation |

For every timeout projection, `timeout_seconds` names the configured bound for
the expired scope.

## 6. Progress semantics

### 6.1 Provider first response

First-response progress must be provider-semantic evidence sufficient to show
the provider call has actually started returning valid response/progress.
Local scheduling, retry bookkeeping, connection setup, heartbeat, ping, or
provider-attribution metadata alone are insufficient.

### 6.2 Provider stream idle

After first valid provider progress, the idle timer may be refreshed only by
meaningful provider progress. Heartbeat/keepalive alone MUST NOT refresh it.

The idle timer MUST remain bounded by the stronger logical
`ProviderCallBudget` hard deadline.

### 6.3 Tool idle

`TOOL_IDLE_TIMEOUT` is legal only where the capability contract exposes
trustworthy progress. A capability with no semantic progress signal may use a
hard call timeout but MUST NOT manufacture idle progress from process-local
heartbeat.

### 6.4 Response idle

Meaningful response progress is client-visible semantic response progress, not
transport heartbeat/ping. Closing the response transport does not imply a
durable Agent/Task terminal state.

## 7. Precedence

Timeout precedence is frozen as:

```text
stronger enclosing hard deadline
    bounds
more specific first-response / idle timeout
```

Examples:

- provider first-response and stream-idle scopes cannot extend
  `ProviderCallBudget`;
- Agent inference/tool timeouts cannot extend remaining execution/iteration
  authority;
- response idle timeout cannot extend response hard timeout;
- none of the above may extend a Task/TBO hard horizon.

When multiple scopes expire, public projection MUST reflect the authoritative
expired scope chosen by the separately released production contract. T-0 does
not alter current runtime precedence.

## 8. Timeout vs UBQ accounting

Timeout handling MUST preserve already-owned resource truth:

- admission/reservation that happened before timeout remains real;
- known actual usage is settled according to canonical UBQ authority;
- unknown usage remains UNKNOWN/RESERVED as defined by the owning quota stage;
- timeout does not auto-refund or reset a quota window;
- provider/tool retry/reconciliation authority decides whether work may be
  retried, not quota rollover.

R12 lease expiry likewise MUST NOT mint/reset/refund quota.

## 9. Compatibility disposition

### KEEP

- `ProviderCallBudget` behavior and AE-R10 deadline dominance;
- existing Agent timeout fields;
- `remaining_active_budget_seconds` durable decoding/state;
- current public timeout errors;
- current Gateway response-wait projection;
- current client/server DTO compatibility.

### MIGRATE LATER

- map legacy inference timeout projection to provider-specific scopes where
  exact authority permits;
- map ordinary tool timeout projection to `TOOL_CALL_TIMEOUT`;
- split response wait into response idle/hard scopes;
- add idle semantics only where trustworthy progress is observable.

### SUPERSEDE TERMINOLOGY

Use "resource budget/quota" only for renewable UBQ accounting.
Use "timeout/deadline/execution guard" for time-based bounds.

### DELETE / RENAME

**HOLD** until every durable/API/client/test consumer is mapped and an explicit
compatibility-removal stage is independently released.

### 9.1 Explicit KEEP / MIGRATE / ALIAS / DEPRECATE matrix

The four disposition verbs below are normative for PRE-CLAIM review:

- **KEEP** = current executable/durable/public identity remains unchanged in T-0.
- **MIGRATE** = a future separately-CLAIMed production stage may project the
  canonical replacement scope while preserving stronger enclosing authority.
- **ALIAS** = only a released compatibility adapter may project a canonical
  timeout back to a legacy name; T-0 itself introduces no runtime alias.
- **DEPRECATE** = removal warning/dual-read/dual-projection may begin only after
  all durable, server, client, API and test consumers are enumerated and the
  compatibility stage is independently released.

| Current symbol / surface | KEEP now | MIGRATE target | ALIAS rule | DEPRECATE gate |
|---|---|---|---|---|
| `ProviderCallBudget` | YES — AE-R10 behavior/identity | terminology/type only in a later compatibility stage; logical deadline semantics remain AE-R10 | replacement type may expose a compatibility alias only under an explicit migration contract | all provider/retry/fallback/tests migrated |
| `remaining_active_budget_seconds` | YES — durable decoding/state | execution-guard terminology only; no UBQ quota meaning | dual-field alias only if a future durable migration explicitly releases it | all SQL/checkpoint/resume/fork/retry/recovery/client consumers migrated |
| Agent `timeout_seconds` / `iteration_timeout_seconds` / `task_timeout_seconds` | YES | future AE/TBO naming only where separately released | compatibility DTO alias only under a released server+client contract | all server/client/API consumers migrated |
| `inference_timeout_seconds` / `AGENT_INFERENCE_TIMEOUT` | YES | UBQ-5B provider first-response/idle/call scopes | legacy Agent code may remain a compatibility projection only after exact provider-scope mapping is released | provider + Agent + Workflow + client/test consumers mapped |
| `tool_timeout_seconds` / `AGENT_TOOL_TIMEOUT` | YES | UBQ-5C `TOOL_CALL_TIMEOUT` / `TOOL_IDLE_TIMEOUT` | legacy Agent code may remain a compatibility projection after tool-scope mapping is released | Agent/Capability/R6/R12/client/test consumers mapped |
| `CAPABILITY_TIMEOUT` | YES | UBQ-5C distinguished hard/idle tool surfaces where applicable | compatibility adapters may preserve `CAPABILITY_TIMEOUT` for legacy consumers only | capability runtime/driver/Agent/client consumers mapped |
| `GATEWAY_RESPONSE_TIMEOUT` / `response_wait` | YES | UBQ-5D `RESPONSE_IDLE_TIMEOUT` / `RESPONSE_HARD_TIMEOUT` | legacy Gateway projection may alias a canonical response scope only after the response contract is released | Gateway/SSE/client/test consumers mapped |
| `AGENT_TASK_TIMEOUT` / `AGENT_EXECUTION_TIMEOUT` / `AGENT_ITERATION_TIMEOUT` / `AGENT_CONTEXT_TIMEOUT` | YES | no UBQ-5 ownership transfer; any future rename belongs to the owning AE/TBO compatibility stage | no new alias in T-0 | owning authority explicitly releases migration |

No row authorizes deletion, renaming, a new public error code, or a runtime alias in
T-0. A future canonical timeout code is **not** executable merely because it is
named in this contract.

### 9.2 Executable compatibility owner paths frozen by T-0

Server-side public/error projection owners include at minimum:

```text
se/src/runtimes/agent/runtime.py
se/src/runtimes/workflow/runtime.py
se/src/runtimes/agent/adapters/tool.py
se/src/runtimes/agent/tool_execution/errors.py
se/src/runtimes/capability/runtime.py
se/src/transport/gateway/api/v1/chat_router.py
```

Current client compatibility consumers remain non-authoritative but must be
included before any rename/deprecation:

```text
cl/src/schemas/agent_execution.py
cl/src/schemas/request.py
cl/src/core/agent_engine.py
cl/src/core/tool_executor.py
cl/src/mcp_client/mcp_adapter.py
cl/src/ui/bridge.py
```

These client paths do not acquire timeout ownership. They are compatibility
consumers whose existence blocks silent server-side removal/rename.

## 10. Production stage split after T-0

### UBQ-5B — provider timeout production

Status: **HOLD / fresh AE-R10 PRE-CLAIM audit required**.

Must preserve one logical `ProviderCallBudget`, retry/fallback ordering,
breaker/accounting semantics, caller cancellation, and late deadline dominance.

### UBQ-5C — tool timeout production

Status: **HOLD / MATERIAL overlap with landed R12-F3-A PR #202 semantics**.

No claim in `AgentToolExecutionCoordinator`, capability continuation dispatch,
or recovery projection paths is allowed until a fresh bilateral PRE-CLAIM
against canonical main resolves the exact ownership boundary.

### UBQ-5D — response idle/hard timeout production

Status: **HOLD / fresh Gateway + AE lifecycle PRE-CLAIM audit required**.

Response expiry cannot become durable Task/Execution terminalization authority.

### UBQ-5E — compatibility rename/deprecation

Status: **HOLD** until all server/client/durable/API/test consumers are mapped.

## 11. T-0 exact scope

Authorized candidate paths are exactly:

```text
docs/user_budget_quota/UBQ_5_T0_TIMEOUT_SEMANTIC_MIGRATION_FREEZE_EA6C5F00.md
se/tests/architecture/test_ubq5_t0_timeout_semantic_migration_freeze.py
```

Production/runtime/schema/migration delta MUST remain ZERO.

## 12. T-0 PRE-CLAIM exit gate

Independent audit must verify:

1. exact current-main inventory is complete enough to prevent a hidden timeout
   owner from being silently bypassed;
2. AE-R10 provider deadline/retry authority is preserved;
3. R6 unknown-side-effect authority is preserved;
4. R12 recovery/lease authority is preserved and landed #202 overlap is
   correctly classified MATERIAL/HOLD for UBQ-5C on current main;
5. Gateway response timeout does not gain durable Task terminalization
   authority;
6. current compatibility fields/error codes are frozen rather than deleted;
7. future timeout taxonomy and precedence cannot extend stronger enclosing
   deadlines;
8. timeout never mints/resets/refunds renewable UBQ quota;
9. exact-head Linux + Windows Architecture is GREEN;
10. changed files remain exactly the two T-0 contract/evidence files.

Only after T-0 independent PRE-CLAIM PASS may a narrower production stage seek
its own separate CLAIM.

## 13. Current disposition

```text
UBQ-5A / T-0 = OWNER CONTRACT/EVIDENCE CANDIDATE
production CLAIM = NONE
UBQ-5B = HOLD / AE-R10 audit required
UBQ-5C = HOLD / landed R12-F3-A direct overlap / fresh bilateral PRE-CLAIM required
UBQ-5D = HOLD / Gateway + AE lifecycle audit required
UBQ-5E = HOLD / compatibility mapping incomplete
merge authority = NONE
```
