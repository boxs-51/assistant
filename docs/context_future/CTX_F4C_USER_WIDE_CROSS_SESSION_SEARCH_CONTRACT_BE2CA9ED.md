# CTX-F4C — User-Wide Cross-Session Discovery/Search Contract

Status: CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
Canonical workspace: Issue #15
Policy: Issue #85 v2.5 + stricter Issue #15 local rules
Independent PRE-CLAIM: Issue #15 comment #6031996248
Owner CLAIM: Issue #15 comment #6032005612

## 1. Frozen baseline and authority

stage = CTX-F4C
development baseline = be2ca9ed5d0438a17f86fb8ab127b4dcfc95f7f7
baseline Architecture #2255 / 37576549147 = GREEN/GREEN
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = CLOSED
production CLAIM = NONE
merge authority = NONE

Exact changed-file maximum:

1. docs/context_future/CTX_F4C_USER_WIDE_CROSS_SESSION_SEARCH_CONTRACT_BE2CA9ED.md
2. se/tests/architecture/test_ctx_f4c_user_wide_cross_session_search_contract.py

exact changed paths = 2 NEW / 2
se/src/** delta = ZERO
cl/** delta = ZERO
repository/SQL delta = ZERO
persisted-index delta = ZERO
schema/migration delta = ZERO
runtime/API/client delta = ZERO
ContextBuilder/Working Set/ContextSnapshot delta = ZERO

Any production, repository, index, schema, migration, runtime, API, client,
ContextBuilder, Working Set, ContextSnapshot, model-visibility, Agent-identity,
or ASSET/provider implementation invalidates this CLAIM.

## 2. Purpose and namespace

CTX-F4C is the contract-only continuation of canonical CTX-F4 structural
access/search.

It freezes the future authority boundary for user-wide cross-session
discovery/search without implementing persistence, loading, indexing, ranking,
read/hydration, or model-visible selection.

CTX-F4C does not reopen or modify completed F3/F4 semantics by implication.
It inherits canonical source/provenance contracts and extends only the future
search/discovery contract boundary.

## 3. Frozen authority separation

The canonical separation is:

SOURCE AUTHORITY
!= SEARCH PROJECTION
!= READ / HYDRATION
!= MODEL WORKING SET

A search projection is derived evidence only.

A search projection, index row, result row, cached digest, query match, ranking
result, model output, or client request must never become source authority.

Canonical Session/Task/Branch/Transcript ownership and provenance remain
authoritative.

Before any future search result is used for read, hydration, or model exposure,
the system must be able to re-check current canonical source ownership,
provenance/version, and availability.

## 4. Trusted owner scope

Future user-wide cross-session discovery/search must be scoped by trusted,
authenticated owner authority resolved by the server.

The following do not independently mint owner or source authority:

- caller-supplied owner_user_id;
- Session ID;
- Task ID;
- Branch ID;
- transcript/search projection metadata;
- persisted index row;
- cached result;
- model output;
- client or connection metadata.

A future production stage must freeze the exact trusted principal resolution
before repository/index/runtime implementation is released.

## 5. F4B inheritance remains canonical

Canonical CTX-F4B remains unchanged.

Existing se/src/context/search.py::search_context_sources(...) continues to
mean finite search over already-loaded Session/Task/Branch evidence only.

Every F4B call continues to re-prove authority through
build_discovery_collection(...).

F4B matching semantics remain:

- internal strip + casefold query normalization;
- literal field-local substring matching;
- exact closed searchable field sets;
- one result per authorized source;
- deterministic global context_source_id ordering;
- zero evidence => ContextAccessAuthorityError;
- valid owner scope + no textual match => immutable empty result.

F4C does not rewrite, weaken, replace, or reinterpret F4B.

## 6. Persisted cross-session search remains unimplemented

CTX-F4C freezes only the future contract.

Still CLOSED:

- repository/SQL loader;
- persisted search/index;
- search projection tables;
- schema/migration;
- background indexer;
- replay/index rebuild runtime;
- search API/router/client endpoint;
- pagination/cursor/top_k;
- ranking/scoring;
- vector/embedding retrieval;
- semantic/NL retrieval;
- model Working Set injection;
- ContextBuilder injection;
- ContextSnapshot persistence/use.

Issue #15 must separately release every production slice.

## 7. R11 retention and read-liveness boundary

Issue #31/R11 retains transcript/checkpoint read-liveness, retention, and
destructive-GC authority.

CTX-F4C does not:

- guarantee that historical source bytes remain retained;
- create a retention root;
- extend Session/Task/Branch/Transcript lifetime;
- alter checkpoint/transcript retention;
- open destructive deletion or GC;
- infer read-liveness from the existence of a search projection.

Any future persisted loader/index that depends on retained historical data
requires a fresh CTX/R11 bilateral audit before production PRE-CLAIM.

Search-index durability must never be interpreted as source-data durability.

## 8. CAS ASSET/provider boundary

CAS #74 retains ASSET/FileAsset/FileBlob/ObjectStorage/provider lifecycle,
grants, hydration, deletion, and physical-GC authority.

CTX-F4C does not release:

- ASSET search;
- provider-backed file search;
- asset hydration/dereference;
- FileAsset/FileBlob/ObjectStorage reads;
- provider binding reads;
- cached CAS-grant substitution;
- asset lifecycle mutation.

Any future asset-bearing discovery/search/read path requires fresh CAS authority
and current grant/readability re-proof.

A CTX search projection may never make a revoked CAS grant usable.

## 9. Agent visibility and AIC boundary

Owner-wide search contract freeze is permitted without Agent-specific production
visibility.

A future Agent-specific visibility filter requires trusted server-resolved
agent_instance_id from separately canonical AIC authority.

Agent visibility must not be derived from:

- AgentDefinition name;
- AgentExecution.owner_instance_id;
- model output;
- request/client metadata;
- Session/Task/Branch metadata;
- search projection metadata.

Agent-specific visibility is a separate authorization gate from owner-wide
source discovery.

No AIC/APR production authority transfers to CTX-F4C.

## 10. #156 / DCS boundary

Issue #156 / DCS owns model-visible capability and Skill selection, not CTX
persisted discovery/search.

CTX-F4C acquires no authority for:

- CapabilityWorkingSet;
- ActiveSkillSet;
- Tool ranking;
- capability schema estimation;
- DCS lazy expansion;
- model-visible Tool selection;
- routing/sandbox policy.

DCS acquires no CTX source, search, persistence, or retention authority.

Direct path overlap with the released two-new-file F4C contract scope is zero.

## 11. Other cross-track fences

- AE/R12/R13 execution/recovery/lifecycle authority remains external.
- UBQ/TBO resource, quota, Task, scheduling, and timeout authority remains external.
- F5 Memory promotion/storage scope is not reopened by F4C.
- F6 Personalization remains CLOSED.
- F9 Working Set/ContextSnapshot remains CLOSED.
- F10 CompactContext remains CLOSED.

No dependency statement transfers production authority.

## 12. Exit gate

CTX-F4C contract FINAL requires:

- exact changed paths remain 2 NEW / 2;
- production/runtime/schema/migration/repository/index/API/client delta remains zero;
- architecture evidence proves F4B still uses finite already-loaded evidence and build_discovery_collection(...);
- architecture evidence proves persisted loader/index/search implementation remains absent from the released candidate;
- owner/source/provenance separation is frozen;
- R11/CAS/AIC/#156 boundaries are frozen;
- exact-head Linux Architecture is GREEN;
- exact-head Windows Architecture is GREEN;
- independent CTX-F4C contract FINAL is PASS;
- unresolved blocking review threads = 0;
- blocking P0/P1 = 0;
- no MATERIAL current-main drift invalidates the freeze.

The Issue #15 zero-production conditional merge exception may be evaluated only
after the full FINAL and immediate exact-main expected-head guard.

No production authority follows from this contract becoming canonical.
