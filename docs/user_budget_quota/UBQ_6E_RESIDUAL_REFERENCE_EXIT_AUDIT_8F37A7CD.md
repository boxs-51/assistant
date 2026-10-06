# UBQ-6E — Residual Reference / Exit Audit

Status: **ZERO-PRODUCTION / CONTRACT + ARCHITECTURE EVIDENCE ONLY**

Canonical program: Issue #141  
Canonical stage workspace: Issue #148  
Repository policy: Issue #85 v2.5  
Independent PRE-CLAIM: Issue #148 comment #6021466396 — **PASS / RELEASED**  
Release audit baseline: `main@8f37a7cd8b2f99edf00439940b72db69d49fa216`  
Owner development base after NON_MATERIAL CAS drift: `main@1cd05ad3e5c390dfcc1612a49ad34474a2c894c8`

## 1. Purpose

UBQ-6E closes the UBQ-6 residual-reference audit without deleting, renaming,
re-fingerprinting, migrating, or reinterpreting production compatibility state.

UBQ-6A through UBQ-6D are already LANDED / CANONICAL / HEALTHY. 6E records
what remains intentionally present, why it remains present, and which future
exit gate owns any later cleanup.

This stage grants no production, runtime, configuration, schema, migration,
client, timeout, TBO, CAS, CTX, #156, or UBQ-7 authority.

## 2. Canonical ownership after UBQ-6A

The normative distinction remains:

```text
UBQ renewable user-resource authority
    !=
TaskBudget structural Task guard authority
    !=
TaskBudget historical resource-fallback compatibility state
```

When canonical user Tool quota is enabled, UBQ-3 owns renewable logical Tool
admission. When canonical user inference quota is enabled, UBQ-4 owns renewable
inference/token/compute/cost admission.

When those canonical quota modes are disabled, the historical finite TaskBudget
resource fallback remains enforceable for compatibility. UBQ-6E does not weaken
or remove that feature-OFF fallback.

## 3. Residual TaskBudget classification

### KEEP — structural Task authority

The following remain Task-scoped execution safety / recovery authority:

- total and active execution guards;
- active branch limits;
- parallel-Agent limits;
- delegation depth and recursive-cycle policy;
- execution / branch reservation and release identities;
- recovery and replay identities required by landed AE-R stages.

These are not renewable user quota.

### KEEP — historical compatibility state

The following remain compatibility/history and MUST NOT be destructively
cleaned before UBQ-7 final exit evidence:

- `TaskBudgetLimits.max_total_tool_calls`;
- `TaskBudgetLimits.max_total_inference_calls`;
- `TaskBudgetLimits.max_total_tokens`;
- `TaskBudgetLimits.max_total_cost_usd`;
- `TaskBudget.used_tool_calls`;
- `TaskBudget.used_inference_calls`;
- `TaskBudget.used_tokens`;
- `TaskBudget.used_cost_usd`;
- `TaskBudgetReservationKind.TOOL_CALL`;
- `TaskBudgetReservationKind.INFERENCE`;
- `TaskBudgetReservationKind.USAGE`;
- historical `policy_version` and `policy_fingerprint`;
- SQL TaskBudget columns and immutable migration history beginning with
  `11a_r5_task_budget.py`;
- compatibility reservation/replay identities consumed by later AE recovery.

Presence of these fields does not restore canonical renewable resource
authority to TaskBudget.

### LATER CLEANUP — explicitly not released here

Any physical deletion, rename, schema cleanup, migration rewrite, reservation
removal, policy re-fingerprint, or feature-OFF fallback removal is outside
UBQ-6E. Destructive compatibility cleanup remains HOLD until after UBQ-7 final
exit evidence and requires new explicit authority.

## 4. Landed UBQ-6C configuration split

`AgentTaskBudgetSettings` now exposes explicit:

- `execution_guards`;
- `legacy_resource_fallback`.

`se/src/main.py` consumes those groups separately when constructing the
historical `TaskBudgetLimits`.

Legacy flat TaskBudget configuration input remains a deterministic dual-read
compatibility surface so merged/default configuration behavior and historical
policy fingerprinting remain stable.

Neither configuration group may derive, construct, select, activate, or mutate
a `UserBudgetPolicy`.

## 5. Landed UBQ-6D DTO compatibility

