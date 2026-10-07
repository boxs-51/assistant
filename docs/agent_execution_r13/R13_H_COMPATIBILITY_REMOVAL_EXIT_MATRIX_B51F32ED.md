# AE-R13-H — Compatibility Removal Exit Matrix and AE-R14 Handoff

**Repository:** `boxs-51/assistant`  
**Canonical workspace:** Issue #283  
**Historical AE-R12 workspace:** Issue #107 (COMPLETE / CLOSED)  
**Policy:** Issue #85 v2.5  
**Released baseline:** `main@b51f32ed3b67367bad74a3fb298b7448899ad631`  
**Claim baseline:** `main@949dbefc51caa4281fe6eb132da1423401cd2537`  
**Class:** CONTRACT / EVIDENCE ONLY  
**Production/runtime/schema/migration/API delta:** ZERO

## 1. Exit rule

AE-R13 removes compatibility only where exact rollout/migration evidence and a
separately released stage proved removal safe. Retained or deferred compatibility
is not described as removed.

The canonical terminal taxonomy is:

```text
REMOVE
MIGRATE_FIRST
KEEP_STABLE_ERROR
KEEP_HISTORICAL_MIGRATION
KEEP_CROSS_TRACK
SUPERSEDED_ALREADY
```

## 2. Canonical exit matrix

| Stage | Compatibility item | Terminal classification | Canonical disposition / future gate |
|---|---|---|---|
| R13-A | Compatibility inventory and role split | MIGRATE_FIRST | The contract prohibited global token deletion and required evidence per semantic role. |
| R13-B | Server legacy WAITING durable residue and rollout observation | REMOVE | Migration regression and bounded residue evidence enabled the later exact D cleanup; the observation boundary remains evidence, not new lifecycle authority. |
| R13-B | CL legacy WAITING wire aliases and normalization | MIGRATE_FIRST | Retained because collected evidence did not authorize removal for every client population. Future removal needs representative client rollout evidence and a fresh SE/CL compatibility audit. |
| R13-A/D | Stable runtime/workflow/coordinator `WAITING_FOR_CONNECTION` error/result vocabulary | KEEP_STABLE_ERROR | This vocabulary is not the retired execution-state alias and must not be globally deleted. |
| R13-A/D | Historical migration `8a_agent_execution_waiting_cas.py` | KEEP_HISTORICAL_MIGRATION | Immutable migration evidence remains present; no history rewrite was authorized. |
| R13-C | Active persistence/resume legacy continuation materialization entrypoints | REMOVE | R13-C1 retired active materialization only after Mode-Z rollout evidence. Canonical pending-ticket and resume paths now require normalized checkpoints. |
| R13-C | Historical `context_state["continuation"]` and legacy parser data | MIGRATE_FIRST | Historical payload remains read-only compatibility/evidence. Future physical-data cleanup requires durable inventory, retention ownership, and a separately released migration or GC gate. |
| R13-D1 | Dead repository helpers `list_legacy_waiting_executions_for_owner` and `bind_legacy_checkpoint_pointer` | REMOVE | Removed under the exact D1 release while the live residue probe and canonical waiting query were preserved. |
| R13-D2 | Dead Python `AgentExecutionState.WAITING_AGENT` and `WAITING_FOR_CONNECTION` source aliases | REMOVE | Removed under D2; canonical `WAITING` remained. |
| R13-D3 | Server raw legacy execution-state normalization | REMOVE | `_LEGACY_WAITING_STATES` was retired after zero-residue evidence; raw legacy state input now fails closed. |
| R13-E | Raw `CONNECTION_RECONNECT -> CLIENT_RECONNECT` ResumeClaim trigger alias | REMOVE | Retired after durable zero-residue evidence; canonical ResumeTriggerType values remain. |
| R13-F | SE/CL `AgentExecutionLimits` timeout dual-read compatibility | KEEP_CROSS_TRACK | Owned by Issue #147 / UBQ-5E/T-4. Rename/removal/default-wire changes require a fresh UBQ-5F/T-5 bilateral release. |
| R13-G | Production `TextContent` class/symbol | SUPERSEDED_ALREADY | No production class or import remained when audited; R13 had no production deletion to perform. |
| R13-G | Legacy text/thinking `data` payload normalization in `MessageContentPart` | MIGRATE_FIRST | Retained in both SE and CL. Removal requires fresh consumption/residue evidence and a bilateral wire-compatibility audit. |

