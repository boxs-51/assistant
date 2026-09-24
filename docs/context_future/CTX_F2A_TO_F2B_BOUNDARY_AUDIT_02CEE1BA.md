# CTX-F2A -> CTX-F2B Boundary Audit / Dormant Source Adapter Plan

**Primary issue:** #15  
**Base:** `02cee1baf4e5ffa04678858a8c0f11051b4fa395`  
**Branch:** `work/ctx-f2b-boundary-02cee1ba`  
**Status:** PREP ONLY / DOCS ONLY / NO PRODUCTION WIRING

## 1. Entry gate

CTX-F2A is FINAL GREEN:
- all known F2A P0/P1 closed;
- exact-head Architecture #1045 and #1046 GREEN;
- F2A delta remains exactly two source-identity files.

Stacked merge ordering remains mandatory:
1. PR #45 F0/F1 lands first;
2. canonical main is reverified;
3. F2A is refreshed onto post-F1 main;
4. exact two-file F2A delta + Architecture are re-proven;
5. only then may F2A receive canonical-main merge gate.

F2B planning does not change that merge ordering.

## 2. F2B objective

F2B adds dormant, pure in-process projection adapters that translate already-trusted native authority objects into `ContextSourceRef`.

It does not discover, fetch, search, rank, persist, register, or inject context into model working state.

```text
native subsystem authority object
        ↓
trusted F2B adapter
        ↓
ContextSourceRef projection
```

The adapter may only project facts already proven by the source subsystem.

## 3. Allowed source adapters

### SESSION adapter

Input authority:
```text
server-side Session authority
session_id
trusted owner_user_id
```

Output:
```text
source_kind = SESSION
authority_id = session_id
authority_version = None
session_id = session_id
```

Rules:
- owner_user_id comes from trusted server-side identity, never caller/model metadata;
- Session projection does not reactivate historical Tasks;
- no Session mutation.

### TASK adapter

Input authority:
```text
task_id
task revision
trusted owner_user_id
optional session/branch projection IDs
descriptive task state
```

Output:
```text
source_kind = TASK
authority_id = task_id
authority_version = task revision
```

Rules:
- revision is native Task authority, not CTX revision;
- terminal Task remains historical/discoverable projection only;
- adapter cannot resume/reopen/continue a Task.

### BRANCH adapter

Input authority:
```text
branch_id
branch revision
trusted owner_user_id
optional task/session projection IDs
descriptive branch state
```

Output:
```text
source_kind = BRANCH
authority_id = branch_id
authority_version = branch revision
```

Rules:
- branch lifecycle/resolution remains Agent authority;
- no branch promotion/merge/resolution decision in CTX.

### AGENT_TRANSCRIPT adapter

Input authority:
```text
transcript_ref
transcript_version
trusted owner_user_id
optional session/task/branch projection IDs
```

Output:
```text
source_kind = AGENT_TRANSCRIPT
authority_id = transcript_ref
authority_version = transcript_version
```

Rules:
- exact pair remains R11-owned immutable persistence identity;
- F2B does not materialize, reconstruct, validate ancestry, repair, retain, or GC transcript storage;
- missing/corrupt R11 transcript evidence fails at the R11 authority boundary;
- no fallback to Session/Memory/CAS.

### TOOL_RESPONSE_PAYLOAD adapter

Input authority:
```text
validated ToolResponsePayload
tool_response_payload_id
trusted owner_user_id
optional projection IDs
```

Output:
```text
source_kind = TOOL_RESPONSE_PAYLOAD
authority_id = tool_response_payload_id
authority_version = None
```

Rules:
- must re-use CTX-F1 integrity validation before projection;
- only COMMITTED TRP is eligible;
- F2B does not persist or garbage-collect TRP;
- binary/file content remains CAS, not TRP.

## 4. ASSET remains F2C

F2B MUST NOT implement an ASSET adapter.

Reason:
```text
logical authority: asset_id
immutable content evidence: blob_id + sha256
lifecycle revision: FileAsset.revision
```

Issue #47/CAS-R0 remains active and has not handed off the canonical immutable content-evidence contract to CTX.

Therefore:
- no AssetService/SQL imports in F2B;
- no FileAsset.revision-as-content-version;
- no object key/provider ID/path/URI projection;
- no hydration;
- no asset read/open/list/delete behavior;
- no physical/logical GC assumptions.

ASSET adapter belongs to CTX-F2C after explicit CAS-R0 handoff.

## 5. Adapter purity and trust boundary

Each adapter must:
- accept an already-authoritative typed/native object or explicit trusted fields;
- derive `ContextSourceRef` only through `create_context_source_ref(...)`;
- never accept user/model-provided owner identity;
- never reinterpret provider-local locators as authority;
- never mutate source subsystem state;
- never open transactions or repositories merely to construct the projection;
- never cache globally.

Adapters are projection helpers, not source readers.

## 6. Proposed code shape

Allowed production scope for F2B candidate:

```text
se/src/context/source_adapters.py
se/tests/architecture/test_ctx_f2_source_adapters.py
docs/context_future/*
```

Potential pure functions:
```text
project_session_source(...)
project_task_source(...)
project_branch_source(...)
project_agent_transcript_source(...)
project_tool_response_payload_source(...)
```

No registry is required in F2B.

## 7. Hard non-scope

Forbidden in F2B:

```text
SQL/Alembic
storage repositories
ContextBuilder
context discovery/search/read APIs
ranking/scoring/dedup
Working Set / ContextSnapshot
Memory / Personalization
runtime registration
DefaultChatAgent migration
provider routing
Agent checkpoint/transcript writes
R11 retention/GC
CAS Asset adapter
CAS physical storage/hydration
global source registry
background indexing
```

## 8. Acceptance matrix

Required tests:

### SESSION
- trusted session -> deterministic SESSION ContextSourceRef;
- owner difference -> different source identity;
- projection metadata changes do not alter identity.

### TASK
- same task/revision/owner -> same identity;
- revision change -> different identity;
- terminal state is descriptive only.

### BRANCH
- same branch/revision/owner -> same identity;
- revision change -> different identity;
- branch state cannot alter native identity tuple.

### AGENT_TRANSCRIPT
- same transcript_ref/version/owner -> same identity;
- version change -> different identity;
- no transcript materialization/storage calls;
- malformed/noncanonical pair fails through F2A contract.

### TOOL_RESPONSE_PAYLOAD
- validated COMMITTED TRP -> deterministic TRP source;
- forged/uncommitted TRP rejected before projection;
- adapter does not carry provider aliases or file/binary locators.

### Boundary
- no ASSET adapter exists in F2B;
- no storage/runtime/provider imports;
- no registry/runtime side effects;
- all returned ContextSourceRef values pass public integrity revalidation.

## 9. F2B exit rule

F2B may be considered semantic GREEN only when:
- adapters are pure/dormant;
- owner fencing is explicit;
- no source authority is duplicated;
- no runtime registration exists;
- no ASSET projection exists;
- targeted + exact-head Architecture are GREEN;
- Issue #15 independent audit has no open P0/P1.

F2C remains CLOSED until Issue #47 explicitly hands off canonical CAS source evidence.
