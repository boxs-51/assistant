# CTX-F3A - Session / Task / Branch Discovery Projection Contract

Primary issue: #15
Branch: work/ctx-f3a-discovery-0853f9bf
Base at CLAIM: main@0853f9bf9be06ff89b3e4b826850e56d2f92c786
Refreshed integration base: main@face48cfd63e4ed09c8db3dc9bbe54cf64b43fa1
Status: CLAIMED / IMPLEMENTATION CANDIDATE
Upstream dependency: CTX-F2C PR #59 and CAS-R1-A PR #69 are LANDED; current canonical main is face48cfd63e4ed09c8db3dc9bbe54cf64b43fa1.

## 1. Purpose

CTX-F3A introduces the first dormant discovery projection contracts for canonical Session, Task and Branch sources.

The stage is intentionally pure and rebuildable:

canonical source authority
-> CTX-F2 ContextSourceRef
-> CTX-F3A digest projection

A discovery digest is not source authority, execution state, durable storage authority, model working context, or a Context Access API.

## 2. Source authority inherited from F2

SESSION authority remains the canonical chat/conversation Session projected by F2B. AgentSessionRecord remains a separate control-plane namespace.

TASK authority remains the F2 versioned Task source:
- authority_id = task id
- authority_version = task revision
- owner = canonical Session user
- session/task lineage remains native Agent authority

BRANCH authority remains the F2 versioned Branch source:
- authority_id = branch id
- authority_version = branch revision
- owner = canonical Session user
- session/task/branch lineage remains native Agent authority

Task status, Task revision and Branch resolution remain Agent-owned facts. F3A only describes them.

## 3. First-slice contracts

The production module provides immutable and rebuildable projections:
- SessionDigest
- TaskDigest
- BranchDigest

All digests carry a projection version plus derived summary, topics and keywords.

Session adds title.

Task adds objective, important decisions and remaining items.

Branch adds important decisions.

Canonical owner and lineage are never accepted as loose digest fields. Public F3A projection entry points consume the same already-loaded canonical Session/Task/Branch evidence used by F2B and re-project authoritative ContextSourceRef values through project_session_source, project_task_source and project_branch_source.

A raw integrity-valid ContextSourceRef is not treated as proof of canonical provenance or authorization. Digest constructors are factory-gated so callers cannot bypass the canonical F3A projection entry points with forged-but-self-consistent refs.

## 4. Lineage rules

SESSION requires:
- source kind SESSION
- session_id equals authority_id
- no task_id
- no branch_id

TASK requires:
- source kind TASK
- owner equals Session owner
- session_id equals Session authority_id
- task_id equals Task authority_id
- no branch_id

BRANCH requires:
- source kind BRANCH
- owner equals Session owner
- session_id equals Session authority_id
- task_id equals Task authority_id
- branch_id equals Branch authority_id

F3A does not accept raw source refs as authorization input. It derives source refs from canonical F2B evidence, then the digest contract revalidates those derived refs through the public F2 source-integrity validator.

## 5. Historical discoverability

Terminal Task states such as COMPLETED, FAILED and CANCELLED remain valid historical discovery metadata.

Resolved Branch states such as ADOPTED, SUPERSEDED, DISCARDED and CANCELLED remain valid historical discovery metadata.

Projection does not imply resume, reactivate, continue, control, adopt, promote or merge.

A terminal Task remains historical and discoverable only.

## 6. Rebuildability and revisions

Derived text such as summary, topics and keywords never changes source authority.

If native Task or Branch revision changes, F2 produces a different versioned source identity. F3A then builds a new immutable discovery view over that new source reference.

F3A does not invent its own lifecycle revision.

## 7. R11-E2 ownership fence

At F3A CLAIM time, Issue #31 is implementing AE-R11-E2.2 query-order indexes.

F3A must not modify:
- AgentRepository.list_iterations
- AgentRepository.list_task_branches
- SQLAlchemy Agent persistence models
- Alembic migration lineage
- query-plan or index behavior
- se/src/runtimes/agent/persistence.py

F3A performs no repository read and creates no competing Task or Branch storage authority.

Any future F3 loader/query stage requires a fresh Issue #15 <-> Issue #31 ownership audit.

## 8. CTX-F2C / ASSET landed boundary

CTX-F2C / PR #59 is LANDED on canonical main:

```text
d2db19769bb328683e78bad9c5580919dbdeae5f
```

Canonical main now contains the dormant ASSET source projection.

F3A nevertheless remains Session / Task / Branch only:
- it does not import `AssetEvidence`;
- it does not import or call `project_asset_source`;
- it does not consume `ContextSourceKind.ASSET`;
- it does not provide ASSET discovery.

The presence of the F2C ASSET projector on canonical main does not open F3/F4
ASSET discovery/access/read. Any such expansion requires a separate Issue #15
audit and, while CAS-R1 is active, a cross-check against Issue #68 authority.

## 9. Hard non-scope

