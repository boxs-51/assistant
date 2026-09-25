# CTX-F5-0 — Long-term Memory Authority Freeze

## Status

Primary workspace: Issue #15.

Exact released baseline:

```text
canonical main = c50d0670ae80aac60efc3557eeff8f78a4a8e425
Architecture #1220 / run 36104672013 = GREEN/GREEN
CTX-F4B = LANDED / COMPLETE
CTX-F5-0 CLAIM = independently RELEASED
production Memory authority = CLOSED
merge authorization = NONE
```

This stage is contract/evidence only. It does not implement durable Memory.

## 1. Domain separation

The frozen authority split is:

```text
source authority
!= Context projection
!= long-term Memory authority
!= model Working Set
```

Long-term Memory is a distinct CTX-owned durable domain.

Reserved durable identity:

```text
memory_id = opaque durable Memory identity
```

Identity fences:

```text
memory_id != context_source_id
memory_id != transcript_ref
memory_id != asset_id
memory_id != provider id / FileProviderBinding id
memory_id != Session/Task/Branch id
memory_id != tool_response_payload_id
memory_id != chunk/vector/index id
```

CTX-F5-0 does not add or assume `ContextSourceKind.MEMORY`.

A durable Memory record may later reference proven source provenance, but that does not make Memory a Context source by implication.

## 2. Existing source projections do not grant F5 loaders

Current `ContextSourceKind` includes:

- SESSION
- TASK
- BRANCH
- AGENT_TRANSCRIPT
- ASSET
- TOOL_RESPONSE_PAYLOAD

Existing adapters may project these canonical authorities into immutable `ContextSourceRef` values.

That projection is not repository, loader, search, retention, lifecycle, promotion, or model-visibility authority.

F4B remains finite structural search over already-supplied Session/Task/Branch evidence only.

F5-0 does not widen F4B into persisted/global retrieval.

## 3. Explicit promotion/write authorization

Creating durable Memory must require an explicit server-authorized promotion operation.

Discoverability or searchability is never promotion authority.

A future promotion boundary must:

1. derive owner scope from trusted server-side authority;
2. re-prove source identity/provenance at commit time;
3. reject cross-owner provenance;
4. freeze exact source identity/version/revision evidence;
5. freeze a content fingerprint;
6. create a new opaque `memory_id`;
7. be idempotent for the same authorized promotion + exact content;
8. fail closed on conflicting replay.

Model output, client-provided owner ids, search hits, relevance scores, legacy `MemoryQueryResult`, or provider-native ids must not authorize promotion.

Automatic Session/Task/Branch -> Memory promotion remains CLOSED.

## 4. Source physical lifetime

Frozen rule:

```text
source physical lifetime != Memory retention root
```

After an authorized promotion commits, Memory provenance does not require R11/CAS/source-authority rows, transcript bytes, blobs, provider bindings, or other physical source material to remain alive solely because Memory references them.

F5 does not acquire:

- R11 checkpoint/transcript retention authority;
- R11 destructive-GC authority;
- CAS asset/blob/provider retention authority;
- CAS provider hydration/lifecycle authority;
- CAS physical-GC authority.

Physical source unavailability caused by independently valid retention/GC policy is not authority for F5 to resurrect or retain that source.

## 5. Durable existence is not authorization

Frozen rule:

```text
Memory durable existence
!= retrieval authorization
!= model visibility
!= personalization authority
!= source authorization
```

A durably stored Memory record does not by itself prove that it may be retrieved, selected, injected, or shown to a model.

The complete Memory state machine, expiry, tombstone, deletion, and revocation model is not frozen in F5-0.

## 6. Revocation / deletion / erasure

Frozen rule:

```text
authoritative source revocation/deletion/erasure handling = UNRELEASED / FAIL-CLOSED
```

F5-0 does not decide that committed Memory remains usable after:

- canonical source revocation;
- owner authorization loss/transfer;
- logical source deletion;
- legal/privacy erasure;
- future authoritative lifecycle invalidation.

Until a later explicit lifecycle contract exists:

```text
uncertain or unproven authorization => FAIL CLOSED
```

F5 must not infer:

```text
source revoked/deleted => Memory remains authorized
```

and must not infer:

