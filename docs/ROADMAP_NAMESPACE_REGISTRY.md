# Roadmap Namespace Registry

## Status

**Authority:** repository-wide roadmap namespace registry  
**Scope:** documentation / coordination only  
**Code impact:** none

This document prevents phase-name collisions across independent roadmap tracks. It does not renumber historical work and does not rewrite existing commits, comments, or checkpoint evidence.

---

## 1. Canonical namespaces

| Namespace | Canonical track | Reserved identifiers | Primary authority |
|---|---|---|---|
| `AE-R*` | Agent Execution / Continuation / Branching | `AE-R0` through `AE-R14+` | `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md` |
| `TV1-T*` | Tools V1 / Metadata / consumer convergence | `TV1-T0` through current/future `TV1-T*` phases | `tools/v1/**` plans and active Tools checkpoint issues |
| `CAS-F*` | Central Asset Storage | `CAS-F0` through `CAS-F8+` | Central Asset Storage checkpoint/contracts |
| `PTC-*` | Provider Tool Contract Convergence | `PTC-1` through `PTC-3+` | Issue #8 and future PTC freeze/completion documents |
| `CTX-F*` | Context / Memory / Personalization / CompactContext | `CTX-F0` through `CTX-F12+` | Issue #15 and `docs/context_future/**` |

Bare historical phase IDs remain valid aliases only inside their original track context.

Examples:

```text
R8  -> AE-R8
T8  -> TV1-T8
F5  -> CAS-F5
```

New provider-tool work MUST use `PTC-*`; it must not allocate bare `R*` IDs.

Future Context/Memory/Personalization work MUST use `CTX-F*`; it must not reuse `CAS-F*` or bare `F*` identifiers.

---

## 2. Repository-wide naming rule

Cross-track documents, Issues, checkpoints, handoffs, and audit summaries MUST use qualified identifiers whenever ambiguity is possible.

Preferred forms:

```text
AE-R8
AE-R8-F7
TV1-T8
TV1-T8-E
CAS-F5
CAS-F5-0
PTC-1
CTX-F0
```

Avoid ambiguous standalone forms in cross-track coordination:

```text
R8
T8
F5
F7
R12
```

Historical comments/commits are not rewritten merely to add qualifiers.

---

## 3. Agent Execution namespace — AE-R*

Canonical sequence:

```text
AE-R0   Contract Freeze
AE-R1   WAITING normalization + compatibility
AE-R2   Durable AgentExecution authority + revision/CAS
AE-R3   Execution lineage
AE-R4   Active budget / wait TTL / deadline hierarchy
AE-R5   Cancellation / TaskBudget / async ownership
AE-R6   Remote invocation reconciliation / idempotency
AE-R7   Durable checkpoint / ResumeClaim / reconnect protocol
AE-R8   Normalized TaskBranch / branch context / FORK
AE-R9   RETRY / branch resolution / Task aggregation
AE-R10  Provider retry/fallback
AE-R11  Persistence/performance
AE-R12  Crash recovery / execution lease
AE-R13  Protocol/data compatibility cleanup
AE-R14  CI / fault injection / production exit gates
```

The authority for future AE phase meaning is:

```text
docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md
```

Older R0/R5/R7 documents remain historical phase-local evidence. Where wording differs from the current roadmap, the current roadmap owns future phase numbering.

---

## 4. Tools V1 namespace — TV1-T*

The Tools track historically uses `T0`, `T1`, etc.

Qualified form:

```text
T0 -> TV1-T0
T1 -> TV1-T1
...
T8 -> TV1-T8
```

Current Tools coordination is:

```text
Issue #7  — TV1-T8 CLOSED / COMPLETED
Issue #12 — TV1-T9 CLOSED / MERGED / FROZEN / POST-MERGE GREEN

tools/v1/T8_METADATA_V2_CONVERGENCE_IMPLEMENTATION_PLAN.md
tools/v1/TV1_T9_LOGICAL_EXPORT_MIGRATION_IMPLEMENTATION_PLAN.md
tools/v1/TV1_T9_LOGICAL_EXPORT_MIGRATION_COMPLETION.md
```

Allocated next phase:

```text
TV1-T10 Live Harness & Real-Machine Exit Gate
```

TV1-T10 is now coordinated by Issue #38.

Current live state:
```text
Issue #38  OPEN
TV1-T10-A CLAIMED
candidate  71b5b355dfb449d5dd633e5cb7b9df6aa1451fac
scope      one contract-doc file under tools/v1/live/**
production/runtime/test delta 0
```

TV1-T10-A is contract-doc stage only. Real-machine execution and production
tool-runtime changes are not authorized by this state.

### Historical T7/T8 wording

`tools/v1/TOOLS_V1_CONTRACT_FREEZE.md` contains earlier planning descriptions in which T7/T8 had a narrower pre-consumer meaning.

