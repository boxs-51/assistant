# CTX-F4B — Finite Structural Search

## 1. Authority

Primary workspace: Issue #15.

Exact implementation baseline:

```text
canonical main = 200aa3dcce3701b620e510a2a618ebccaddb6dff
post-M2 main Architecture #1197 = GREEN / GREEN
CTX-F3A/F3B/F4A = LANDED
P1-CTX-F4A-EMPTY-AUTHORITY-SEMANTICS-1 = CLOSED
```

Independent PRE-CLAIM release:

```text
Issue #15 comment #5822654258
CTX-F4B finite already-loaded structural search = CLAIM RELEASED
branch creation / 3-file implementation = RELEASED
merge/integration = NOT RELEASED
CTX-F5 / context.read / persisted search = CLOSED
```

Implementation branch:

```text
work/ctx-f4b-search-daa672f2
```

This refreshed slice is an exact child of canonical main after CAS-R1-C / PR #73 landed.

## 2. Purpose

CTX-F4B adds only finite, deterministic structural search over already-loaded
Session/Task/Branch evidence.

It extends CTX-F4 without adding a loader, persistence layer, index, ranking
engine, content hydration path, retention policy, lifecycle authority, or model
working-set behavior.

The frozen separation remains:

```text
SOURCE AUTHORITY
!=
CONTEXT PROJECTION
!=
MODEL WORKING SET
```

## 3. Public boundary

The first slice exposes:

```python
search_context_sources(
    query,
    *,
    sessions=(),
    tasks=(),
    branches=(),
) -> ContextSearchResult
```

The evidence inputs are exactly the evidence-carrying values already accepted by
F3B/F4A:

```text
(SessionEvidence, SessionDigest)
(SessionEvidence, TaskEvidence, TaskDigest)
(SessionEvidence, TaskEvidence, BranchEvidence, BranchDigest)
```

Every call re-proves authority through:

```python
build_discovery_collection(...)
```

before matching any text.

The public boundary does not accept:
- DiscoveryCollection;
- raw ContextSourceRef;
- owner_user_id;
- native Session/Task/Branch IDs;
- repository/loader/runtime handles;
- AssetEvidence;
- caller-provided ordering/ranking controls.

F3B remains the collection/provenance authority.

## 4. Query normalization

Query normalization is internal and deterministic.

Rules:

1. query must be `str`;
2. leading/trailing whitespace is stripped;
3. empty-after-strip is rejected;
4. matching uses `casefold()` for both query and candidate field text;
5. normalization does not mutate source evidence or digest values.

Caller formatting is not treated as authority.

## 5. Matching semantics

Matching is ordinary literal substring containment over each field independently.

```text
casefold(query) in casefold(field_value)
```

There is no cross-field concatenation.

Explicitly absent:
- regex;
- tokenization;
- stemming;
- word-boundary interpretation;
- fuzzy matching;
- edit distance;
- synonyms;
- embeddings/vector search;
- natural-language interpretation.

## 6. Closed searchable field set

SESSION:
- title;
- summary;
- topics;
- keywords.

TASK:
- objective;
- summary;
- topics;
- keywords;
- important_decisions;
- remaining_items.

BRANCH:
- summary;
- topics;
- keywords;
- important_decisions.

Not searchable:
- owner IDs;
- native Session/Task/Branch IDs;
- context_source_id;
- source kind;
- lineage IDs;
- authority revision/version;
- source lifecycle/state;
- timestamps;
- CAS/asset metadata.

Lifecycle/state remains structural authority metadata only.

## 7. Result contract

`ContextSearchResult` is an immutable derived value:

```text
query = trimmed caller query
hits  = tuple[authorized F3A digest, ...]
```

A source appears at most once even when several allowed fields match.

All hits are globally sorted by canonical `context_source_id`, across
Session/Task/Branch kinds. There is no per-kind concatenation ordering.

The result is not:
- a new authorization token;
- a persistence handle;
- a durable registry/index;
- a retention root;
- a mutable collection.

No score, confidence, matched-field weighting, relevance tier, recency boost,
latest/current preference, pagination, cursor, max_results, top_k, or
caller-defined sort exists.

## 8. Empty and no-authority distinction

The F4A authority distinction remains frozen.

```text
zero evidence / no canonical owner scope
    => ContextAccessAuthorityError

valid non-empty single-owner scope + zero textual matches
    => immutable ContextSearchResult(..., hits=())
```

No textual match is not an exact-selector NOT_FOUND condition.

## 9. Historical state behavior

Terminal Tasks and resolved Branches may be returned only when an allowed
structural text field matches.

Search does not:
- search lifecycle/state metadata;
- prefer active/latest/current entities;
- resume or reactivate Tasks;
- adopt/reopen Branches;
- reinterpret source state.

## 10. R11 boundary after M2

M2 landed R11 E2/F0/F1-A/F1-B. R11-F1-B is non-destructive dry-run only and
R11-F1-C is separately stage-gated.

CTX-F4B:
- performs no AgentRepository or SQL read;
- creates no persisted index;
- extends no source lifetime;
- creates no GC root;
- performs no delete;
- assumes no historical evidence retention.

