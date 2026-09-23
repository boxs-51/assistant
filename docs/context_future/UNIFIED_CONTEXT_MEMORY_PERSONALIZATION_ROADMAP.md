# Future Context / Memory / Personalization Roadmap

**Repository:** `boxs-51/assistant`  
**Branch:** `feature/context-memory-personalization-future`  
**Base:** `main @ 28757e9c46355083ed16ee7bfda9c98fd7883a9b`  
**Status:** FUTURE / PARKED — architecture only, no production implementation  
**Current gate:** R9 merged; resume implementation only after R10→R14 production exit gates unless explicitly re-frozen earlier.

---

## 1. Purpose

This roadmap freezes the intended direction for the next generation of context handling after the current Agent Execution / Continuation / Branching roadmap.

The target system must support:

- on-demand context discovery instead of injecting all historical data;
- cross-session memory;
- Central Asset-backed file context;
- large Tool Response Payload storage;
- Session/Task/Branch discovery and resolution;
- user/agent personalization;
- pins, scoring and deduplication;
- execution continuity for long multi-step work;
- CompactContext that preserves the original objective and critical constraints;
- a future `DIRECT -> DefaultChatAgent -> AgentRuntime` convergence.

Core rule:

```text
SOURCE AUTHORITY
!=
CONTEXT PROJECTION
!=
MODEL WORKING SET
```

---

## 2. Dependency order

```text
R9 merged
  |
  v
R10 Provider retry/fallback hardening
  |
  v
R11 Persistence/performance hardening
  |
  v
R12 Crash recovery / execution lease
  |
  v
R13 Protocol/data cleanup
  |
  v
R14 Fault injection / production exit gate
  |
  v
FUTURE-0 Unified post-R14 baseline
  |
  +---------------------+
  |                     |
  v                     v
Asset F5             Tool Payload Store
  |                     |
  +----------+----------+
             |
             v
Context Source Contracts
             |
             v
Session/Task/Branch Discovery
             |
             v
context.resolve/search/describe/read
             |
        +----+----+
        |         |
        v         v
      Memory  Personalization
        |         |
        +----+----+
             |
             v
Pins / Score / Dedup
             |
             v
Execution Continuity State
             |
             v
Working Set + ContextSnapshot
             |
             v
CompactContext
             |
             v
DefaultChatAgent migration
```

No runtime wiring in this future branch.

### Central Asset cross-roadmap audit

The detailed F1→F8 dependency matrix is frozen in:

```text
docs/context_future/CENTRAL_ASSET_F1_F8_CTX_F_DEPENDENCY_AUDIT.md
```

Important scheduling refinement:

```text
post-R14
-> safely integrate/replay Central Asset F1-F4
-> re-freeze F5-0 against final R10/R11/R12 semantics
-> create/activate a dedicated Central Asset issue
```

After that gate, Central Asset F5-F8 and CTX-F may advance according to explicit
HARD/SOFT/EXIT dependencies. Full F5-F8 completion is not automatically a
prerequisite for CTX-F0/CTX-F1. A CTX phase may proceed only when every HARD
dependency assigned to that phase in the matrix is closed.

Two cross-roadmap rules are already frozen for the future re-audit:

- one canonical Central Asset read-lease authority must fence provider hydration,
  Context derivation/read, DELETE and GC; CTX must not invent a second lease authority;
- `FileAsset.revision` is lifecycle/CAS state, not immutable content version evidence.
  Context snapshots use canonical content evidence such as `blob_id + sha256`.


---

## 3. Context is pull-first, not push-all

A new session should receive only a minimal bootstrap:

```text
system / constitution
agent identity/instruction
effective personalization
current session/task metadata
critical inline anchors
context tool availability
```

Historical sessions, memory, files, old tool results and branch state remain **discoverable but unloaded**.

Invariant:

```text
available persistent context
!=
model-visible context
```

The Agent retrieves history only when the current request depends on it.

---

## 4. Context Access API

The canonical read-only surface should be small:

```text
context.resolve
context.search
context.describe
context.read
```

### context.resolve

Resolve natural-language references to durable entities.

Examples:

```text
"phiên hôm qua về R7-H"
"file roadmap đó"
"task triển khai R8"
"kết quả tool lần trước"
```

Possible locators:

