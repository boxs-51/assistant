# CTX-F3B — Immutable Derived Discovery Collection

## 1. Authority

CTX-F3B is a local Issue #15 substage label under the existing roadmap phase:

```text
CTX-F3 = Session / Task / Branch discovery
```

It does not create a new top-level roadmap namespace.

Parent authority:

```text
canonical main = 9a84a3a9055f371162289d2741f360e80ef5fa81
CTX-F3A PR #62 = MERGED / CLOSED
audited CTX-F3A HEAD = 7696880edcd887b26eeff36356d17db3dfa49d58
CTX-F3A merge commit = 9a84a3a9055f371162289d2741f360e80ef5fa81
CTX-F3B integration relationship = DIRECT CHILD OF CANONICAL MAIN
branch = work/ctx-f3b-collection-7ac9f7c0
```

Integration order is preserved: F3A landed first; F3B is now replayed directly on the resulting canonical main.

## 2. Purpose

F3A defines canonical, rebuildable:

- SessionDigest;
- TaskDigest;
- BranchDigest.

F3B adds only a pure immutable collection projection over those authorized digest values.

The collection is not a database index, search service, registry, cache, retention policy, lifecycle authority or model-visible working set.

```text
canonical authority
  -> F2B source projection
  -> F3A digest
  -> F3B immutable collection value
```

Every layer remains derived and rebuildable.

## 3. Collection contract

The first candidate exposes:

```text
DiscoveryCollection
build_discovery_collection(...)
```

A collection contains:

```text
collection_version
owner_user_id
sessions: tuple[SessionDigest, ...]
tasks: tuple[TaskDigest, ...]
branches: tuple[BranchDigest, ...]
```

The public builder does not accept bare digest objects as authority. Every input digest must travel with the canonical already-loaded evidence from which F2B/F3A authority can be re-projected:

```text
sessions = (SessionEvidence, SessionDigest)
tasks    = (SessionEvidence, TaskEvidence, TaskDigest)
branches = (SessionEvidence, TaskEvidence, BranchEvidence, BranchDigest)
```

F3B re-projects the expected F3A digest source authority from that evidence using the landed F3A projector chain and requires exact digest equality before inclusion.

The builder does not accept an owner override. The partition owner is derived from the re-proven canonical F3A digest source identities.

An empty collection is rejected because there is no canonical evidence from which to derive owner authority.

## 4. Single-owner partition

All included digests must resolve to exactly one canonical owner_user_id.

Mixed-owner input fails closed.

This stage does not create a global multi-owner registry.

## 5. F3A digest authority, source identity and lineage validation

F3B revalidates the complete F3A digest contract before accepting a value into the collection. This is required because low-level Pydantic construction paths such as model_construct(...) can bypass normal F3A validation.

The accepted digest must use the exact F3A projection contract version consumed by this stage:

```text
projection_version = ctx-f3a-v1
```

F3B also revalidates canonical derived-field shape:

- common: summary, topics, keywords;
- SessionDigest: title;
- TaskDigest: objective, important_decisions, remaining_items;
- BranchDigest: important_decisions.

Canonical text must already be normalized, canonical text tuples must contain normalized non-empty strings without duplicates, and an incompatible projection_version fails closed.

Shape validation is not provenance. After shape/source-lineage validation, F3B requires corresponding canonical evidence and re-projects through:

```text
SessionEvidence
  -> project_session_digest(...)

SessionEvidence + TaskEvidence
  -> project_task_digest(...)

SessionEvidence + TaskEvidence + BranchEvidence
  -> project_branch_digest(...)
```

The caller-supplied derived text is reused only as projector input; the canonical source owner/lineage authority must be reproduced from the evidence. If the re-projected digest is not exactly equal to the supplied digest, F3B fails closed.

This blocks the forged path:

```text
create_context_source_ref(...)
+ model_construct(F3A digest)
+ canonical-looking derived fields
!= authorized F3B input without matching canonical evidence
```