It searches only finite evidence already supplied for the invocation.

Any future persisted search/loader/index or retention-dependent search requires a
fresh Issue #15 <-> Issue #31 ownership audit before CLAIM.

## 11. CAS boundary

CAS-R1-B / PR #70 and CAS-R1-C / PR #73 are LANDED; latest canonical main is `200aa3dcce3701b620e510a2a618ebccaddb6dff`.

CTX-F4B contains no:
- AssetEvidence;
- project_asset_source;
- ASSET source-kind handling;
- asset URI matching;
- AssetService;
- FileAsset/FileBlob/FileReference/FileProviderBinding access;
- ObjectStorage access;
- provider hydration;
- CAS lifecycle/release/physical-GC authority.

F5-0/F5 remain CLOSED.

CAS-R1-B landing changed only CAS evidence/regression paths and did not broaden F4B authority.\n\nIf canonical main advances again before F4B integration, the branch must be
refresh/re-anchored to the new exact main and receive fresh Architecture plus
independent Issue #15 audit. Drift never broadens F4B authority.

## 12. Exact candidate scope

Only these paths belong to the first candidate:

```text
docs/context_future/CTX_F4B_FINITE_STRUCTURAL_SEARCH_DAA672F2.md
se/src/context/search.py
se/tests/architecture/test_ctx_f4b_structural_search.py
```

No landed CTX production file is modified.

## 13. Acceptance evidence

Architecture must freeze:

- internal strip/casefold query normalization;
- explicit non-string/whitespace-only query failure;
- field-local literal substring matching;
- exact closed searchable field sets;
- metadata IDs/state/revision are not searchable;
- duplicate field matches produce one source;
- global context_source_id ordering across kinds;
- valid owner scope + no match gives immutable empty result;
- zero evidence gives ContextAccessAuthorityError;
- mixed owner and forged digest provenance fail through F3B;
- historical lifecycle state is not searched or mutated;
- no bare collection/owner/native-ID/ranking/pagination controls;
- no backend/ASSET/runtime/retention imports or surfaces;
- Linux + Windows exact-head Architecture GREEN;
- independent Issue #15 re-audit before merge preparation.

## 14. Hard non-scope

```text
NO context.read
NO persisted search/index/loader
NO SQL/repository/runtime reads
NO retention guarantee/source-lifetime extension
NO R11 retention/GC ownership
NO destructive behavior
NO ASSET discovery/search/read
NO CAS lifecycle/provider hydration/physical GC
NO ranking/scoring/fuzzy/vector/NL retrieval
NO pagination/cursor/top_k/max_results
NO ContextBuilder/AgentRuntime/tool registration
NO Working Set/ContextSnapshot
NO Memory/Personalization/Pins
NO CTX-F5 claim
```

This contract does not authorize merge.


## 15. Post-CAS-R1-B refresh

Canonical main advanced because CAS-R1-B / PR #70 landed:

```text
old F4B base      = daa672f2a93ad16cc3c6fe0a2e9edc0b3821b9ad
PR #70 audited HEAD = 5f612faadc5a226ec1df1047ae81b94a3854fbac
PR #70 merge/main   = c52fa42d61dcab3b4a7871f93942fd52bed6accd
```

The #70 merge touched only CAS evidence/regression scope and had production
delta zero. F4B production semantics are replayed byte-for-byte onto the new
canonical main.

Historical Architecture #1199 on the old F4B candidate was GREEN/GREEN, but is
not used as current integration evidence after this re-anchor.

The refreshed architecture regression additionally freezes the P1 audit gap:
positive matching evidence now covers every released SESSION, TASK and BRANCH
searchable field. No searchable field was added or broadened.

`P1-CTX-F4B-FIELD-FREEZE-EVIDENCE-1` is implementation-fixed in the refreshed
candidate, subject to fresh exact-head Architecture and independent re-audit.


## 16. Post-PR #73 refresh

Repository sequencing advanced through PR #72 and then PR #73 before CTX-F4B
integration.

```text
previous F4B base        = c52fa42d61dcab3b4a7871f93942fd52bed6accd
intermediate main        = 9a8ac14751880427db7d472499e2d42e286026d9
PR #73 audited HEAD      = 19db3848daa799573b96383cb0bdfdcaef0e2aa9
post-PR #73 canonical main = 200aa3dcce3701b620e510a2a618ebccaddb6dff
```

The intervening drift does not change CTX-F4B authority. PR #73 has production
delta zero, and the F4B production search implementation is replayed
byte-for-byte.

`P1-CTX-F4B-FIELD-FREEZE-EVIDENCE-1` remains CLOSED. The complete positive
SESSION/TASK/BRANCH field-freeze regression is preserved unchanged.

Post-PR #73 main Architecture #1206 is a separate canonical-main prerequisite
for final integration evidence. This re-anchor may be prepared while #1206 runs,
but no refreshed FINAL GREEN may be claimed until:
- exact main@200aa3dc #1206 is GREEN/GREEN;
- fresh exact-head PR #71 Architecture is GREEN/GREEN;
- independent Issue #15 refreshed audit passes;
- canonical main and PR HEAD remain unchanged.

No merge authorization is implied.
