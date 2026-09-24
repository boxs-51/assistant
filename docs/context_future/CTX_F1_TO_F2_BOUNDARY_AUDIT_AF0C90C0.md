# CTX-F1 -> CTX-F2 Boundary Audit / Source Identity Contract

**Issue:** #15  
**Prepared from:** `work/ctx-f0-early-f5c77e93@af0c90c07c8a24663db69a71330cf6eda7a0c413`  
**Preparation branch:** `work/ctx-f2-source-identity-af0c90c0`  
**Canonical main observed:** `f5c77e93929a3d1fc8dfbfed8a046378bb9a3894`  
**Status:** PREPARED / CONTRACT-ONLY / NO CTX-F2 PRODUCTION WIRING

## 1. Boundary decision

CTX-F2 is narrowed to:

```text
Context source identity / authority references
+
Central Asset convergence contract
+
projection-only owner/version fencing
```

It does **not** own discovery/search/read APIs yet. Those remain CTX-F3/F4.

It does **not** replay Central Asset F1-F4. Issue #47 / CAS-R0 is the only authority for
that convergence work.

It does **not** persist ContextSource rows, embeddings, chunks, indexes, or Memory.

## 2. Inherited authority separation

```text
Agent Session / Task / Branch / Execution / Checkpoint
    = Agent authority

(transcript_ref, transcript_version)
    = R11 transcript persistence identity

asset_id / asset://<asset_id>
    = Central Asset authority

tool_response_payload_id
    = CTX-F1 ToolResponsePayload authority

context_source_id
    = CTX projection identity only
```

A `context_source_id` must never become the lifecycle or mutation authority for the
source object it references.

## 3. CTX-F2 source kinds

Initial closed vocabulary:

```text
SESSION
TASK
BRANCH
AGENT_TRANSCRIPT
ASSET
TOOL_RESPONSE_PAYLOAD
```

Later source kinds require their own ownership audit.

### SESSION

Authority key:

```text
session_id
```

Owner scope comes from trusted server-side Session authority.

A new Session does not automatically reactivate historical Tasks.

### TASK

Authority key:

```text
task_id
revision
```

Task terminalization does not delete or rewrite Context history.

### BRANCH

Authority key:

```text
branch_id
revision
```

Branch resolution state is Agent authority. CTX may project it, never decide it.

### AGENT_TRANSCRIPT

Authority key:

```text
transcript_ref
transcript_version
```

This pair remains R11-owned immutable transcript persistence identity.

CTX may reference an exact transcript representation as a source, but:

```text
context_source_id != transcript_ref
Context projection != continuation checkpoint authority
```

No fallback from missing/corrupt transcript data to Session/Memory/CAS is permitted.

### ASSET

Authority key:

```text
asset_id
asset revision if/when the converged CAS contract exposes one as required projection evidence
```

CTX must use stable `asset_id`, never:

```text
object key
signed URL
local path
provider file id
provider URI
raw bytes
```

Issue #47 / CAS-R0 owns FileAsset/FileBlob/FileReference/FileProviderBinding and
physical byte lifecycle.

### TOOL_RESPONSE_PAYLOAD

Authority key:

```text
tool_response_payload_id
```

CTX-F1 remains the lifecycle/identity authority. F2 only exposes it as a context source.

## 4. ContextSourceRef

The F2 projection contract is conceptually:

```text
ContextSourceRef
    context_source_id
    source_kind
    authority_id
    authority_version?
    owner_user_id
    session_id?
    task_id?
    branch_id?
    source_created_at?
    source_state?
    metadata
```

Rules:

1. `authority_id` is the native source authority ID, opaque to CTX.
2. `authority_version` is mandatory where the source contract is explicitly versioned
   (Task/Branch revision, Agent transcript version).
3. `context_source_id` is a deterministic CTX projection identity over source kind +
   owner scope + exact authority identity/version.
4. `context_source_id` is never passed back to another subsystem as if it were that
   subsystem's primary key.
5. Owner scope must be derived from trusted server-side identity, never user/model
   supplied metadata.
6. Source state is descriptive projection only. CTX cannot mutate Task/Branch/Asset
   lifecycle state through this record.

## 5. Deterministic context_source_id

