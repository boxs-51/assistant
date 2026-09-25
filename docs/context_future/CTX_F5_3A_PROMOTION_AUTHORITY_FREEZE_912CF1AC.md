# CTX-F5-3A — Memory Promotion Authority Contract Freeze

## Status

~~~text
stage                        = CTX-F5-3A
mode                         = CONTRACT / AUTHORITY FREEZE ONLY
claim baseline               = 912cf1ac0386f765a5d6324a337b84b5ae928715
baseline Architecture        = #1323 GREEN/GREEN
production promotion runtime = NOT RELEASED
source-specific adapters     = NOT RELEASED
automatic promotion          = CLOSED
Memory retrieval/model use   = CLOSED
~~~

This document freezes the authority boundary required before any runtime Memory
promotion implementation may be released.

It adds no production service, endpoint, adapter, repository, schema, migration,
loader, hydration path, retrieval path, or ContextBuilder wiring.

## 1. Authority separation

The canonical split is:

~~~text
source authority
!= ContextSourceRef projection
!= Memory promotion authority
!= durable Memory persistence
!= Memory retrieval authorization
!= model Working Set visibility
~~~

A ContextSourceRef is an immutable source identity/provenance claim. It is
not current source read authority, source liveness authority, or Memory
promotion authority.

The existence of a durable Memory row is not evidence that the row may be
retrieved, selected, injected, or shown to a model.

## 2. Current source-claim vocabulary

The current Context source vocabulary contains:

- SESSION
- TASK
- BRANCH
- AGENT_TRANSCRIPT
- ASSET
- TOOL_RESPONSE_PAYLOAD

F5-3A does not declare any source kind promotable by default.

Unsupported, unavailable, stale, foreign, or otherwise unprovable source
authority must fail closed.

F5-3A does not add or assume ContextSourceKind.MEMORY.

## 3. Server-owned promotion authority

Every future durable Memory creation must be associated with a trusted
server-owned promotion_authority_id.

Frozen rules:

~~~text
promotion_authority_id
!= context_source_id
!= memory_id
!= asset_id
!= transcript id
!= Session / Task / Branch id
!= tool_response_payload_id
!= provider id / FileProviderBinding id
~~~

A client, model, search result, provider-native identifier, relevance score, or
existing source projection cannot mint or substitute promotion authority.

The promotion authority must be reserved or minted by trusted server authority.

Replay semantics remain those already frozen by F5-1/F5-2:

- same authorized promotion + same exact frozen provenance + same exact content
  + same Memory schema => idempotent replay;
- same promotion authority with changed source/content/provenance => fail closed;
- different authorized promotion decisions may produce distinct Memory records
  even when canonical content is identical.

## 4. Source re-proof contract

Before promotion commit, the owning source authority must provide a
server-issued/server-verifiable proof or receipt for the requested source claim.

The generic proof must be capable of binding at least:

~~~text
source_kind
context_source_id
authority_id
authority_version / source revision where applicable
owner_user_id
current read/liveness authorization scope
proof revision / state token / receipt identity
ordering evidence sufficient for stale-proof rejection
~~~

The proof is provider-neutral at the CTX boundary.

The proof must match the exact source claim and trusted owner scope.

A stored ContextSourceRef, an old successful read, a discovery result, or a
persisted provenance snapshot is not a substitute for this current re-proof.

## 5. Commit-time race / TOCTOU rule

A successful source proof is not an evergreen authorization token.

Frozen rule:

~~~text
old ContextSourceRef
+ old successful source read
!= current promotion authority
~~~

A future runtime promotion implementation must either:

1. validate an authority revision/receipt that the owning source authority
   guarantees remains authoritative for the promotion admission decision; or
2. fail closed when deterministic stale-proof rejection cannot be established.

CTX must not pretend cross-store atomicity exists where it does not.

Where exact atomicity across the source authority and Memory store is
impossible, the source authority contract must expose revision/receipt
semantics strong enough to detect stale or foreign proofs deterministically.

## 6. Owner and provenance rules

Trusted owner scope is server-derived.

The source proof owner must equal the owner frozen in the validated
ContextSourceRef provenance claim.

Client-selected owner ids never become promotion authority.

Cross-owner promotion fails closed.