## 3. R13 work completed and canonical

The following bounded removals are canonical and healthy:

- active legacy continuation materialization entrypoints;
- dead legacy execution repository helpers;
- dead Python execution-state aliases;
- server raw legacy execution-state normalization;
- the legacy ResumeClaim trigger spelling.

R13-A/B observation and migration evidence remain the proof chain for those
removals. They do not create new recovery, persistence, TaskBudget, client, or
cross-track authority.

## 4. Deferred compatibility and exact release gates

Deferred items remain live by design:

1. **CL legacy WAITING wire input — MIGRATE_FIRST.**  
   Owner/gate: fresh SE/CL compatibility release after representative client
   telemetry proves zero consumption across the relevant deployed population.

2. **Historical continuation payload/data — MIGRATE_FIRST.**  
   Owner/gate: fresh persistence/retention/migration authority with durable
   inventory and an explicit treatment for historical rows. R11 retention/GC
   ownership is not transferred.

3. **Legacy nested text/thinking `data` payloads — MIGRATE_FIRST.**  
   Owner/gate: fresh SE/CL wire audit plus consumption/residue evidence. The
   current normalizers must remain symmetric until then.

No deferred item is counted as complete removal.

## 5. Intentionally retained cross-track compatibility

- AgentExecutionLimits timeout dual-read/default-wire behavior is
  `KEEP_CROSS_TRACK` and remains owned by Issue #147.
- CTX #15, CAS #74, capability/sandbox #156, APR #278, UBQ/TBO, and AE-R6
  through AE-R12 retain their existing authorities.
- Stable WAITING error/result vocabulary is `KEEP_STABLE_ERROR`.
- Historical migration 8a is `KEEP_HISTORICAL_MIGRATION`.

## 6. Items superseded before R13

The production `TextContent` class is `SUPERSEDED_ALREADY`. This conclusion
does not apply to the separate legacy MessageContentPart raw-payload migration
seam, which remains `MIGRATE_FIRST`.

## 7. AE-R14 entry assumptions — no authority grant

A future AE-R14 may assume only that the bounded R13 removals listed in section 3
are canonical and that every retained/deferred boundary in sections 4–6 still
exists with its named owner and gate.

This handoff does **not**:

- open AE-R14 production work;
- authorize any `se/src/**` or `cl/src/**` edit;
- authorize schema, migration, persistence, retention, GC, lifecycle, runtime,
  state-machine, ResumeClaim, Task/TBO/UBQ, CTX, CAS, #156, or APR changes;
- reinterpret stable error vocabulary or historical migrations as removable;
- transfer cross-track authority.

AE-R14 requires its own current-policy audit, PRE-CLAIM/release, exact path
freeze, CLAIM, CI, independent FINAL, and applicable merge authorization.

## 8. R13-H exact scope and exit gate

R13-H owns exactly two new files:

1. `docs/agent_execution_r13/R13_H_COMPATIBILITY_REMOVAL_EXIT_MATRIX_B51F32ED.md`
2. `se/tests/architecture/test_r13_h_compatibility_removal_exit_matrix.py`

No production, runtime, client, schema, migration, configuration, API, or
existing evidence file is changed.

R13-H reaches FINAL only when:

- exact two-file scope is preserved;
- the architecture evidence pins the repository truth and this matrix;
- Linux and Windows Architecture are GREEN on the exact candidate HEAD;
- independent audit finds no blocking P0/P1/P2;
- current-main and cross-track drift are freshly classified.

Production and AE-R14 authority remain NONE.