Domain separator:

```text
ctx-context-source-v1
```

Identity material:

```text
source_kind
owner_user_id
authority_id
authority_version?   # included when the authority is versioned
```

Optional session/task/branch scopes are projection metadata unless they are themselves
the native authority for the source kind.

Two different source kinds using the same textual native ID must produce different
`context_source_id` values.

Provider-local aliases are forbidden from the identity tuple.

## 6. CAS-R0 dependency

Fresh Issue #47 state observed during this audit:

```text
CAS-R0: ACTIVE
branch: work/cas-r0-convergence-f5c77e93
F5-0/F5-F8: CLOSED
historical feature/central-asset-storage-f1: reference-only
```

CAS-R0 currently owns replay/adaptation of F1-F4 and has explicit unresolved migration,
semantic replay, Session liveness and Agent-root physical-GC concerns.

Therefore CTX-F2 must not:

- import the historical CAS SQL migrations;
- create its own FileAsset/FileReference tables;
- infer asset liveness from Agent state;
- implement physical asset GC;
- reinterpret checkpoint/transcript IDs as asset identities;
- activate provider hydration.

Until CAS-R0 converges, the F2 Asset source contract is interface/identity-only.

## 7. R11 dependency

R11 remains authoritative for checkpoint/transcript reconstruction and retention.

CTX-F2 may reference exact `(transcript_ref, transcript_version)` as immutable source
evidence only.

Forbidden:

- using `context_source_id` to resume execution;
- reconstructing an Agent execution from CTX projections;
- replacing R11 retention roots;
- falling back from R11 corruption to Context/Memory/CAS data.

## 8. F2 implementation fence

Before any production code in F2:

1. CTX-F1 final exact-head CI must be GREEN.
2. F1 must remain dormant and migration-free.
3. Current main and Issue #31/#47 must be re-read.
4. F2 code must be additive and unused by current Agent execution.
5. No SQL/Alembic revision is permitted in the first F2 substage.
6. No ContextBuilder wiring.
7. No automatic discovery, search, read, ranking or Memory promotion.
8. No CAS physical storage dependency.
9. No R11 checkpoint/transcript write path.
10. Targeted tests must prove deterministic source identity and authority separation.

## 9. Proposed CTX-F2 substages

```text
CTX-F2A  ContextSourceRef contract + deterministic identity helper
CTX-F2B  source adapters for dormant in-process projection
CTX-F2C  Central Asset source adapter after CAS-R0 handoff
CTX-F2D  exit audit / handoff to CTX-F3 discovery
```

Only F2A should be considered for early implementation immediately after F1 final
freeze.

## 10. CTX-F2A exact allowed scope

Allowed:

```text
se/src/context/source_identity.py
se/tests/architecture/test_ctx_f2_source_identity.py
docs/context_future/*
```

Not allowed:

```text
se/src/runtimes/agent/**
se/src/infrastructure/storage/**
se/src/provider/**
se/src/application/assets/**
se/src/context/manager.py
Alembic migrations
ContextBuilderAdapter
Memory / profile schemas
```

F2A must be a pure domain/helper layer with no runtime registration.

## 11. F2A acceptance matrix

Required tests:

- SESSION same authority -> same context_source_id;
- TASK same id/revision -> same identity;
- TASK same id/new revision -> different identity;
- BRANCH same id/new revision -> different identity;
- AGENT_TRANSCRIPT same ref/version -> same identity;
- AGENT_TRANSCRIPT same ref/new version -> different identity;
- ASSET same asset_id -> same identity;
- TOOL_RESPONSE_PAYLOAD same payload_id -> same identity;
- same textual authority_id across different source kinds -> different identity;
- different owner_user_id -> different identity;
- empty owner/source authority -> reject;
- provider/local path/signed URL fields are not accepted as identity authority;
- model-copy/forged context_source_id is rejected if a domain model is introduced.

## 12. Exit rule

CTX-F2 is not considered open for production integration merely because F2A exists.

F2A is dormant source-identity infrastructure only.

F2B+ require a new Issue #15 checkpoint and fresh overlap audit.

CENTRAL-ASSET DEPENDENCY: now formally coordinated through active Issue #47 / CAS-R0.
