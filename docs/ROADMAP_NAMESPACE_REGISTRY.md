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

Bare historical phase IDs remain valid aliases only inside their original track context.

Examples:

```text
R8  -> AE-R8
T8  -> TV1-T8
F5  -> CAS-F5
```

New provider-tool work MUST use `PTC-*`; it must not allocate bare `R*` IDs.

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

Current TV1-T8 work is governed by:

```text
Issue #7
tools/v1/T8_METADATA_V2_CONVERGENCE_IMPLEMENTATION_PLAN.md
```

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

Issue number #8, its URL, and historical finding IDs remain unchanged.

Historical finding IDs such as `P0-T8-XPROV-1` remain valid evidence labels; they are mapped to PTC ownership rather than renamed.

---

## 7. PTC / Agent overlap boundary

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

## 8. Dependency graph

```text
Agent:
AE-R8 -> AE-R9 -> AE-R10 -> AE-R11 -> AE-R12 -> AE-R13 -> AE-R14
                                                          |
                                                          v
                                                   re-audit CAS-F5
                                                          |
                                                          v
                                                     CAS-F5+

Tools / Provider Tool:
TV1-T8-H
   |
   v
PTC-1 -> PTC-2 -> PTC-3
                    |
                    +---- overlap audit ----> AE-R10
```

PTC-1/PTC-2 may proceed independently of Agent branching work only while their exact file scope remains disjoint from active AE ownership.

---

## 9. Branch, commit, Issue, and checkpoint convention

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

## 10. Allocation rule for future roadmaps

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
```

Do not allocate a second independent `R10`, `T8`, or `F5` roadmap meaning.

---

## 11. Historical compatibility rule

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

## 12. Current coordination snapshot

At creation of this registry:

```text
AE-R7    CLOSED
AE-R8    ACTIVE
AE-R9+   NOT YET ENTERED

TV1-T7   CLOSED / audit-approved
TV1-T8   ACTIVE

CAS-F1-F4 CLOSED
CAS-F5+   PAUSED behind AE-R14 production gates

PTC-1-3   ROADMAP ONLY / NOT IMPLEMENTED
```

This snapshot is informational. Phase-specific Issues/checkpoints remain the authority for live implementation status.

---

## 13. Final invariant

Every roadmap identifier used in cross-track coordination must resolve to exactly one owner.

```text
AE-R8     != TV1-T8
AE-R10    != PTC-1
AE-R12    != PTC-3
AE-R8-F7  != CAS-F7
```

If a proposed identifier cannot be resolved unambiguously through this registry, stop and assign a qualified namespace before implementation starts.
