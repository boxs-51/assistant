# CTX-F4A — Structural Resolve / Describe Contract

Parent authority:

```text
canonical main      = a74a3ca44f78a7b7cd575265d9e213d88b574ade
parent PR #64        = MERGED / CLOSED
audited parent HEAD  = df425f38defba3bf7b21542f31092266cfd51d40
parent merge commit  = a74a3ca44f78a7b7cd575265d9e213d88b574ade
relationship         = DIRECT CHILD OF CANONICAL MAIN
```

## Purpose

CTX-F4A is the first bounded Context Access slice under CTX-F4.

It exposes only exact structural `resolve` and `describe` semantics over finite,
already-loaded Session/Task/Branch evidence. It does not introduce persistence,
loading, search, content hydration, ranking, lifecycle authority, or runtime/tool
integration.

The contract preserves the frozen Context separation:

```text
SOURCE AUTHORITY
!=
CONTEXT PROJECTION
!=
MODEL WORKING SET
```

## Public authorization boundary

A bare `DiscoveryCollection` is not authorization proof.

The public F4A functions do not accept a caller-supplied collection. They accept
only the same evidence-carrying finite inputs consumed by FINAL-GREEN F3B:

```text
(SessionEvidence, SessionDigest)
(SessionEvidence, TaskEvidence, TaskDigest)
(SessionEvidence, TaskEvidence, BranchEvidence, BranchDigest)
```

Every public resolve/describe call internally executes:

```python
build_discovery_collection(...)
```

before any selector match.

Therefore the F3B/F3A trust boundary is replayed for each access operation:

- complete F3A digest shape is revalidated;
- source identity/lineage integrity is revalidated;
- canonical F2B/F3A provenance is re-projected from supplied already-authorized
  evidence;
- the re-projected digest must equal the supplied digest;
- forged raw `ContextSourceRef` plus `model_construct(...)` digest paths fail;
- a forged `DiscoveryCollection.model_construct(...)` cannot enter the public
  F4A API at all.

No private Python token or marker is treated as access authorization.

## Exact selector contract

The first F4A slice accepts only:

```text
exact canonical context_source_id
```

The selector must be the exact canonical lowercase SHA-256 identity already
derived by CTX-F2.

There is no:

- native Task ID lookup;
- native Branch ID lookup;
- implicit latest/current revision;
- revision ordering;
- partial identity match;
- owner override;
- source-kind override;
- caller-created source reference.

TASK and BRANCH revisions remain distinct because each canonical revision already
has a distinct `context_source_id`.

The selector is only a lookup key against the internally rebuilt single-owner
F3B collection. It creates no authority.

## Resolve contract

`resolve_context_source(...)`:

1. validates the exact selector shape;
2. rebuilds the authorized F3B collection from evidence-carrying inputs;
3. matches the selector across Session/Task/Branch groups;
4. returns exactly one immutable authorized F3A digest when present;
5. returns deterministic `NOT_FOUND` only when a valid non-empty owner-scoped
   collection exists but the exact selector is absent;
6. raises `ContextAccessAuthorityError` when zero evidence is supplied because
   no canonical owner scope exists in which absence can be classified.

F3B global canonical-source dedupe means conflicting/duplicate identity cannot
survive collection rebuild.

Resolve performs no source fetch, state mutation, lifecycle interpretation, or
content hydration.

## Describe contract

`describe_context_source(...)` executes the same provenance re-proof before
lookup.

The description is a frozen structural value copied only from the selected
authorized F3A digest and its canonical source reference:

- canonical source identity;
- source kind;
- owner;
- authority ID/version;
- Session/Task/Branch lineage IDs;
- source creation/state metadata;
- F3A projection version;
- summary/topics/keywords;
- type-specific title/objective/decisions/remaining items already present.

Describe does not create new summarization, inference, ranking, lifecycle
interpretation, or source-state transformation.

Historical terminal Task / resolved Branch state is surfaced exactly as already
projected, without reactivation.

## Explicit non-scope

```text
context.search
context.read
SQL/repository/runtime reads
AgentRepository/list_task_branches/list_iterations
persisted discovery/search/index
SQLAlchemy/Alembic/query-plan/index work
filesystem/blob/object-store reads
ASSET / CTX-F2C consumption
R11 retention/GC ownership
durable GC root / source-lifetime extension
background indexer/loader
vector/embedding/ranking/fuzzy/NL search
ContextBuilder/AgentRuntime/tool registration
Task/Branch lifecycle mutation
Memory/Personalization/Pins/Working Set/ContextSnapshot/CompactContext
```

