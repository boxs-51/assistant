# UBQ-6B — Residual TaskBudget Compatibility / Configuration Split Freeze

Status: **CONTRACT / ARCHITECTURE EVIDENCE ONLY**

Canonical program: Issue #141  
Canonical stage workspace: Issue #148  
Repository policy: Issue #85 v2.5  
Development baseline: `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`  
Baseline health: Architecture #2128 / 37267898302 **GREEN/GREEN**  
Pre-claim authority: Issue #148 comment #5988979197  
Owner contract claim: Issue #148 comment #5994541605

## 1. Purpose

UBQ-6A moved renewable tool and inference/token/cost admission authority to the
canonical user-budget services when their feature flags are enabled. That does
not make the historical TaskBudget representation disposable.

UBQ-6B freezes the residual compatibility boundary before any configuration
taxonomy is changed. It is deliberately zero-production: no runtime, schema,
migration, configuration, DTO, composition-root, persistence, or client file is
authorized by this stage.

The goal is to make the next production slice mechanically small and prevent a
configuration cleanup from accidentally:

- deleting historical TaskBudget evidence;
- weakening feature-OFF finite fallback;
- manufacturing per-user UBQ policy from per-Task configuration;
- changing R12 execution/recovery semantics;
- changing public AgentExecutionLimits wire behavior;
- absorbing UBQ-5 timeout / ProviderCallBudget authority; or
- treating SQLite Architecture CI as PostgreSQL UBQ parity.

## 2. Current authority after UBQ-6A

When canonical user tool quota is enabled, UBQ-3 owns renewable logical tool
admission. When canonical user inference quota is enabled, UBQ-4 owns renewable
inference/token/cost admission.

TaskBudget still owns Task-scoped structural execution safety and still carries
resource-looking compatibility state for historical durability, replay,
fingerprinting, feature-OFF behavior, and migration compatibility.

The following distinction is normative:

```text
UBQ renewable user-resource authority
    !=
TaskBudget structural Task guard authority
    !=
TaskBudget historical resource-fallback compatibility state
```

No later stage may collapse these identities by naming convenience.

## 3. Exact residual inventory on e2395dab

### 3.1 AgentTaskBudgetSettings — mixed vocabulary that must be split later

`se/src/infrastructure/config/schemas.py::AgentTaskBudgetSettings` currently
contains both categories.

Task execution / branch / delegation guards:

- `deny_recursive_agent_cycle`
- `max_total_executions`
- `max_active_executions`
- `max_active_branches`
- `max_parallel_agents`
- `max_delegation_depth`

Legacy TaskBudget resource-fallback compatibility:

- `max_total_tool_calls`
- `max_total_inference_calls`
- `max_total_tokens`
- `max_total_cost_usd`

`se/config/default.yaml::agent.task_budget` exposes the same mixed vocabulary,
and `se/src/main.py` currently constructs one historical `TaskBudgetLimits`
from both categories.

Disposition: **MIGRATE LATER**, not in UBQ-6B.

A future config split must create an explicit structural-guard settings surface
and an explicitly named legacy TaskBudget resource-fallback compatibility
surface. It must preserve behavior while making ownership visible.

It must not derive, construct, select, activate, or mutate `UserBudgetPolicy`
from either TaskBudget configuration surface.

## 4. KEEP — durable/historical TaskBudget compatibility

The following remain compatibility authority and must not be renamed, removed,
re-fingerprinted, or destructively migrated before the UBQ-7 exit gate:

- `TaskBudgetLimits.max_total_tool_calls`
- `TaskBudgetLimits.max_total_inference_calls`
- `TaskBudgetLimits.max_total_tokens`
- `TaskBudgetLimits.max_total_cost_usd`
- `TaskBudget.used_tool_calls`
- `TaskBudget.used_inference_calls`
- `TaskBudget.used_tokens`
- `TaskBudget.used_cost_usd`
- `TaskBudgetReservationKind.TOOL_CALL`
- `TaskBudgetReservationKind.INFERENCE`
- `TaskBudgetReservationKind.USAGE`
- historical `policy_version` and `policy_fingerprint`
- SQL columns in `TaskBudgetRecord`
- immutable migration history beginning with
  `11a_r5_task_budget.py`