```text
session://
task://
branch://
execution://
message://
memory://
asset://
tool-result://
tool-payload://
profile://
compaction://
```

If resolution is ambiguous, return candidates and confidence; the Agent asks the user instead of guessing.

### context.search

Search within authorized sources or within a resolved locator.

### context.describe

Return metadata without loading the full source.

### context.read

Materialize exact selected content and return:

```text
locator
source_revision
content_hash
projection_version
content
```

These operations are:

```text
SE-local
READ_ONLY
REPLAY_SAFE
NO_EXTERNAL_SIDE_EFFECT
```

---

## 5. Context Acquisition Policy

Tool selection must be contract-driven rather than vague model intuition.

```text
CAP-1
If needed information is absent from current working context but may exist
persistently, retrieve before answering.

CAP-2
Resolve ambiguous historical references before reading.

CAP-3
Prefer digest/metadata before large payloads.

CAP-4
Search coarse-to-fine:
entity -> source -> segment.

CAP-5
Do not load entire historical sessions unless required.

CAP-6
Ask the user when entity resolution remains genuinely ambiguous.

CAP-7
Authorization scope always comes from trusted execution identity,
never model-supplied user_id/project authorization.
```

---

## 6. Session / Task / Branch discovery

Historical entities need derived discovery indexes.

### SessionDigest

Suggested fields:

```text
session_id
title
summary
topics
keywords
entities
important decisions
time range
message_count
agent refs
task refs
embedding
projection_version
```

### TaskDigest

```text
task_id
objective
state
branch summaries
adopted result
important decisions
remaining/deferred items
session refs
```

### BranchDigest

```text
branch_id
task_id
resolution
base_checkpoint
summary
important sources
```

These digests are derived and rebuildable. They are never conversation or Task authority.

---

## 7. Task Admission

A Task is created only when a new logical objective appears and the request cannot validly continue/resume/control an existing Task.

Canonical decisions:

```text
CREATE
CONTINUE
RESUME
CONTROL
CREATE_RELATED
CREATE_SUBTASK
SPLIT
RESOLVE_REQUIRED
```

Examples:

```text
"Python decorator là gì?"
-> CREATE

"Cho ví dụ thêm."
-> CONTINUE

"Chọn phương án B."
-> CONTROL

"Tiếp tục R8 hôm qua."
-> RESOLVE_REQUIRED -> RESUME if old Task is WAITING

"Sửa thêm R8 đã hoàn thành."
-> CREATE_RELATED

"Giao một Agent audit riêng SQL."
-> CREATE_SUBTASK
```

Important:

```text
Task != step
Task != tool call
Task != iteration
Task != inference
```

A multi-step request may still be one Task.

---

## 8. Task completion and finalization

Terminal Task state is not deletion.

When a Task completes:

```text
ACTIVE
  -> finalize
  -> HISTORICAL / DISCOVERABLE
```

A future `TaskFinalizer` should:

```text
verify terminal Execution
resolve authoritative branch result
freeze final Task result
freeze continuity revision
invalidate pending resume paths
close TaskBudget
produce TaskDigest
produce MemoryCandidates
release ephemeral pins/leases
schedule retention/GC
mark Task COMPLETED
```

Simple Tasks may produce only lightweight metadata.

Complex Tasks may retain a rich final digest and final continuity state.

A terminal Task is never resurrected. Follow-up work creates a new related Task.

---

## 9. Execution Continuity State

CompactContext is not primarily a generic conversation summarizer.

Its first responsibility is to preserve continuity during long tasks.

Proposed authority/projection:

```text
ExecutionContinuityState

execution_id
task_id
branch_id

objective
original_requirements[]
hard_constraints[]
explicit_exclusions[]

accepted_decisions[]
critical_invariants[]

completed_milestones[]
current_focus[]
open_items[]
blockers[]

definition_of_done[]
critical_context_refs[]

revision
updated_at
```

Importance classes:

```text
CRITICAL
IMPORTANT
SUPPORTING
TRANSIENT
```

CRITICAL examples:

```text
original objective
explicit user prohibition
scope boundary
architecture invariant
definition of done
```

Critical requirements should not be repeatedly summarized into weaker paraphrases.

Prefer structured reducer/event updates:

