# CTX-F5-3B — Generic Memory Promotion Handoff Contract Freeze

## Status

~~~text
stage                         = CTX-F5-3B
mode                          = CONTRACT / AUTHORITY FREEZE ONLY
claim baseline                = f7c02b4023692d9e3bc5ccb91738e4d7f729d003
baseline Architecture         = #1332 GREEN/GREEN
production delta              = ZERO
concrete supported source kinds = NONE
production promotion runtime  = NOT RELEASED
source-specific adapters      = NOT RELEASED
HTTP/public promotion API     = CLOSED
automatic promotion           = CLOSED
Memory retrieval/model use    = CLOSED
~~~

This slice freezes the generic authorization handoff required before any
production Memory promotion runtime can be released. It changes no production
service, protocol implementation, endpoint, adapter, repository, schema,
migration, loader, hydration path, retrieval path, or runtime wiring.

F5-3B consumes the F5-3A authority separation and the existing F5-2 Memory
identity/admission semantics. It does not create a second Memory identity,
repository, replay authority, or persistence path.

## 1. Authority separation remains normative

The canonical separation remains:

~~~text
source authority
!= ContextSourceRef projection
!= source proof / receipt
!= promotion reservation / authorization envelope
!= durable Memory persistence
!= Memory retrieval authorization
!= model Working Set visibility
~~~

A validated ContextSourceRef is an immutable source identity/provenance claim.
It is not current read/liveness authority and is not Memory promotion
authorization.

A source proof/receipt proves a source-owner decision. It does not itself mint
Memory promotion authority.

A promotion reservation authorizes one exact promotion intent. It is not a
second Memory identity and does not replace F5-2 admission/replay semantics.

## 2. Generic source proof / receipt contract

A future source authority port may return a provider-neutral immutable
source-proof receipt only after re-proving the requested source claim for the
trusted owner scope.

The receipt must bind at least:

~~~text
source_kind
context_source_id
authority_id
authority_version / source revision where applicable
owner_user_id
current read/liveness authorization scope
proof_receipt_id
source authority revision/state token
ordering evidence sufficient for stale-proof rejection
~~~

The exact validated ContextSourceRef must match the receipt's source identity.
The proof owner must equal both the trusted server-derived owner scope and the
source provenance owner.

A timestamp alone is NOT sufficient freshness authority.

The source owner must define what its revision/state token or receipt guarantees
for one promotion admission. CTX must not invent cross-store atomicity. If a
source authority cannot provide deterministic stale/foreign-proof rejection,
that source kind remains unsupported and promotion fails closed.

Old projections, old successful reads, discovery hits, model output, provider
metadata, or persisted provenance snapshots do not substitute for current
source proof.

## 3. Promotion reservation / authorization envelope

A server-owned promotion_authority_id is necessary but not sufficient
authorization.

Before F5-2 admission, trusted server authority must issue or reserve an
immutable promotion reservation / authorization envelope bound to the exact
validated promotion intent.

The reservation must bind at least:

~~~text
promotion_authority_id
trusted owner_user_id
exact validated ContextSourceRef snapshot or canonical snapshot digest
exact context_source_id
source proof_receipt_id
source authority revision/state token used for the admission decision
content_digest
canonical metadata digest or exact canonical metadata
memory_schema_version
~~~

The reservation/verifier must reject an attempted use when any authorized
intent component differs, including:
- trusted owner scope;
- exact source snapshot or context_source_id;
- proof receipt identity;
- source authority revision/state token;
- content_digest;
- canonical metadata;
- memory_schema_version.

created_at is not required to be part of the authorization/replay identity
because existing F5-2 immutable replay equivalence intentionally excludes only
created_at.

The promotion reservation is server-issued/server-verifiable. A client, model,
source id, asset id, transcript id, Session/Task/Branch id, tool response id,
provider id, FileProviderBinding id, search result, score, or existing
ContextSourceRef cannot mint or substitute it.

## 4. Why exact-intent binding is required

Existing F5-2 Memory identity binds:

~~~text
context_source_id
promotion_authority_id
content_digest
memory_schema_version
~~~

Existing F5-2 immutable replay equivalence checks the fuller Memory record while
excluding created_at, so source_ref_snapshot and metadata participate in replay
equivalence even though they are not independently encoded in memory_id.

Therefore repository uniqueness can reject an inconsistent replay after a
winner exists, but it cannot by itself prove that the first admission was
authorized for the exact source snapshot and metadata.

The promotion reservation closes that authorization gap before admission.

## 5. Responsibility split

Names of future production interfaces are deliberately non-normative, but these
responsibilities must remain separate:

~~~text
source authority port
  requested validated source claim + trusted owner
  -> current source proof / receipt

promotion authority issuer / reserver
  exact validated promotion intent
  -> server-owned promotion reservation

promotion handoff validator / orchestrator
  verifies source claim + proof + reservation + canonical payload
  -> create_memory_record(...)
  -> exactly one existing MemoryRecordRepository.put(...)
~~~

create_memory_record() is not promotion authorization. Current production code
describes it as constructing a dormant Memory value from already-authorized
provenance input. A future runtime must verify authorization before invoking it.