Promotion commit must preserve the complete canonical provenance required by
the existing F5 Memory integrity rules.

## 7. Cross-track ownership

### ASSET / CAS

CAS remains owner of:

- FileAsset;
- FileBlob;
- FileReference / canonical asset identity;
- ObjectStorage;
- provider hydration/upload;
- FileProviderBinding;
- asset readability/liveness;
- asset lifecycle, deletion, reconciliation, and physical GC.

A future CTX promotion flow may consume only a narrow CAS-owned
source-read/liveness proof result.

Provider ids and provider bindings never become Memory promotion authority.

CTX must not call ObjectStorage or provider hydration as an implied consequence
of this contract.

### AGENT_TRANSCRIPT and other R11-owned durable execution evidence

R11 remains owner of:

- transcript/checkpoint source authority;
- retention policy;
- current read/liveness authority;
- destructive-GC authority for R11-owned evidence.

A future CTX promotion flow may consume only a narrow R11-owned proof/receipt.

Memory provenance must not turn a source into a retention root.

### SESSION / TASK / BRANCH / TOOL_RESPONSE_PAYLOAD

Existing CTX projection/discovery code does not automatically become a
production source re-proof adapter.

Any source-specific adapter requires its own explicit authority audit/release.

## 8. Reuse of canonical F5-2 admission

F5-3 must not create a second Memory persistence or replay path.

A future separately released runtime promotion implementation must:

1. obtain trusted owner scope;
2. receive the exact source claim;
3. obtain current source-authority proof;
4. validate claim/proof/owner consistency;
5. canonicalize Memory content and metadata through existing F5 domain rules;
6. reuse the existing canonical content digest;
7. reuse the existing deterministic Memory identity;
8. bind the server-owned promotion_authority_id;
9. perform exactly one F5-2 durable admission/replay operation;
10. preserve existing F5-2 conflict and idempotency semantics.

No alternate repository, alternate Memory identity, or alternate idempotency
authority is released.

## 9. Failure taxonomy

A future runtime contract must be able to distinguish at least these failure
classes, although exact API names remain for a later implementation freeze:

- unsupported source authority;
- source not found / unavailable;
- source not currently authorized;
- source owner mismatch;
- stale source proof;
- source proof conflict;
- promotion replay conflict;
- Memory admission conflict;
- Memory persistence failure.

All uncertain or unprovable authorization states fail closed.

## 10. Explicitly closed authority

F5-3A does not release:

- runtime promotion orchestration;
- public or HTTP Memory promotion endpoints;
- automatic Session/Task/Branch/Asset/Transcript/ToolResponse -> Memory
  promotion;
- source loaders or hydration adapters;
- CAS provider/blob/asset lifecycle ownership;
- R11 retention or destructive-GC ownership;
- ContextSourceKind.MEMORY;
- Memory retrieval/search/ranking/vector/index/chunk storage;
- embeddings, score, recency, top_k, or global search;
- ContextBuilder or model Working Set visibility;
- F6 Personalization;
- F7 pins/scoring/dedupe;
- F9 Working Set / ContextSnapshot;
- F10 CompactContext;
- Memory revocation/tombstone/deletion/erasure implementation.

## 11. Future release gate

Before any production promotion implementation may be CLAIMED, a fresh
independent authority audit must freeze:

1. the concrete server authority that mints/reserves promotion_authority_id;
2. the source-proof port/receipt shape;
3. supported source kinds;
4. source-specific proof ownership;
5. stale-proof/TOCTOU semantics;
6. exact atomic handoff behavior into F5-2;
7. failure/result contract;
8. runtime/API visibility;
9. tests proving unsupported/foreign/stale authority fails closed.

A production runtime, source adapter, schema, repository, or wiring change
requires a separate production release and merge authorization.

## 12. Acceptance evidence for this slice

This F5-3A slice is complete only when evidence proves:

- it is contract/evidence only;
- source claim != current source proof;
- promotion authority is server-owned;
- stale/unprovable source proof fails closed;
- CAS and R11 ownership remains external;
- F5-2 identity/content/admission semantics are reused;
- no adjacent retrieval/model/personalization/lifecycle authority is opened;
- no global historical invariant is introduced that would prohibit a later
  separately released production stage.
