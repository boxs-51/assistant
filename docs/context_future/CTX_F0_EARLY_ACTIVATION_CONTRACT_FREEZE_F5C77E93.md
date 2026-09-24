# CTX-F0 — Early Activation Authority / Namespace / Boundary Contract Freeze

**Repository:** `boxs-51/assistant`  
**Issue:** #15  
**Activation authority:** explicit user re-freeze on 2026-09-24  
**Canonical baseline:** `main@f5c77e93929a3d1fc8dfbfed8a046378bb9a3894`  
**Work branch:** `work/ctx-f0-early-f5c77e93`  
**Status:** ACTIVE / EARLY-ACTIVATED / CONTRACT-FIRST

---

## 1. Purpose

Issue #15 was originally parked until AE-R14. The user has explicitly opened the
track early. This document converts that authorization into a constrained
implementation contract.

Early activation does **not** erase existing ownership. CTX work may begin only
where it is additive and does not redefine currently active Agent Execution,
Central Asset, Tools, provider, or Session authorities.

The first implementation objective is to make future Context/Memory work
non-ambiguous before introducing production wiring.

---

## 2. Canonical namespace

The repository-wide namespace remains:

```text
CTX-F0   Contract / authority freeze
CTX-F1   Immutable ToolResponsePayload
CTX-F2   Context source identities + Central Asset convergence
CTX-F3   Session / Task / Branch discovery projections
CTX-F4   Context Access APIs
CTX-F5   Long-term Memory
CTX-F6   Personalization
CTX-F7   Pins / scoring / dedup
CTX-F8   ExecutionContinuityState
CTX-F9   Working Set + immutable ContextSnapshot
CTX-F10  CompactContext
CTX-F11  DefaultChatAgent convergence
CTX-F12  Observability / quality / migration exit gate
```

No new bare `F*`, `R*`, or `T*` identifiers may be allocated by this track.

---

## 3. Source authority separation

The system must preserve:

```text
Execution / continuation authority
!=
Agent transcript persistence authority
!=
Central Asset authority
!=
ToolResponsePayload authority
!=
Long-term Memory authority
!=
Context projection / retrieval index
!=
Model Working Set
```

A physical storage backend may be shared. Domain identity, lifecycle,
authorization, retention and replay authority may not be silently shared.

---

## 4. Inherited live authorities

### 4.1 Agent Execution / AE-R11

Current main includes R11-B1 dormant transcript representation storage.

Frozen inherited rules:

```text
AgentExecutionCheckpoint
    = continuation safe-point authority

(transcript_ref, transcript_version)
    = exact immutable Agent transcript persistence identity
```

Therefore:

```text
transcript_ref != Context locator
transcript_ref != Memory identity
transcript_ref != Central Asset identity
transcript_ref != ToolResponsePayload identity
```

CTX-F0/F1 must not modify:

- checkpoint resume authority;
- R11 transcript representation ancestry;
- continuation reconstruction fallback rules;
- R11 retention/GC roots;
- R12 future lease/recovery ownership.

### 4.2 Existing AgentContextSnapshot

`se/src/runtimes/agent/contracts/context_builder.py::AgentContextSnapshot` is the
current **per-inference-turn runtime snapshot**.

It is not automatically the future CTX-F9 durable `ContextSnapshot`.

Until CTX-F9 explicitly converges them:

```text
AgentContextSnapshot
    = existing runtime inference-turn contract

CTX ContextSnapshot
    = future versioned immutable context selection evidence
```

No aliasing by name or persistence identity is allowed in F0-F8.

### 4.3 ContextBuilderAdapter

The current `ContextBuilderAdapter` owns deterministic assembly of the runtime
Agent inference turn from explicit history or ContextRuntime/ContextEngine.

Early CTX work must not inject all persistent Memory into this adapter.

Pull-first access remains the target:

```text
discover -> resolve/search -> describe/read -> select -> working set -> inference
```

Persistent context is discoverable by default, not model-visible by default.

### 4.4 Central Asset

Historical Central Asset ownership remains:

- `FileAsset`;
- `FileBlob`;
- `FileReference`;
- `FileProviderBinding`;
- stable logical `asset://<asset_id>`.

CTX must not create a second file/media blob lifecycle.

Large non-file tool results belong to CTX-F1 `ToolResponsePayload`. It may later
reuse ObjectStorage physically, but it remains a separate domain authority.

CENTRAL-ASSET DEPENDENCY remains tracked by branch/docs until a dedicated CAS
issue exists.

---

## 5. Legacy naming audit

The current repository contains older schemas that use Memory terminology:

### `cl/src/schemas/context.py::MemoryQueryResult`

This type describes retrieved memory-like data and
`AgentContextSession.retrieved_memories` describes direct injection.

It is **legacy schema vocabulary**, not CTX-F5 long-term Memory authority.

CTX-F5 must not treat this model as the canonical durable Memory record without
an explicit migration/convergence contract.

### `se/src/domain/schemas/agent.py::AgentMemoryConfig`

The current `conversation_window/summary` configuration is an Agent definition
setting.

