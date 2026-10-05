# AE-R13-A — HEAD Audit + Protocol/Data Migration Cleanup Contract Freeze

**Repository:** `boxs-51/assistant`  
**Canonical workspace:** Issue #283  
**Policy:** Issue #85 v2.5  
**Baseline:** `main@e2395dabdac3c45e4b97cbc6a7ee51e08ab46d72`  
**Predecessor:** AE-R12 / Issue #107 COMPLETE / CLOSED / CANONICAL HEALTHY  
**Class:** CONTRACT / EVIDENCE / DOCS / ARCHITECTURE-TEST ONLY  
**Production/runtime/schema/migration delta:** ZERO

## 1. Canonical roadmap authority

The canonical AE roadmap defines:

```text
AE-R13 = Protocol and Data Migration Cleanup
goal = remove temporary compatibility only after rollout confidence
exit gate = compatibility removal is backed by migration tests / telemetry
```

R13 scope includes:
- old waiting enums;
- old `WAITING_FOR_CONNECTION` wire form;
- old continuation JSON;
- legacy execution records;
- deprecated continuation branch naming;
- remaining SE/CL schema drift;
- legacy TextContent edge cleanup if still applicable.

R13 is a cleanup/migration phase, not authority to rewrite earlier AE semantics.

## 2. Frozen ownership boundaries

R13 MUST preserve existing canonical owners:
- R6: remote invocation reconciliation/idempotency;
- R7: durable checkpoints, ResumeClaim, reconnect and resume semantics;
- R8: TaskBranch/FORK and branch context;
- R9: RETRY, branch resolution, Task aggregation, structural TaskBudget semantics;
- R10: provider retry/fallback/deadline authority;
- R11: persistence representation/performance/retention/GC unless separately released;
- R12: execution lease, stale-RUNNING evacuation and crash recovery.

Cross-track compatibility remains externally owned where applicable:
- UBQ #147 owns timeout/resource naming and the current timeout dual-read compatibility;
- CTX #15 owns Memory/Context lifecycle;
- CAS #74 owns asset lifecycle/persistence;
- #156 owns Agent-only/capability selection/routing/sandbox architecture;
- APR #278 owns future Agent identity/runtime-profile taxonomy.

R13 cleanup does not transfer any of those authorities.

## 3. HEAD inventory

### 3.1 Legacy WAITING input/state compatibility remains live

Current server schema still contains compatibility aliases:

```text
AgentExecutionState.WAITING_AGENT = "WAITING"
AgentExecutionState.WAITING_FOR_CONNECTION = "WAITING"
_LEGACY_WAITING_STATES["WAITING_FOR_CONNECTION"] = CONNECTION
_LEGACY_WAITING_STATES["WAITING_AGENT"] = AGENT
```

These are input-normalization compatibility, not new lifecycle states.

Client gateway code still recognizes legacy waiting spellings and normalizes them to canonical wait reasons.

### 3.2 WAITING_FOR_CONNECTION has multiple semantic roles

The same string currently appears in materially different roles:

1. obsolete/legacy state or wire spelling;
2. historical migration input;
3. compatibility repository/query input;
4. stable runtime/workflow/coordinator error/result code;
5. historical docs/tests.

Therefore R13 MUST NOT globally delete the token. Every occurrence requires role classification.

### 3.3 Legacy continuation JSON remains read-only migration input

`se/src/runtimes/agent/legacy_materialization.py` still reads
`AgentExecution.context_state["continuation"]` and can materialize normalized checkpoint data from historical records.

At the same time, `DurableAgentStore` already rejects new writes:
- new AgentExecution rows cannot write legacy continuation JSON;
- later updates cannot reintroduce `context_state["continuation"]`;
- historical continuation data is inert/read-only compatibility data.

This proves R7 removed write authority but deliberately retained a read-side migration seam.

### 3.4 Historical migrations are not deletion targets

Historical Alembic migration `8a_agent_execution_waiting_cas.py` contains transformations from
`WAITING_FOR_CONNECTION` to canonical `WAITING + CONNECTION`.

R13 MUST preserve immutable migration history unless a separately reviewed migration-history policy explicitly permits otherwise. The presence of legacy tokens in historical migrations is not itself evidence of active compatibility debt.

### 3.5 Repository/TaskBudget compatibility must be separated from structural authority

Current repository and TaskBudget code still recognize legacy `WAITING_FOR_CONNECTION` records.

Any later cleanup MUST preserve:
- R9 structural TaskBudget semantics;
- R12 stale-RUNNING recovery and RELEASE_EXECUTION accounting;
- revision/CAS correctness;
- legacy-record quarantine/fail-closed rules.

### 3.6 UBQ timeout aliases are cross-track compatibility

Server and client `AgentExecutionLimits` intentionally dual-read canonical and legacy timeout names introduced by UBQ-5E, including:

```text
execution_timeout_seconds <-> timeout_seconds
provider_call_timeout_seconds <-> inference_timeout_seconds
tool_call_timeout_seconds <-> tool_timeout_seconds
```

Those aliases are not automatically removable by AE-R13. Any cleanup requires a fresh bilateral #147/AE-R13 authority decision.