F3A does not implement:
- context.resolve, context.search, context.describe or context.read
- SQL or repository reads
- SQLAlchemy, Alembic or index changes
- background indexing
- vector or embedding search
- ContextBuilder registration
- AgentRuntime wiring
- TaskAdmission or TaskFinalizer mutation
- Task resume, reactivation or control
- Branch resolution or promotion
- ASSET discovery
- transcript materialization
- R11 retention or GC
- CAS lifecycle, provider hydration or GC
- Memory
- Personalization
- Pins, scoring or dedup
- Working Set
- ContextSnapshot
- CompactContext
- DefaultChatAgent migration

## 10. Acceptance matrix

The F3A candidate must prove:
1. SESSION digest preserves canonical F2 owner and source authority.
2. TASK digest requires valid SESSION -> TASK owner and lineage.
3. BRANCH digest requires valid SESSION -> TASK -> BRANCH owner and lineage.
4. Terminal Tasks remain discoverable metadata only.
5. Resolved or discarded Branches remain discoverable metadata only.
6. Derived text changes do not rewrite source identity.
7. Source revision changes create a distinct source view.
8. Forged but fully self-consistent raw ContextSourceRef values cannot authorize a digest.
9. Canonical F2B Session/Task/Branch evidence still projects successfully.
10. Caller cannot provide a loose owner override.
11. Digest models are immutable and cannot be directly constructed around raw refs.
12. Discovery module imports no storage, runtime, SQL or Alembic authority.
13. No F4 Context Access API or ASSET projection surface appears.
14. Existing F2 behavior remains unchanged.
15. Exact-head Architecture Linux and Windows must be GREEN before semantic close.

## 11. Candidate path scope

The initial F3A slice is limited to:
- docs/context_future/CTX_F3A_DISCOVERY_PROJECTION_0853F9BF.md
- se/src/context/discovery.py
- se/tests/architecture/test_ctx_f3_discovery.py

No existing production source file is modified by the initial F3A slice.


## 12. P1 source-provenance closure

Independent audit on PR #62 exact c4108b04 identified P1-CTX-F3A-SOURCE-PROVENANCE-1:

valid ContextSourceRef integrity != canonical source provenance != authorization.

The fix keeps F3A pure and storage-free:

- public project_session_digest consumes canonical Session evidence and calls project_session_source;
- public project_task_digest consumes canonical Session + Task evidence and calls project_session_source + project_task_source;
- public project_branch_digest consumes canonical Session + Task + Branch evidence and calls all three F2B projection helpers;
- direct digest construction is factory-gated;
- forged self-consistent raw refs are rejected as authorization input;
- no SQL/repository/runtime/R11 ownership is added.

This preserves the original F2B trust boundary instead of weakening it at F3A.


## 13. Canonical-main refresh

After semantic closure of the F3A provenance P1, canonical main advanced through Tools V1 completion PR #61.

The F3A candidate was replayed onto:

```text
main@3c6ffa06d4dc8b75c9cb03f8026e3a78a978ebb3
```

The refresh preserves the same three F3A paths and absorbs no Tools, R11, CAS, F4, runtime, repository or lifecycle scope. The landed CTX F2B source identity/adapter contracts consumed by F3A were independently audited as byte-identical across the old and refreshed base.


## 14. Post-F2C canonical-main refresh

After CTX-F2C PR #59 landed, canonical main moved to:

```text
d2db19769bb328683e78bad9c5580919dbdeae5f
```

The old F3A HEAD was:

```text
7ac9f7c06ac6fd4f32711716ca2732f8f24b8b09
```

The main drift from the prior F3A base consists only of the three landed F2C
paths. None of the three F3A-owned paths exists on the new canonical main.

This refresh therefore replays the FINAL-GREEN F3A production source and
architecture-test blobs byte-for-byte onto exact `main@d2db1976`, while this
contract alone updates dependency history.

The refresh does not change F3A semantics and does not open ASSET discovery,
storage/runtime reads, retention authority, F4 access APIs, or runtime wiring.

A separately visible post-merge push Architecture run for exact
`main@d2db1976` was not available through the Issue #15 audit surface at refresh
time. That missing evidence is not represented as GREEN. The refreshed F3A
candidate must establish its own fresh exact-head Linux + Windows Architecture
evidence before any integration decision.


## 15. Post-CAS-R1-A canonical-main refresh

CAS-R1-A PR #69 landed after exact-head Architecture GREEN/GREEN,
replacement-auditor FINAL GREEN, and explicit user merge confirmation.

Canonical main is now:

```text
face48cfd63e4ed09c8db3dc9bbe54cf64b43fa1
```

The landed CAS-R1-A delta is confined to its contract/evidence paths and does
not overlap the three F3A-owned paths.

This refresh therefore replays the previously FINAL-GREEN F3A production source
and architecture-test blobs byte-for-byte onto exact `main@face48cf`. Only this
contract updates integration history.

F3A remains Session / Task / Branch discovery projection only. This refresh does
not open ASSET discovery/read, CAS lifecycle/provider hydration/GC, R11 retention,
F4 access APIs, repository/runtime reads, Working Set, Memory, or Personalization.

A separately visible post-merge push Architecture run for exact
`main@face48cf` is not available through the current commit-run/status surface
at refresh time and is not represented as GREEN. The refreshed F3A candidate
must establish fresh exact-head Linux + Windows Architecture evidence before any
merge decision.