It is not the durable long-term Memory store, provenance model, retention model
or personalization authority planned by CTX-F5/F6.

Existing API compatibility may be preserved, but the name must not cause domain
ownership conflation.

---

## 6. CTX-F0 canonical domain identities

The following future identities are reserved and must remain opaque across
domain boundaries:

```text
context_source_id
context_item_id
tool_response_payload_id
memory_id
profile_fact_id
pin_id
continuity_state_id
context_snapshot_id
working_set_id
```

Rules:

1. provider file IDs, signed URLs, local paths and object keys are never any of
   these identities;
2. `asset_id` remains Central Asset identity;
3. `transcript_ref` remains Agent persistence identity;
4. Session/Task/Branch IDs may be source-owner keys but do not become generic
   Context item IDs;
5. derived chunk/vector IDs are rebuildable projection identities, not source
   authority.

---

## 7. Owner fencing and provenance

Every durable CTX object must carry enough trusted provenance to answer:

- who owns it;
- which source authority produced it;
- which immutable source/version it refers to;
- whether it is durable, provisional or derived;
- whether it may be shown to a model;
- whether it may be promoted to Memory.

Owner fencing must come from trusted server-side execution/session identity.
Model-supplied or client-arbitrary owner IDs must not create cross-owner access.

A derived Context index must always be rebuildable from authorized canonical
sources.

---

## 8. Task lifecycle invariants

CTX work inherits these semantics:

- a new Session does not automatically reactivate an old Task;
- a Task is created only for a new logical objective that cannot validly be
  handled as CONTINUE / RESUME / CONTROL;
- terminal Tasks remain historical/discoverable and are never resurrected;
- Context discovery may expose historical evidence without changing execution
  state.

Task admission/finalization integration must remain dormant until its exact
Agent ownership boundary is audited.

---

## 9. Early-activation implementation fence

Before AE-R14 completes, production CTX changes are allowed only when all are
true:

1. additive / isolated;
2. dormant-by-default or unused by current Agent execution;
3. no change to existing continuation/retry/fork/aggregate semantics;
4. no new provider retry/fallback authority;
5. no R11 checkpoint/transcript writer cutover;
6. no R12 lease/recovery behavior;
7. no Central Asset lifecycle duplication;
8. no automatic Memory injection into ContextBuilder;
9. no destructive transcript compaction;
10. targeted tests prove current behavior unchanged.

Any patch failing one of these gates returns to contract/audit-only status.

---

## 10. CTX-F1 entry contract — ToolResponsePayload

CTX-F1 is the first allowed implementation stage after this freeze.

The initial implementation must be a **dormant immutable payload domain** for
large non-file tool outputs.

Minimum contract:

```text
ToolResponsePayload
    payload_id
    owner scope
    source invocation identity
    content digest
    media/type metadata
    size
    storage locator (physical only)
    created_at
    retention state
```

Required invariants:

- immutable after commit;
- idempotent creation for identical source/content identity;
- conflicting replay fails closed;
- no provider-native ID is canonical authority;
- no automatic ContextBuilder injection;
- no Memory promotion;
- no Central Asset FileReference substitution;
- lifecycle/GC remains CTX-owned unless an explicit future shared-storage
  contract says otherwise.

The first F1 patch must not modify Agent checkpoint/transcript persistence files.

---

## 11. F0 acceptance findings

Current baseline audit found these important boundaries:

1. namespace `CTX-F*` is already reserved repository-wide;
2. existing `AgentContextSnapshot` is a live runtime contract and must not be
   confused with future durable CTX-F9 ContextSnapshot;
3. `ContextBuilderAdapter` currently performs runtime assembly and therefore is
   a protected integration boundary during early F stages;
4. legacy `MemoryQueryResult` / `AgentMemoryConfig` exist and require explicit
   compatibility treatment rather than implicit reuse;
5. R11 explicitly freezes transcript refs away from Context/Memory/CAS/TRP
   identity;
6. Central Asset remains the file/media authority.

No F0 production-code change is required or permitted by this freeze.

---

## 12. Stage order under early activation

```text
CTX-F0 contract freeze
    |
    v
CTX-F1 dormant ToolResponsePayload foundation
    |
    v
re-check Issue #15 + AE-R11/R12 live ownership
    |
    +--> if overlap is material: stop at dormant boundary
    |
    v
CTX-F2 source identities / CAS convergence contract
```

F3+ are not automatically opened by F0/F1 completion. Each stage requires a
fresh dependency/overlap check and a durable Issue #15 claim.

---

## 13. CTX-F0 exit gate

CTX-F0 is complete only when:

- this contract exists on the early activation branch;
- Issue #15 records the user re-freeze and owner claim;
- namespace collision audit is clean;
- R11 transcript identity boundary is explicitly inherited;
- existing ContextBuilder / AgentContextSnapshot boundary is frozen;
- legacy Memory naming collision is documented;
- Central Asset ownership is preserved;
- CTX-F1 exact dormant scope is frozen;
- no production runtime behavior changed.