### 3.7 TextContent appears already removed from production

Fresh source search found no production `class TextContent`.

Current production references are limited to compatibility/presentation commentary such as Gemini response conversion; constructor-style uses are historical docs/tests/fakes.

R13 therefore treats TextContent as `SUPERSEDED_ALREADY` provisionally unless a fresh runtime edge is found.

### 3.8 Dedicated legacy-consumption telemetry is absent

No dedicated runtime metric/telemetry path was found that proves:
- legacy WAITING wire/state input is no longer received;
- legacy continuation materialization is no longer exercised;
- legacy durable rows have been fully migrated or intentionally quarantined.

Because the roadmap exit gate explicitly requires migration tests/telemetry, immediate removal is not authorized.

## 4. Required compatibility taxonomy

Each relevant occurrence MUST be classified as one of:

```text
REMOVE
MIGRATE_FIRST
KEEP_STABLE_ERROR
KEEP_HISTORICAL_MIGRATION
KEEP_CROSS_TRACK
SUPERSEDED_ALREADY
```

Examples frozen by R13-A:

| Area | Initial class | Reason |
|---|---|---|
| server legacy waiting aliases | MIGRATE_FIRST | still accepted compatibility input |
| CL legacy waiting normalization | MIGRATE_FIRST | still consumes old wire spellings |
| runtime/workflow WAITING_FOR_CONNECTION error code | KEEP_STABLE_ERROR pending separate review | not equivalent to old state enum |
| historical Alembic waiting conversion | KEEP_HISTORICAL_MIGRATION | historical migration evidence |
| legacy continuation materializer | MIGRATE_FIRST | still read-side migration path |
| persistence write prohibition | KEEP | prevents compatibility from regaining authority |
| UBQ timeout aliases | KEEP_CROSS_TRACK | #147-owned compatibility |
| TextContent production class | SUPERSEDED_ALREADY | no active production class found |

## 5. Initial findings

### P0-R13-A-TELEMETRY-1

The roadmap requires migration tests/telemetry before compatibility removal, but no dedicated legacy-consumption telemetry exists on the audited HEAD.

**Disposition:** removal stages remain CLOSED until R13-B freezes a trustworthy observation/migration strategy.

### P1-R13-A-ROLE-SPLIT-2

`WAITING_FOR_CONNECTION` spans state/wire aliases, migration inputs, repository compatibility and stable error vocabulary.

**Disposition:** no global replacement/removal is allowed.

### P1-R13-A-CONTINUATION-MIGRATION-3

Legacy continuation JSON is still consumed as read-only materialization input.

**Disposition:** retirement requires proof that durable legacy rows are migrated, absent, or intentionally unsupported with a fail-closed contract.

### P1-R13-A-CROSS-TRACK-SCHEMA-4

SE/CL timeout aliases are current UBQ compatibility.

**Disposition:** R13 may inventory them but cannot remove them without fresh bilateral authority from #147.

### P2-R13-A-TEXTCONTENT-5

TextContent appears already removed from production.

**Disposition:** verify with architecture evidence and close as no-op if no runtime dependency is found.

## 6. Provisional staged roadmap

```text
R13-A  HEAD inventory + removal taxonomy + telemetry/migration contract freeze
R13-B  legacy waiting state/wire telemetry + migration proof
R13-C  legacy continuation JSON/materialization retirement
R13-D  legacy execution-record/data compatibility cleanup
R13-E  continuation naming/protocol residue cleanup
R13-F  SE/CL schema convergence with bilateral ownership gates
R13-G  TextContent residual verification / cleanup if applicable
R13-H  compatibility-removal exit matrix + AE-R14 handoff
```

Only R13-A is released by this document.

## 7. R13-A exact scope

Exact two-file maximum:

1. `docs/agent_execution_r13/R13_A_HEAD_AUDIT_PROTOCOL_DATA_MIGRATION_CONTRACT_FREEZE_E2395DAB.md`
2. `se/tests/architecture/test_r13_a_protocol_data_migration_contract_freeze.py`

Forbidden in R13-A:
- any `se/src/**` or `cl/src/**` edit;
- schema/model/repository/runtime behavior change;
- Alembic migration;
- removal of aliases or fields;
- new telemetry implementation;
- CAS/CTX/UBQ/#156/APR production edits.

## 8. R13-A exit gate

R13-A may become FINAL only if:
1. exact two-file zero-production scope remains intact;
2. architecture evidence pins the current role split;
3. historical migrations are distinguished from active runtime compatibility;
4. UBQ-owned timeout compatibility is explicitly fenced;
5. TextContent active-production absence is verified;
6. exact-head Architecture Linux + Windows are GREEN;
7. independent audit finds no blocking P0/P1 in this contract;
8. current-main and cross-track drift are refreshed at FINAL.

Only after R13-A FINAL may an independent audit release the smallest R13-B telemetry/migration PRE-CLAIM.

## 9. Current disposition

```text
AE-R13 = ACTIVE
R13-A = CLAIM / CONTRACT-EVIDENCE ONLY
production authority = NONE
schema/migration authority = NONE
merge authority = NONE
```