Those descriptions are retained as historical planning context.

For implemented/current T7/T8 semantics, precedence is:

```text
Issue #6 + accepted T7 implementation/freeze evidence
    -> canonical TV1-T7 implementation authority

Issue #7 + T8_METADATA_V2_CONVERGENCE_IMPLEMENTATION_PLAN.md
    -> canonical TV1-T8 implementation authority
```

Do not renumber completed T7/T8 work solely to reconcile this planning evolution.

---

## 5. Central Asset Storage namespace — CAS-F*

Canonical mapping:

```text
F0   -> CAS-F0
F1   -> CAS-F1
F2   -> CAS-F2
F2-H -> CAS-F2-H
F3   -> CAS-F3
F4   -> CAS-F4
F5   -> CAS-F5
F6   -> CAS-F6
F7   -> CAS-F7
F8   -> CAS-F8
```

Current dependency freeze:

```text
AE-R8 -> AE-R14 production gates complete
        |
        v
re-audit CAS-F5-0
        |
        v
resume CAS-F5+
```

Therefore any historical wording such as:

```text
"complete R8->R14 before F5"
```

must be interpreted canonically as:

```text
complete AE-R8 -> AE-R14 before CAS-F5 implementation resumes
```

This prevents PTC or TV1 identifiers from being mistaken for the Asset dependency gate.

---

## 6. Provider Tool Contract namespace — PTC-*

Issue #8 previously used local labels `R10`, `R11`, `R12`.

Those labels collided with canonical Agent Execution phases and are deprecated.

Migration:

```text
Issue #8 legacy R10 -> PTC-1
Issue #8 legacy R11 -> PTC-2
Issue #8 legacy R12 -> PTC-3
```

Canonical phases:

```text
PTC-1  Provider-facing tool-name/schema lowering
PTC-2  OpenAI/Ollama tool contract adapters
PTC-3  Tool-capability-aware routing + cross-provider exit gate
```

Current status:

```text
PTC-1 -> PTC-3  CLOSED / MERGED / FINAL GREEN
Issue #8       CLOSED / completed
```

Issue number #8, its URL, and historical finding IDs remain unchanged.

Historical finding IDs such as `P0-T8-XPROV-1` remain valid evidence labels; they are mapped to PTC ownership rather than renamed.

---

## 7. Future Context / Memory namespace — CTX-F*

Issue #15 parks the future Context / Memory / Personalization / CompactContext program behind the Agent Execution production hardening roadmap.

Canonical provisional phases:

```text
CTX-F0   Contract freeze
CTX-F1   Tool Response Payload
CTX-F2   Context source identities
CTX-F3   Session/Task/Branch discovery
CTX-F4   Context Access APIs
CTX-F5   Long-term Memory
CTX-F6   Personalization
CTX-F7   Pins / score / dedup
CTX-F8   Execution Continuity State
CTX-F9   Working Set + ContextSnapshot
CTX-F10  CompactContext
CTX-F11  DefaultChatAgent migration
CTX-F12  Observability / quality gates
```

Current status:

```text
FUTURE / PARKED / ARCHITECTURE ONLY
```

Production implementation is blocked until AE-R10 through AE-R14 have completed their production exit gates unless the roadmap is explicitly re-frozen earlier. CAS-F5+ must also be re-audited on the eventual unified post-R14 baseline before cross-cutting Context/Asset integration resumes.

Do not wire Memory, Personalization, ContextSnapshot, CompactContext, or automatic context retrieval into production while this track is parked.

---

## 8. PTC / Agent overlap boundary

PTC-3 and AE-R10 may touch adjacent provider-routing/fallback code.

Ownership is therefore frozen as:

```text
PTC-3:
    tool-capability eligibility
    provider-facing tool compatibility
    provider-native tool declaration/history lowering

AE-R10:
    provider retry/fallback lifecycle
    deadline-aware retry policy
    fallback selection policy
    retry/fallback ownership and accounting
```

Before either phase changes shared provider selection/fallback files, perform a fresh overlap audit and claim exact file ownership.

Neither roadmap may silently absorb the other's responsibilities.

---

## 9. Dependency graph

```text
Merged baseline:
AE-R9 CLOSED / merged @ 28757e9c
        |
        v
PRE-ROADMAP GATE — Issue #16
CI / Exit-Gate cleanup
        |
        v
POST-CLEANUP CANONICAL MAIN
        |
        +-----------------------------+-----------------------------+
        |                             |                             |
        v                             v                             v
Agent track                      Tools track                   Provider Tool
AE-R10                           re-audit/reconcile             PTC-1
  |                              TV1-T8 onto current main         |
AE-R11                                |                           v
  |                                   v                         PTC-2
AE-R12                           TV1-T9 -> TV1-T10                 |
  |                                                                 v
AE-R13                                                          PTC-3
  |                                                               |
AE-R14 <---------------- fresh overlap audit ----------------------+
  |
  v
POST-R14 UNIFIED BASELINE
  |
  +----------------------+----------------------+
  |                                             |
  v                                             v
re-audit CAS-F5-0                         re-audit CTX-F0
  |                                             |
  v                                             v
CAS-F5+                                   CTX-F0 -> CTX-F12+
```