```text
source physically GC'd => Memory must automatically be physically deleted
```

Cascade/tombstone/revocation/erasure behavior remains CLOSED.

## 7. Retrieval boundary

Future Memory retrieval is a separate F5 operation over committed Memory authority.

```text
F4B source search
  = finite already-supplied Session/Task/Branch evidence

future F5 Memory retrieval
  = separate committed-Memory loader/repository/index contract

retrieved Memory
  != selected model Working Set
```

F5-0 releases no repository, SQL loader, vector/semantic index, ranking, relevance score, recency policy, pagination, cursor, top_k, or global search.

Derived chunk/vector/index identities are rebuildable projection identities only and never canonical Memory identity.

## 8. Model visibility and later CTX stages

Frozen separation:

```text
Memory persistence/retrieval (F5)
!= Personalization/profile mutation (F6)
!= Pins/scoring/dedup policy (F7)
!= Working Set / ContextSnapshot selection (F9)
!= CompactContext (F10)
```

No direct Memory injection into ContextBuilder is released.

No model Working Set visibility is implied by persistence or retrieval.

## 9. Legacy Memory vocabulary

The following current surfaces are compatibility vocabulary only:

### `cl/src/schemas/context.py::MemoryQueryResult`

This is legacy client/schema vocabulary and is not canonical CTX-F5 durable Memory authority.

### `AgentContextSession.retrieved_memories`

This is a legacy direct-injection field and is not canonical F5 retrieval, selection, persistence, or model-visibility authority.

### `se/src/domain/schemas/agent.py::AgentMemoryConfig`

This is Agent conversation-window/summary configuration, not durable long-term Memory authority.

### SessionRuntime "memory" wording

`SessionRuntime._on_provider_responded` contains legacy wording such as "Memory or Storage Engine" / "session memory", while the actual durable operation uses `CanonicalMessageService.persist_message(...)`.

That wording does not create CTX-F5 authority.

## 10. Cross-track boundaries

### R11 / Issue #31

R11 retains checkpoint/transcript persistence, retention, serialization, and destructive-GC authority.

Repair B / semantic writer serialization may evolve independently. CTX-F5-0 does not depend on unresolved destructive-GC semantics and does not absorb them.

### CAS / Issue #74

CAS retains:

- FileAsset
- FileBlob
- FileReference
- FileProviderBinding
- provider hydration/runtime binding
- ObjectStorage lifecycle
- asset/provider deletion
- CAS physical GC

`asset_id / asset://<asset_id>` remains canonical CAS identity.

Provider ids/bindings never become Memory identity.

## 11. Schema/migration determination

CTX-F5-0 determines only that a future durable Memory implementation would require an explicit, separately released Memory-domain persistence contract if storage is introduced.

F5-0 itself creates:

```text
NO Memory table
NO Memory ORM/model
NO repository
NO migration
NO schema mutation
```

Any F5-1 schema/migration must receive a fresh independent release.

## 12. Initial F5-1 direction

Possible later candidate direction, not released here:

```text
CTX-F5-1:
dormant immutable Memory record + exact provenance/promotion contract
```

Still CLOSED after F5-0:

- Memory persistence implementation;
- global retrieval;
- semantic/vector search;
- ranking/scoring/top_k;
- automatic promotion;
- ContextBuilder injection;
- Personalization;
- Working Set / ContextSnapshot;
- CompactContext;
- `ContextSourceKind.MEMORY`;
- source hydration;
- R11/CAS retention or deletion convergence;
- revocation/tombstone/erasure implementation.

## 13. Acceptance evidence

Architecture evidence must freeze:

- distinct opaque `memory_id`;
- no `ContextSourceKind.MEMORY`;
- legacy Memory vocabulary is non-authoritative;
- source physical lifetime is not a Memory retention root;
- Memory durable existence is not retrieval/model-visibility authority;
- revocation/deletion/erasure is unreleased and fail-closed;
- F4B remains finite Session/Task/Branch search;
- no Memory model/repository/schema/migration exists in this F5-0 delta;
- no R11/CAS lifecycle authority expansion;
- no ContextBuilder / Working Set / Personalization / CompactContext wiring.

This contract does not authorize merge or CTX-F5-1 implementation.