Only after full digest-contract and provenance validation does F3B accept the digest into dedupe/collection semantics.

SESSION:

```text
source_kind = SESSION
session_id == authority_id
no task/branch lineage
```

TASK:

```text
session_source = SESSION
task_source = TASK
owners equal
task_source.session_id == session authority
task_source.task_id == task authority
no branch lineage on task source
```

BRANCH:

```text
session_source = SESSION
task_source = TASK
branch_source = BRANCH
owners equal
session lineage equal
branch.task_id == task authority
branch.branch_id == branch authority
```

Raw ContextSourceRef values are not accepted as collection input and are never upgraded from integrity-valid to authorized discovery evidence.

F3B consumes canonical F3A digest values only.

## 6. Deterministic ordering

Each typed digest group is ordered by canonical context_source_id.

Therefore equivalent finite input produces the same immutable collection independent of caller input ordering.

No ranking, relevance, fuzzy search, natural-language query or embedding semantics are introduced.

## 7. Dedupe and conflict behavior

The canonical dedupe key is ContextSourceRef.context_source_id.

For duplicate canonical source identities:

- equality-equivalent digest values collapse deterministically;
- different derived payloads for the same canonical source identity fail closed.

The first candidate intentionally does not invent:

- latest-wins authority;
- mutable upsert semantics;
- projection timestamp authority;
- incremental update ordering.

Native Task or Branch revision changes remain distinct because F2/F3A produce distinct context_source_id values for versioned authority.

## 8. Historical state behavior

Terminal Tasks and resolved Branches remain represented if they are present in the authorized input.

Examples include:

```text
Task: COMPLETED / FAILED / CANCELLED
Branch: ADOPTED / SUPERSEDED / DISCARDED / CANCELLED
```

F3B does not resume, reactivate, adopt, supersede, discard, cancel or otherwise mutate those authorities.

Historical presence in a collection does not guarantee source retention.

## 9. Rebuild and mutation semantics

F3B is complete-snapshot only.

```text
finite authorized input
  -> deterministic rebuild
  -> new immutable DiscoveryCollection value
```

There is no:

- append API;
- update API;
- delete API;
- persisted cursor;
- watermark;
- background updater;
- TTL;
- retention lifecycle;
- durable registry.

A new rebuild creates a new derived value. It does not mutate an existing collection.

## 10. R11 retention / GC boundary

Issue #31 owns Agent retention and live-root semantics.

F3B does not guarantee that historical Session/Task/Branch evidence remains available forever.

It does not:

- extend source lifetime;
- create a durable GC root;
- define collectibility;
- define TTL;
- own deletion ordering;
- own checkpoint/execution retention.

The only valid statement is:

```text
historical evidence present in authorized input may appear in the derived collection
```

Any future persisted discovery index, storage loader, historical retention dependency or lifetime guarantee requires a fresh Issue #15 <-> Issue #31 ownership audit.

## 11. CTX-F4 boundary

F3B structures finite authorized data only.

The following remain CTX-F4 and are absent:

```text
context.resolve
context.search
context.describe
context.read
ranking
fuzzy search
relevance scoring
embedding/vector lookup
natural-language discovery query
```

Structural assertions in architecture tests do not create F4 authority.

## 12. ASSET boundary

CTX-F2C / PR #59 and CAS-R1-A / PR #69 are LANDED; current canonical main is `9a84a3a9055f371162289d2741f360e80ef5fa81` after CTX-F3A / PR #62 also landed.

Canonical main now contains the dormant ASSET source projection, but F3B remains
Session / Task / Branch collection-only. F3B contains no ASSET discovery input,
does not import AssetEvidence or project_asset_source, and does not treat landed
F2C as authority to expand discovery scope.

F3/F4 ASSET access remains closed unless separately released/audited.

## 13. Hard non-scope