- exact compatibility reservation/replay identity used by later R6-R12 work.

These fields are not evidence that TaskBudget still owns canonical renewable
user quota.

## 5. KEEP — structural Task guards and recovery semantics

TaskBudget structural execution authority remains independent of UBQ renewable
resource authority. At minimum this includes:

- total/active execution limits;
- active branch limits;
- parallel-Agent limits;
- delegation depth / recursive-cycle policy;
- execution/branch reservation and release identities;
- R12 recovery ownership and recovery receipt semantics.

UBQ-6B and any later config taxonomy work must not reinterpret or delete
`NEW_EXECUTION`, `RESUME_EXECUTION`, `RELEASE_EXECUTION`, `BRANCH`, or
`RELEASE_BRANCH` semantics.

## 6. Feature-ON / feature-OFF compatibility rule

The post-UBQ-6A behavior is intentional:

- canonical tool quota **ON**: UBQ-3 owns renewable tool admission; TaskBudget
  preserves compatibility counters/reservation identity without a second
  renewable veto;
- canonical inference quota **ON**: UBQ-4 owns renewable inference/token/cost
  admission; TaskBudget preserves compatibility counters/reservation identity
  without a second renewable veto;
- corresponding canonical quota **OFF**: the historical finite TaskBudget
  resource fallback remains enforceable.

A future split may rename configuration containers, but it may not silently
remove the feature-OFF fallback. Removing that fallback requires its own
migration/rollout authority and exit evidence.

## 7. AgentExecutionLimits residual compatibility

Server and client `AgentExecutionLimits` both still expose:

- `max_tool_calls`: **KEEP** as an execution-local safety guard. It is not a
  renewable user quota.
- `max_cost`: resource-looking public compatibility vocabulary. The exact
  baseline has no production consumer of that field. It is not UBQ authority.

`max_cost` is **SEPARATE FUTURE SERVER/CLIENT COMPATIBILITY WORK**. It must
not be deleted, renamed, or repurposed inside the future TaskBudget config
split, because its change is a DTO/wire compatibility decision.

## 8. Timeout surfaces are outside UBQ-6

`agent.budget.configure` currently allocates/proposes Task/iteration/model/tool
**time** values. It does not grant authority to mint tool calls, tokens, cost,
or any user quota.

`ProviderCallBudget` remains AE-R10 / UBQ-5 deadline-and-retry behavior for one
logical provider call. Its name is legacy-adjacent but its semantics are outside
UBQ-6.

UBQ-6 config work must not rename, reinterpret, or absorb either surface by
adjacency.

## 9. PostgreSQL V7 deployment gate remains mandatory

`docs/user_budget_quota/UBQ_2_POSTGRESQL_V7_EVIDENCE_GATE.md` remains:

```text
OPEN / REQUIRED BEFORE ANY POSTGRESQL 26a / UBQ-2 RUNTIME DEPLOYMENT
```

Linux/Windows repository Architecture success is SQLite-oriented repository
evidence and does not establish executable PostgreSQL parity.

Neither UBQ-6B nor later configuration cleanup may claim that the PostgreSQL V7
gate is satisfied.

## 10. Frozen migration matrix

| Surface | Disposition | Earliest authority |
|---|---|---|
| TaskBudget persisted resource limits/counters | KEEP compatibility | destructive change HOLD until after UBQ-7 |
| TOOL_CALL / INFERENCE / USAGE reservations | KEEP compatibility | destructive change HOLD until after UBQ-7 |
| TaskBudget policy version/fingerprint | KEEP historical identity | destructive/refingerprint HOLD until after UBQ-7 |
| 11a and later TaskBudget migration history | KEEP immutable | never rewrite historical migration |
| Structural execution/branch/delegation guards | KEEP Task authority | outside renewable quota |
| `AgentTaskBudgetSettings` mixed taxonomy | MIGRATE | fresh UBQ-6C production PRE-CLAIM |
| `default.yaml::agent.task_budget` mixed taxonomy | MIGRATE | fresh UBQ-6C production PRE-CLAIM |
| `main.py` TaskBudget settings composition | MIGRATE | fresh UBQ-6C production PRE-CLAIM |
| `AgentExecutionLimits.max_tool_calls` | KEEP execution-local guard | no UBQ migration required |
| `AgentExecutionLimits.max_cost` | SEPARATE compatibility slice | future UBQ-6D PRE-CLAIM |
| `agent.budget.configure` | KEEP current time semantics | UBQ-5/TBO-compatible future work only |
| `ProviderCallBudget` | KEEP current retry/deadline semantics | AE-R10/UBQ-5 authority |
| obsolete TaskBudget resource compatibility deletion | HOLD | only after UBQ-7 exit evidence |
| PostgreSQL V7 deployment | HOLD | executable PG parity gate |