Server and client `AgentExecutionLimits` retain the physical/public:

- `max_tool_calls` execution-local guard;
- `max_cost` compatibility field and wire/schema key.

The landed `legacy_max_cost` accessor is read-only, in-process only, and
returns `self.max_cost`. It is not a Pydantic field, request key, serialized
key, currency declaration, runtime enforcement rule, or renewable UBQ quota.

UBQ-6E performs no further DTO change.

## 6. Timeout and provider retry separation

Timeout/deadline remains distinct from renewable resource quota.

`ProviderCallBudget` remains the process-local deadline/retry budget for one
logical provider call and is outside UBQ-6 renewable-resource ownership.
Agent timeout compatibility from UBQ-5 remains unchanged.

UBQ-6E does not rename or reinterpret timeout/retry surfaces.

## 7. Historical UBQ-2 wording inventory

`UserBudgetDualAccountingService` still contains historical UBQ-2-era wording:

```text
TaskBudget remains admission authority.
```

For UBQ-6E this sentence is an **inventoried historical/compatibility
reference**, not current canonical renewable-quota authority.

Current canonical authority is determined by the landed UBQ-3/UBQ-4
feature-enabled admission paths plus the UBQ-6A demotion contract. UBQ-6E MUST
NOT use this legacy sentence to mint, restore, or broaden TaskBudget renewable
authority.

No production documentation string is edited in this zero-production slice.

## 8. PostgreSQL V7 deployment gate

`docs/user_budget_quota/UBQ_2_POSTGRESQL_V7_EVIDENCE_GATE.md` remains:

```text
OPEN / REQUIRED BEFORE ANY POSTGRESQL 26a / UBQ-2 RUNTIME DEPLOYMENT
```

Linux/Windows Architecture success is not executable PostgreSQL parity.

PostgreSQL migration/upgrade to `26a_ubq2_dual_accounting_bridge`, or
deployment of runtime code relying on its unconditional V7 TaskBudget
incarnation semantics, remains blocked until the exact PostgreSQL evidence gate
is satisfied.

UBQ-6E does not close, waive, weaken, or relocate this deployment gate.

## 9. Cross-track exit dependency

Issue #167 / AOS-2 Agent-only production cutover is a **MATERIAL future UBQ-7
exit dependency**.

It is not a blocker to this zero-production UBQ-6E residual audit. UBQ-7 should
run its final cross-track fault/rollout matrix against the final Agent-only
architecture after #167 is lawfully released and canonical.

No #167, #156, CAS, CTX, AE, or TBO authority transfers to UBQ-6E.

## 10. UBQ-6E exit disposition

UBQ-6E may be declared complete only when the exact released two-file candidate
proves:

1. the residual KEEP / compatibility / later-cleanup classification above;
2. feature-ON canonical UBQ authority and feature-OFF finite TaskBudget fallback;
3. landed UBQ-6C configuration separation remains intact;
4. landed UBQ-6D `max_cost` wire compatibility remains intact;
5. timeout / `ProviderCallBudget` remains separate from renewable quota;
6. historical TaskBudget migration and fingerprint identity remain retained;
7. PostgreSQL V7 remains OPEN / mandatory;
8. destructive compatibility cleanup remains blocked before UBQ-7 final exit;
9. #167 is recorded only as a MATERIAL future UBQ-7 dependency;
10. exact-head Linux + Windows Architecture is GREEN;
11. independent UBQ-6E FINAL is PASS;
12. no production/runtime/config/schema/migration/client path is changed.

UBQ-6E completion does not automatically release UBQ-7 production work. UBQ-7
requires a fresh exact-main roadmap/dependency/PRE-CLAIM audit.

## 11. Exact authority fence

UBQ-6E may add exactly two NEW files:

1. `docs/user_budget_quota/UBQ_6E_RESIDUAL_REFERENCE_EXIT_AUDIT_8F37A7CD.md`;
2. `se/tests/architecture/test_ubq6e_residual_reference_exit_audit.py`.

No third path is authorized.

Production authority: **NONE**.  
Runtime authority: **NONE**.  
Configuration authority: **NONE**.  
Schema/migration authority: **NONE**.  
Client authority: **NONE**.  
UBQ-7 authority: **NONE**.  
Merge authority: **NONE** until the applicable Policy #85 integration gate.