Rules:
- Issue #16 is CLOSED / completed and no longer blocks later roadmap phases.
- TV1-T9 has been reconciled, merged and post-merge validated on canonical main.
- PTC-1 through PTC-3 are CLOSED / merged / FINAL GREEN and are inherited
  authority for later Agent work.
- AE-R11 is the active Agent roadmap phase under Issue #31.
- TV1-T10 is coordinated by Issue #38; T10-A is CLAIMED at contract-doc stage
  only, with no production/runtime changes and no R11 path overlap.
- CAS-F5+ and CTX-F* remain parked behind AE-R14 and require fresh post-R14 audits.

---

## 10. Branch, commit, Issue, and checkpoint convention

For new work, prefer:

```text
branch:
work/ae-r9-...
work/tv1-t9-...
feature/cas-f5-...
work/ptc-1-...

commit:
AE-R9: ...
TV1-T9: ...
CAS-F5: ...
PTC-1: ...

Issue/checkpoint marker:
[CLAIM] AE-R8-F7 @ <HEAD>
[AUDIT] TV1-T8-E @ <HEAD>
[CHECKPOINT] CAS-F5-0 @ <HEAD>
[CLAIM] PTC-1 @ <HEAD>
```

Existing branches and historical commits are not renamed solely to adopt this convention.

---

## 11. Allocation rule for future roadmaps

A new roadmap MUST NOT allocate a bare phase prefix already owned by another active or historical repository-wide track.

Before introducing a new roadmap:

1. check this registry;
2. choose a unique qualified namespace;
3. document historical aliases if migration is required;
4. identify overlap boundaries with existing roadmap owners;
5. use qualified IDs in all new coordination surfaces.

Reserved owners:

```text
R -> AE-R*    Agent Execution
T -> TV1-T*   Tools V1
F -> CAS-F*   Central Asset Storage
PTC -> PTC-*  Provider Tool Contract
CTX -> CTX-F*  Context / Memory / Personalization
```

Do not allocate a second independent `R10`, `T8`, or `F5` roadmap meaning.

---

## 12. Historical compatibility rule

Historical evidence is immutable context, not a migration target.

Do not rewrite old:

- commits;
- Issue comments;
- audit finding IDs;
- completion documents;
- checkpoint hashes;

solely to replace short aliases.

When referring to historical work from new documents, write the qualified canonical name and optionally retain the old alias:

```text
AE-R8 (historically "R8")
TV1-T8 (historically "T8")
CAS-F5 (historically "F5")
PTC-1 (Issue #8 legacy alias "R10")
```

---

## 13. Current coordination snapshot

At this update:

```text
canonical main 78479a64a97353094817b92a090449191782d366

AE-R8    CLOSED / merged
AE-R9    CLOSED / merged / PR #13
AE-R10   CLOSED / FINAL GREEN / Issue #14
AE-R11   ACTIVE / R11-A measurement + storage contract / Issue #31
AE-R12-14 NOT STARTED

PRE-ROADMAP CI GATE
Issue #16 CLOSED / completed

TV1-T8   CLOSED / GREEN / FINAL-FROZEN / Issue #7
TV1-T9   CLOSED / MERGED / FROZEN / POST-MERGE GREEN / Issue #12
TV1-T10  Issue #38 OPEN / T10-A CLAIMED / CONTRACT-DOC STAGE / NO RUNTIME DELTA

PTC-1-3  CLOSED / MERGED / FINAL GREEN / Issue #8

CAS-F1-F4 historically CLOSED on parked line
CAS-F5+   PAUSED behind AE-R14; post-R14 replay/re-audit required

CTX-F0-F12 FUTURE / PARKED / architecture only / Issue #15
CTX implementation waits for post-AE-R14 re-audit.
```

This snapshot is informational. Phase-specific Issues/checkpoints remain the authority for live implementation status.

---

## 14. Final invariant

Every roadmap identifier used in cross-track coordination must resolve to exactly one owner.

```text
AE-R8     != TV1-T8
AE-R10    != PTC-1
AE-R12    != PTC-3
AE-R8-F7  != CAS-F7
CTX-F5    != CAS-F5
```

If a proposed identifier cannot be resolved unambiguously through this registry, stop and assign a qualified namespace before implementation starts.