```text
REQUIREMENT_ADDED
REQUIREMENT_CHANGED
CONSTRAINT_ADDED
DECISION_ACCEPTED
DECISION_REVOKED
MILESTONE_COMPLETED
BLOCKER_ADDED
BLOCKER_RESOLVED
FOCUS_CHANGED
```

This prevents summary-of-summary drift.

---

## 10. CompactContext

CompactContext has two responsibilities:

```text
1. Continuity Preservation
2. Working-Set Compaction
```

It must **not** compact the entire persistent universe.

It only operates on the current Agent Working Set:

```text
minimal bootstrap
+ current conversation
+ current Task/Branch state
+ context.read results
+ COMMITTED tool projections
+ INLINE pins
```

When token pressure appears:

```text
Working Set
  -> compact selected non-critical material
  -> preserve anchors/invariants
  -> produce a new immutable ContextSnapshot
```

Canonical sources remain unchanged.

---

## 11. ContextSnapshot

Each inference consumes exactly one immutable logical snapshot.

Suggested fields:

```text
snapshot_id
execution_id
generation
task_id
branch_id
effective_personalization_revision
continuity_revision
policy_version
token_budget
created_at
```

Each item records:

```text
ordinal
source_locator
source_revision
content_hash
projection_version
selection_reason
token_count
pin_mode
```

R7 invariant:

If the model already saw:

```text
memory://M42 @ revision=7
```

a restart/resume must not silently substitute revision 8 as if it had already been seen.

R8/R9 inheritance:

A FORK inherits the checkpoint's context base, then creates isolated BranchContext.

---

## 12. Tool Response Payload Store

`AgentToolResult` remains execution/result authority.

Large raw payload moves to a separate immutable store:

```text
ToolResponsePayload

id
owner_user_id
execution_id
invocation_id
tool_call_id
content_type
schema_id
encoding
size_bytes
sha256
storage_backend
object_key
state
revision
created_at
deleted_at
metadata_json
```

Visibility requires:

```text
ToolResponsePayload READY
AND
parent AgentToolResult COMMITTED
```

Recommended split:

```text
small text/json
-> may stay inline

large text/json/log/html
-> ToolResponsePayload

file/image/pdf/audio/video
-> Central Asset Storage
-> AGENT_TOOL_RESULT reference
```

Reuse ObjectStorage physically; keep domain authority separate.

---

## 13. Central Asset integration

Central Asset remains the canonical authority for file/media.

```text
FileAsset
FileBlob
FileReference
FileProviderBinding
```

Context search should operate on derived AssetDerivative/AssetChunk projections, while authorization begins from canonical asset visibility/reference scope.

Provider-specific file IDs are never Context authority.

F5-F8 remain parked behind the execution roadmap until re-audited on the final post-R14 baseline.

---

## 14. Long-term Memory

Memory is not conversation history, BranchContext or Personalization.

Suggested scopes:

```text
PROJECT
USER_AGENT
USER_GLOBAL (opt-in / policy controlled)
```

Suggested kinds:

```text
SEMANTIC
EPISODIC
DECISION
PREFERENCE_CANDIDATE
```

Memory retrieval occurs through Context APIs instead of injecting the whole store.

Memory extraction only consumes finalized durable sources.

Never auto-promote:

```text
stream chunks
PROVISIONAL tool results
unknown side effects
discarded branch conclusions
```

Branch promotion boundary:

```text
ADOPTED / authoritative AGGREGATE
-> eligible

DISCARDED / CANCELLED
-> not eligible by default
```

---

## 15. Personalization

Personalization is a separate domain.

```text
Memory
= what is known / happened

Personalization
= how the system should behave
```

Authorities:

```text
UserProfile
AgentProfile
UserAgentProfile
```

Examples:

### UserProfile

```text
language
response style
format preference
workflow preference
tool preference
```

### AgentProfile

```text
role
goal
communication style
execution policy
context policy
memory policy
tool strategy
retrieval strategy
```

### UserAgentProfile

Relationship-specific preferences.

Example:

```text
User + ArchitectureAgent
-> audit before code
-> freeze contracts before implementation
```

Preference provenance:

```text
USER_EXPLICIT
USER_ACTION
AGENT_INFERRED
SYSTEM_DEFAULT
ADMIN_POLICY
```

Explicit preference outranks inferred preference.

Do not automatically infer sensitive personal traits.

---

## 16. Pins / Score / Dedup