## 11. Provisional follow-on ordering — no authority granted here

```text
UBQ-6A  conditional renewable-resource authority demotion
        = LANDED / CANONICAL / HEALTHY

UBQ-6B  residual compatibility/config split freeze
        = THIS ZERO-PRODUCTION STAGE

UBQ-6C  structural guard vs legacy resource-fallback config split
        = NOT RELEASED / fresh production PRE-CLAIM required

UBQ-6D  AgentExecutionLimits resource-vocabulary compatibility cleanup
        = NOT RELEASED / separate server+client PRE-CLAIM required

UBQ-6E  UBQ-6 residual reference/exit audit
        = NOT RELEASED / zero destructive cleanup by default

UBQ-7   cross-track fault / rollout / final exit
        = RESERVED

post-UBQ-7 destructive compatibility deletion
        = HOLD / requires new explicit authority
```

This ordering is provisional until each predecessor is canonical/healthy and a
fresh exact-main audit confirms the smallest safe scope.

## 12. Cross-track fences

- AE-R12 #107 is COMPLETE/CLOSED. UBQ consumes its structural/recovery boundary;
  no recovery ownership transfers to UBQ.
- UBQ-5 #147 owns timeout migration. UBQ-6 does not absorb timeout semantics.
- CAS #74 and first-producer work own media/CAS producer semantics; no UBQ
  authority transfers.
- CTX #15 owns Memory/Context/StorageEngine promotion semantics; no UBQ
  authority transfers.
- #156 owns Agent-only/capability/sandbox roadmap boundaries; it may depend on
  UBQ-6 completion but does not grant UBQ new authority.
- TBO #150 remains RESERVED / NOT OPEN. Task orchestration cannot be inferred
  from a TaskBudget naming cleanup.

## 13. UBQ-6B acceptance gate

UBQ-6B is acceptable only if exact-head evidence proves:

1. mixed structural/resource-fallback config vocabulary still exists;
2. persisted resource-looking TaskBudget fields/counters/reservations remain
   compatibility state;
3. feature-OFF finite fallback remains intentional;
4. UBQ feature-ON paths remain canonical renewable resource authority;
5. TaskBudget policy/fingerprint/SQL/migration history remains intact;
6. `max_tool_calls` remains an execution-local AgentExecutionLimits guard;
7. `max_cost` remains present in server/client DTOs and has no current
   production consumer;
8. `agent.budget.configure` remains time-only;
9. `ProviderCallBudget` remains retry/deadline-only;
10. PostgreSQL V7 parity gate remains OPEN/mandatory;
11. no config path manufactures `UserBudgetPolicy` from TaskBudget settings;
12. UBQ-6C/6D/6E remain separately gated.

UBQ-6B FINAL does not release UBQ-6C production by implication. After UBQ-6B
lands canonical/healthy, a fresh exact-main independent PRE-CLAIM is mandatory.

## 14. Authority fence

UBQ-6B itself may change exactly two NEW files:

1. this contract;
2. `se/tests/architecture/test_ubq6b_residual_taskbudget_compatibility_split_contract.py`.

Everything else is CLOSED.

Production authority: **NONE**.  
Schema/migration authority: **NONE**.  
Runtime authority: **NONE**.  
Configuration authority: **NONE**.  
Public DTO/wire authority: **NONE**.  
Merge authority: **NONE** until the applicable Policy #85 integration gate.