```text
NO SQL/repository reads
NO SQLAlchemy/Alembic/physical DB index/query-plan changes
NO AgentRepository/list_task_branches/list_iterations changes
NO persisted registry/search/index table
NO retention guarantee / TTL / GC root
NO R11 lifecycle/retention ownership
NO vector/embedding activation
NO background indexer
NO ContextBuilder/AgentRuntime wiring
NO Task/Branch lifecycle mutation
NO Memory/Personalization/Pins/Working Set/ContextSnapshot/CompactContext
NO F4 resolve/search/describe/read
NO ASSET discovery/consumption despite landed CTX-F2C
```

## 14. Acceptance evidence

The first candidate must prove:

1. a canonical F3A digest set builds one frozen owner-scoped collection;
2. owner is derived from digest authority rather than caller input;
3. mixed-owner input fails closed;
4. finite input order does not change collection order;
5. equality-equivalent duplicate identity collapses;
6. conflicting payload for one canonical source identity fails closed;
7. native Task revision change remains a distinct source identity;
8. terminal Task state remains descriptive/historical;
9. resolved Branch state remains descriptive/historical;
10. raw ContextSourceRef input is rejected;
11. complete F3A digest authority is revalidated even when model_construct(...) bypasses F3A initialization;
12. incompatible F3A projection_version is rejected;
13. SessionDigest, TaskDigest and BranchDigest invalid derived payloads fail closed with canonical source refs and valid lineage;
14. bare digest objects without evidence are rejected;
15. forged self-consistent SESSION/TASK/BRANCH source refs wrapped with canonical-shaped model_construct digests are rejected when canonical evidence does not re-project the same authority;
16. normal canonical F3A projector outputs with matching evidence remain accepted;
17. embedded digest lineage is revalidated;
18. empty caller-created owner partition is rejected;
19. direct collection construction is closed behind the builder;
20. collection is immutable;
21. no incremental mutation/query/retention surface exists;
22. production module imports no storage/runtime/SQL/Alembic authority;
23. exact-head Architecture Linux and Windows must be GREEN before semantic close.

## 15. Candidate paths

```text
docs/context_future/CTX_F3B_DISCOVERY_COLLECTION_7AC9F7C0.md
se/src/context/discovery_collection.py
se/tests/architecture/test_ctx_f3_discovery_collection.py
```

No F3A file is modified by this first stacked child candidate.


## 16. Post-F2C / refreshed-F3A replay

After CTX-F2C landed, CTX-F3A was refreshed onto exact canonical main and now has
candidate HEAD:

```text
705e11aafb6fbdc3464016b94b96e49573f9a67e
```

This F3B refresh is stacked directly on that exact parent. The production
`discovery_collection.py` and its architecture regression are replayed
byte-for-byte from FINAL-GREEN F3B `31b64541...`; only this contract updates
dependency history and exact parent authority.

No storage, retention, F4, runtime, lifecycle or ASSET scope is added by the
refresh. Fresh exact-head Linux + Windows Architecture and independent re-audit
remain required before any integration decision.


## 17. Post-CAS-R1-A / landed-F3A canonical-main refresh

M1 integration advanced through:

```text
CAS-R1-A PR #69 merge -> face48cfd63e4ed09c8db3dc9bbe54cf64b43fa1
CTX-F3A PR #62 audited HEAD -> 7696880edcd887b26eeff36356d17db3dfa49d58
CTX-F3A PR #62 merge -> 9a84a3a9055f371162289d2741f360e80ef5fa81
```

F3B is therefore no longer integrated as an unmerged stacked child. This
refresh replays the previously FINAL-GREEN F3B production source and
architecture-test blobs byte-for-byte onto exact canonical
`main@9a84a3a9`.

Only this contract updates integration history and parent authority. The F3B
scope remains exactly the three F3B-owned paths. No F3A file is part of the
refreshed PR delta.

This refresh does not open ASSET discovery/read, CAS lifecycle/provider
hydration/GC, persistence/indexing, R11 retention, F4 access APIs, runtime
wiring, Working Set, Memory, or Personalization.

Fresh exact-head Linux + Windows Architecture and refreshed independent Issue
#15 audit are required before any merge decision.