A future slice that adds persistence reads, historical loading, persisted indexes,
or source-lifetime guarantees requires a fresh Issue #15 <-> Issue #31 ownership
audit.

CTX-F2C / PR #59 is LANDED on canonical main, but F4A ASSET resolution/read
remains CLOSED. This F4A slice continues to accept Session/Task/Branch evidence
only and does not consume AssetEvidence or project_asset_source.

## Initial owned files

```text
docs/context_future/CTX_F4A_STRUCTURAL_ACCESS_31B64541.md
se/src/context/access.py
se/tests/architecture/test_ctx_f4_structural_access.py
```

No F3A/F3B parent file is modified by this first candidate.

## Acceptance evidence

Architecture evidence must prove:

- exact Session/Task/Branch source-ID resolve;
- Task revision identities remain distinct;
- zero evidence fails with explicit ContextAccessAuthorityError;
- deterministic NOT_FOUND for non-empty valid owner scope with absent selector;
- noncanonical/partial selectors fail closed;
- describe copies only existing structural metadata;
- historical Branch state is not reinterpreted;
- forged F3A digest provenance is rejected through F3B re-proof;
- bare/forged DiscoveryCollection cannot enter public F4A access;
- result/description values are immutable;
- no native-ID or owner-override selector is exposed;
- no search/read/storage/runtime/retention dependency is imported.

## Merge and stage fence

CTX-F4A has been re-anchored directly onto canonical main after PR #64 landed.

This contract authorizes no merge of the F4A child. PR #59
is already landed and does not authorize ASSET access in this slice.
Merge remains a separate explicit user decision.


## P1 closure — empty authority semantics

The refreshed F4A candidate explicitly distinguishes two states that must not be
collapsed:

```text
zero evidence / no canonical owner scope
    => ContextAccessAuthorityError

valid non-empty single-owner scope + exact selector absent
    => NOT_FOUND
```

Zero evidence is therefore an access-authority/input error, not ordinary
absence. The public API still has no caller-supplied owner override, and the
precondition does not construct an owner. Every non-empty access continues
through `build_discovery_collection(...)`, preserving the F3B provenance and
single-owner boundary.

This closes the implementation requirement of
`P1-CTX-F4A-EMPTY-AUTHORITY-SEMANTICS-1` subject to exact-head CI and
independent re-audit.

## Refreshed parent chain

```text
canonical main        = a74a3ca44f78a7b7cd575265d9e213d88b574ade
landed F3A #62 HEAD    = 7696880edcd887b26eeff36356d17db3dfa49d58
landed F3A merge       = 9a84a3a9055f371162289d2741f360e80ef5fa81
landed F3B #64 HEAD    = df425f38defba3bf7b21542f31092266cfd51d40
landed F3B merge       = a74a3ca44f78a7b7cd575265d9e213d88b574ade
this F4A relationship  = DIRECT CHILD OF CANONICAL MAIN
```

F3A and F3B have landed after their exact-head CI/audit gates. This refreshed F4A candidate remains non-integrating until its new exact-head CI and independent audit gates close.


## Post-F3B landing refresh

M1 integration advanced through:

```text
CTX-F3A PR #62 merge -> 9a84a3a9055f371162289d2741f360e80ef5fa81
CTX-F3B PR #64 audited HEAD -> df425f38defba3bf7b21542f31092266cfd51d40
CTX-F3B PR #64 merge -> a74a3ca44f78a7b7cd575265d9e213d88b574ade
```

This F4A refresh replays the previously FINAL-GREEN production source and
architecture-test blobs byte-for-byte onto exact canonical
`main@a74a3ca4`. Only this contract updates integration history and parent
authority.

The P1 empty-authority semantics remain unchanged:
- zero evidence / no canonical owner scope -> ContextAccessAuthorityError;
- valid non-empty single-owner scope + absent selector -> NOT_FOUND.

No ASSET access, search/read, SQL/repository/runtime access, CAS lifecycle or
provider hydration/GC, R11 retention authority, Working Set, Memory, or
Personalization is opened by this refresh.

Fresh exact-head Linux + Windows Architecture and refreshed independent Issue
#15 audit are required before any merge decision.