F5-3B does not release any of these production interfaces or implementations.
It freezes only their semantic boundary.

## 6. Exact handoff into F5-2

A future separately released production handoff must:

1. obtain trusted server-derived owner scope;
2. receive and validate the exact ContextSourceRef claim;
3. obtain current source proof from the owning source authority;
4. verify claim/proof/owner equality and freshness semantics;
5. canonicalize content and metadata using existing F5 rules;
6. derive the existing canonical content_digest;
7. reserve/verify the exact-intent promotion authorization envelope;
8. invoke existing create_memory_record(...) only after authorization;
9. perform exactly one existing MemoryRecordRepository.put(...) admission;
10. preserve all F5-2 idempotency, replay-conflict, persistence, and transaction
    semantics.

No alternate Memory repository, identity, admission path, replay authority, or
idempotency authority is released.

## 7. Failure semantics

Exact exception/API names remain for a later production release, but future
behavior must distinguish these semantic failure classes:

- unsupported source kind or source authority;
- source missing or unavailable;
- source not currently readable/authorized;
- source owner mismatch;
- source claim/proof mismatch;
- stale, invalid, or foreign source proof;
- promotion reservation missing or invalid;
- promotion reservation intent mismatch;
- promotion authority replay conflict;
- F5-2 Memory admission/replay conflict;
- persistence failure.

All uncertain authorization states fail closed.

## 8. Supported source kinds

F5-3B claims ZERO concrete source kinds as supported for production promotion.

The current ContextSourceKind vocabulary may contain SESSION, TASK, BRANCH,
AGENT_TRANSCRIPT, ASSET, and TOOL_RESPONSE_PAYLOAD, but no kind becomes
promotable by implication.

SESSION/TASK/BRANCH/TOOL_RESPONSE_PAYLOAD require separately audited
source-specific proof semantics before production promotion.

ASSET production promotion remains blocked until CAS-owned read/liveness proof
semantics are separately frozen and released.

AGENT_TRANSCRIPT or other R11-owned durable evidence promotion remains blocked
until R11-owned read/liveness proof semantics are separately frozen and
released.

## 9. Cross-track ownership

### CAS / ASSET

Issue #74 / CAS retains FileAsset, FileBlob, FileReference, ObjectStorage,
provider hydration/upload, FileProviderBinding, asset read/liveness,
reconciliation, lifecycle, deletion, and physical-GC authority.

CTX may later consume only a narrow CAS-owned proof/receipt after a separate
source-specific release. Provider identity is never Memory promotion authority.

### R11 / durable execution evidence

Issue #31 / R11 retains transcript/checkpoint source authority, current
read/liveness semantics, retention, and destructive-GC authority.

CTX may later consume only a narrow R11-owned proof/receipt after a separate
source-specific release. Memory provenance must not become a source retention
root.

## 10. Explicitly closed authority

F5-3B does not release:

- production Memory promotion service/runtime;
- production Protocol/adapter/issuer/verifier/orchestrator implementation;
- HTTP/public Memory promotion endpoint;
- automatic promotion;
- any concrete supported source kind;
- source-specific adapter, loader, read path, or hydration;
- CAS ObjectStorage/provider/lifecycle/GC authority;
- R11 retention/checkpoint/destructive-GC authority;
- ContextSourceKind.MEMORY;
- alternate Memory repository/identity/replay/idempotency paths;
- Memory retrieval/search/ranking/vector/index/chunk storage/embeddings;
- score, recency, top_k, or global semantic retrieval;
- ContextBuilder or model Working Set visibility;
- F6 Personalization;
- F7 pins/scoring/dedupe;
- F9 Working Set / ContextSnapshot;
- F10 CompactContext;
- revocation, tombstone, deletion, or erasure implementation.

Any production implementation requires a fresh explicit release and separate
production merge authorization.

## 11. Future production release gate

Before a production promotion handoff may be CLAIMED, a fresh independent audit
must freeze at minimum:

1. concrete production interface names and ownership;
2. trusted promotion reservation issuer/verifier lifecycle;
3. exact canonical source-snapshot digest or snapshot representation;
4. exact canonical metadata digest/representation;
5. source-proof freshness verification timing at admission;
6. supported concrete source kinds and their source-owner proof adapters;
7. result/exception/API semantics;
8. dependency injection/runtime wiring location;
9. cancellation/deadline/transaction behavior where applicable;
10. tests proving source/proof/reservation/content/metadata/schema mismatch fails
    closed before durable admission.

## 12. Acceptance evidence for this slice

F5-3B is complete only when evidence proves:

- the slice is docs + architecture evidence only;
- promotion_authority_id alone is explicitly insufficient authorization;
- the promotion reservation binds the exact promotion intent;
- source proof is distinct from promotion reservation;
- timestamp-only freshness is rejected;
- create_memory_record() remains non-authorizing;
- existing F5-2 Memory identity and full immutable replay facts are accurately
  represented;
- the only allowed future durable handoff is exactly one existing
  MemoryRecordRepository.put(...);
- zero concrete source kinds are released;
- CAS/R11 ownership remains external;
- production runtime/retrieval/model visibility remain CLOSED;
- no global historical absence invariant prevents a later separately released
  production implementation.