Pin modes:

```text
INLINE
PRIORITY
DISCOVERABLE
```

INLINE:
small critical bootstrap data.

PRIORITY:
strong retrieval boost without automatic injection.

DISCOVERABLE:
highlight in discovery/search.

Pins never bypass authorization, deletion, branch isolation or PROVISIONAL rules.

Scoring is query-specific, not global truth.

Deduplication remains separated:

```text
D1 logical identity duplicate
D2 exact content-hash duplicate
D3 semantic near-duplicate
D4 superseded/conflicting authority
```

D1-D3 affect selection only.
D4 is resolved by source lifecycle/version authority.

---

## 17. New-session behavior

Opening a new Session does not automatically reactivate previous work.

```text
New Session
  -> minimal bootstrap
  -> classify context need
```

Possible decisions:

```text
SELF_CONTAINED
CURRENT_SESSION
CURRENT_TASK
HISTORICAL_CONTEXT_REQUIRED
AMBIGUOUS_REFERENCE
```

Unrelated new requests do not load old Task continuity, files, memory or old tool results.

Historical context remains discoverable.

Invariant:

```text
New Session != resume previous Task
```

---

## 18. DIRECT migration

Future target:

```text
DIRECT/default
   -> DefaultChatAgent
   -> AgentRuntime

explicit Agent
   -> SelectedAgent
   -> AgentRuntime
```

Do not build a second permanent Memory/CompactContext architecture inside `DirectChatRuntime`.

Migration should happen only after Agent Context, Memory, Personalization and ContextSnapshot semantics are stable.

---

## 19. Future implementation phases

Suggested namespace for this future program:

```text
CTX-F0 Contract freeze
CTX-F1 Tool Response Payload
CTX-F2 Context source identities
CTX-F3 Session/Task/Branch discovery
CTX-F4 Context Access APIs
CTX-F5 Long-term Memory
CTX-F6 Personalization
CTX-F7 Pins/Score/Dedup
CTX-F8 Execution Continuity State
CTX-F9 Working Set + ContextSnapshot
CTX-F10 CompactContext
CTX-F11 DefaultChatAgent migration
CTX-F12 Observability / quality gates
```

These labels are provisional and must not collide with existing R/T/F roadmap namespaces.

---

## 20. Hard invariants

```text
CTX-I01 Persistent context is pull/on-demand by default.
CTX-I02 Context indexes/digests are derived, never authority.
CTX-I03 AgentToolResult remains tool execution/result authority.
CTX-I04 PROVISIONAL tool results never become durable model-visible context.
CTX-I05 Large raw tool output lives outside the transcript.
CTX-I06 File/media authority remains Central Asset Storage.
CTX-I07 Memory, BranchContext and Personalization are separate domains.
CTX-I08 Discarded branches do not automatically promote durable memory.
CTX-I09 Every retrieved model-visible item has locator + revision/hash.
CTX-I10 One inference consumes one immutable ContextSnapshot.
CTX-I11 R7 resume preserves logical context versions already seen.
CTX-I12 R8/R9 branch isolation is preserved by context retrieval.
CTX-I13 CompactContext preserves original objective and critical constraints.
CTX-I14 CompactContext compacts the Working Set, not the full persistent store.
CTX-I15 Continuity State is Task/Branch scoped, never user-global.
CTX-I16 New Session does not imply resume of old Task.
CTX-I17 Terminal Task is historical/discoverable, never resurrected.
CTX-I18 Personalization is behavior preference, not generic memory.
CTX-I19 DIRECT eventually converges on DefaultChatAgent -> AgentRuntime.
CTX-I20 No subsystem may create a parallel prompt/context authority.
```

---

## 21. Start condition

This roadmap is intentionally parked.

Implementation may begin only after:

1. R10→R14 production gates are complete, or the user explicitly re-freezes this dependency earlier;
2. Central Asset F1-F4 is integrated into the chosen baseline;
3. the exact post-R14 ContextEngine / AgentRuntime / TaskBranch / persistence blast radius is re-audited;
4. a dedicated `CONTEXT_FABRIC_CF0_CONTRACT_FREEZE.md` (or renamed final namespace equivalent) is approved.

Until then:

```text
DOCS / AUDIT / CONTRACT DESIGN ONLY
NO PRODUCTION WIRING
```
