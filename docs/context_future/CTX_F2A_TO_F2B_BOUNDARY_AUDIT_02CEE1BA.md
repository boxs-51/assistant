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

Canonical authority:
```text
chat_data.Session only
NOT AgentSessionRecord
```

Required already-loaded evidence:
```text
Session.id
Session.user_id
Session.status
Session.created_at
```

Eligibility:
```text
Session.user_id is present and non-empty
owner_user_id = Session.user_id
```

Output:
```text
source_kind = SESSION
authority_id = Session.id
authority_version = None
session_id = Session.id
```

Rules:
- no caller/model-supplied owner value is accepted;
- AgentSessionRecord is a separate control-plane namespace and is not projected as CTX SESSION;
- Session projection does not reactivate historical Tasks;
- no Session mutation.

### TASK adapter

Required already-loaded evidence bundle:
```text
chat_data.Session
AgentTaskRecord
```

Required proof:
```text
task.session_id == session.id
session.user_id is present and non-empty
```

Output:
```text
source_kind = TASK
authority_id = task.id
authority_version = task.revision
owner_user_id = session.user_id
session_id = session.id
task_id = task.id
source_state = task.status
```

Rules:
- no loose owner_user_id argument exists;
- task.created_by is descriptive provenance only;
- revision is native Task authority, not CTX revision;
- terminal Task remains historical/discoverable projection only;
- adapter cannot resume/reopen/continue a Task.

### BRANCH adapter

Required already-loaded evidence bundle:
```text
chat_data.Session
AgentTaskRecord
AgentTaskBranchRecord
```

Required proof:
```text
branch.task_id == task.id
task.session_id == session.id
session.user_id is present and non-empty
```

Output:
```text
source_kind = BRANCH
authority_id = branch.branch_id
authority_version = branch.revision
owner_user_id = session.user_id
session_id = session.id
task_id = task.id
branch_id = branch.branch_id
source_state = branch.resolution_state
```

Rules:
- no loose owner_user_id argument exists;
- branch.created_by is descriptive provenance only;
- branch lifecycle/resolution remains Agent authority;
- no branch promotion/merge/resolution decision in CTX.

### AGENT_TRANSCRIPT adapter

Required already-loaded evidence bundle:
```text
chat_data.Session
AgentExecutionRecord
AgentExecutionCheckpointRecord
```

Required proof:
```text
checkpoint.execution_id == execution.id
checkpoint.session_id == execution.session_id == session.id
checkpoint.task_id == execution.task_id
checkpoint.branch_id == execution.branch_id
checkpoint.transcript_ref is present
checkpoint.transcript_version is present and >= 0
session.user_id is present and non-empty
```

Output:
```text
source_kind = AGENT_TRANSCRIPT
authority_id = checkpoint.transcript_ref
authority_version = checkpoint.transcript_version
owner_user_id = session.user_id
session_id = session.id
task_id = checkpoint.task_id
branch_id = checkpoint.branch_id
```

Rules:
- no loose ref/version/owner projection API exists;
- exact pair remains R11-owned immutable persistence identity;
- F2B does not materialize, reconstruct, validate ancestry, repair, retain, or GC transcript storage;
- missing/corrupt R11 transcript evidence fails at the R11 authority boundary;
- no fallback to Session/Memory/CAS.

### TOOL_RESPONSE_PAYLOAD adapter

Required already-loaded evidence bundle:
```text
chat_data.Session
AgentExecutionRecord
CapabilityInvocation
AgentToolResultRecord
ToolResponsePayload
```

Required proof is defined in section 11 and includes durable result commitment plus exact identity/ownership relationships.

Output:
```text
source_kind = TOOL_RESPONSE_PAYLOAD
authority_id = payload.payload_id
authority_version = None
owner_user_id = session.user_id
session_id = session.id
```

Rules:
- no loose owner_user_id argument exists;
- must re-use CTX-F1 integrity validation before projection;
- durable AgentToolResult.commit_state == COMMITTED is the commitment authority;
- self-declared payload COMMITTED is not sufficient;
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
- accept only an already-loaded authority evidence bundle or structural evidence DTO/Protocol that preserves the frozen native field semantics;
- derive `ContextSourceRef` only through `create_context_source_ref(...)`;
- never accept user/model-provided owner identity;
- never reinterpret provider-local locators as authority;
- never mutate source subsystem state;
- never import SQLAlchemy ORM/storage models into the CTX production adapter module; use structural Protocol/evidence DTO interfaces or equivalent decoupled views;
- never open transactions or repositories merely to construct the projection;
- never cache globally.

Concrete ORM records may be used by upstream loaders and architecture tests as evidence providers, but CTX projection code must remain storage-layer decoupled.

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


## 10. Pre-code blocker closure: canonical source-authority evidence

This section closes the pre-code authority ambiguity raised by independent audit.

### 10.1 Canonical CTX SESSION authority

For F2B, `ContextSourceKind.SESSION` maps **only** to the canonical chat/conversation session authority:

```text
se/src/infrastructure/storage/models/sql/chat_data/session.py
Session
  id
  user_id
  status
  created_at
```

It does **not** map to:

```text
AgentSessionRecord / agent_sessions
```

`AgentSessionRecord` is a distinct multi-agent control-plane namespace and must not be collapsed into the single F2A SESSION source kind.

Eligibility rule:

```text
Session.user_id must be present and non-empty
owner_user_id = Session.user_id
authority_id  = Session.id
```

No caller-supplied owner string may substitute for `Session.user_id`.

### 10.2 TASK evidence chain

The pure TASK adapter must consume the already-loaded authoritative pair:

```text
Chat Session
AgentTaskRecord
```

and prove:

```text
task.session_id == session.id
session.user_id is present
```

Then and only then:

```text
owner_user_id    = session.user_id
authority_id     = task.id
authority_version= task.revision
session_id       = session.id
task_id          = task.id
source_state     = task.status
```

`task.created_by` is descriptive provenance and MUST NOT substitute for owner authority.

### 10.3 BRANCH evidence chain

The pure BRANCH adapter must consume:

```text
Chat Session
AgentTaskRecord
AgentTaskBranchRecord
```

and prove:

```text
branch.task_id  == task.id
task.session_id == session.id
session.user_id is present
```

Then:

```text
owner_user_id     = session.user_id
authority_id      = branch.branch_id
authority_version = branch.revision
session_id        = session.id
task_id           = task.id
branch_id         = branch.branch_id
source_state      = branch.resolution_state
```

`branch.created_by` is never owner authority.

### 10.4 AGENT_TRANSCRIPT evidence chain

A loose `(transcript_ref, transcript_version, owner_user_id)` tuple is forbidden.

The adapter must consume already-loaded authoritative evidence:

```text
Chat Session
AgentExecutionRecord
AgentExecutionCheckpointRecord
```

and prove:

```text
checkpoint.execution_id == execution.id
checkpoint.session_id   == execution.session_id
execution.session_id    == session.id

checkpoint.task_id      == execution.task_id
checkpoint.branch_id    == execution.branch_id

checkpoint.transcript_ref     is present
checkpoint.transcript_version is present and >= 0
session.user_id                is present
```

If task_id/branch_id are absent on both sides, equality remains valid as `None == None`.
Any mismatch rejects projection.

Then:

```text
owner_user_id     = session.user_id
authority_id      = checkpoint.transcript_ref
authority_version = checkpoint.transcript_version
session_id        = session.id
task_id           = checkpoint.task_id
branch_id         = checkpoint.branch_id
```

F2B does not materialize or validate transcript content/ancestry. The evidence chain only proves that the exact R11-owned pair belongs to the same canonical execution/session lineage. R11 remains the authority for reconstruction, DUAL equivalence, ancestry, corruption taxonomy, retention and GC.

### 10.5 No repository reads inside adapters

F2B projection helpers may validate relationships among evidence objects passed to them, but they MUST NOT perform SQL/repository/network lookups.

The source-authority layer that already owns/loads those records supplies the evidence bundle. F2B only:
1. verifies exact cross-record identity relations;
2. derives owner from canonical Chat Session;
3. calls `create_context_source_ref(...)`.

This preserves:

```text
source authority / loading != CTX projection
```

## 11. Pre-code blocker closure: proven TRP commitment authority

A self-consistent `ToolResponsePayload.source_commit_state == "COMMITTED"` is not sufficient durable proof.

F2B TRP projection must consume the full already-loaded authority evidence bundle:

```text
Chat Session
AgentExecutionRecord
CapabilityInvocation
AgentToolResultRecord
ToolResponsePayload
```

Required checks before projection:

```text
session.user_id is present

execution.session_id == session.id

result.commit_state == "COMMITTED"
result.execution_id == execution.id

invocation.execution_id == execution.id
invocation.session_id   == session.id
invocation.owner_user_id is present and non-empty
invocation.owner_user_id == session.user_id

payload.source_result_id      == result.id
payload.invocation_id         == result.invocation_id
payload.execution_id          == result.execution_id
payload.tool_call_id          == result.tool_call_id
payload.logical_capability_id == result.capability_id

invocation.invocation_id == result.invocation_id
invocation.tool_call_id  == result.tool_call_id
invocation.capability_id == result.capability_id
```

If the optional payload projection fields are present, they must also agree:

```text
payload.owner_user_id is None OR payload.owner_user_id == session.user_id
payload.session_id    is None OR payload.session_id == session.id
```

The adapter must additionally run the existing CTX-F1 ToolResponsePayload integrity validator.

Only after all checks pass:

```text
owner_user_id    = session.user_id
authority_id     = payload.payload_id
authority_version= None
source_kind      = TOOL_RESPONSE_PAYLOAD
```

### Why this is required

`ToolResponsePayload` is a dormant immutable projection created under the precondition "source result already committed." Its own `source_commit_state` field does not replace R7 durable commitment authority.

Therefore:

```text
payload says COMMITTED
!=
durable AgentToolResult commitment proof
```

F2B must never upgrade a self-declared payload flag into source authority.

## 12. Revised F2B function contracts

The production candidate may expose pure functions conceptually equivalent to:

```text
project_session_source(session)

project_task_source(
    session,
    task,
)

project_branch_source(
    session,
    task,
    branch,
)

project_agent_transcript_source(
    session,
    execution,
    checkpoint,
)

project_tool_response_payload_source(
    session,
    execution,
    invocation,
    result,
    payload,
)
```

No loose `owner_user_id` argument is accepted by TASK/BRANCH/TRANSCRIPT/TRP adapters.

The SESSION adapter derives owner directly from canonical Chat Session.

## 13. Revised authority-negative acceptance matrix

Before F2B semantic freeze, tests must additionally prove:

### SESSION namespace
- Chat Session projects successfully;
- missing/empty Chat Session user_id rejects;
- AgentSessionRecord is not accepted as CTX SESSION authority.

### TASK
- task.session_id mismatch rejects;
- caller cannot substitute a different owner;
- task.created_by cannot become owner authority.

### BRANCH
- branch.task_id mismatch rejects;
- task.session_id mismatch rejects;
- branch.created_by cannot become owner authority.

### AGENT_TRANSCRIPT
- checkpoint.execution_id mismatch rejects;
- checkpoint/session vs execution/session mismatch rejects;
- checkpoint.task_id vs execution.task_id mismatch rejects;
- checkpoint.branch_id vs execution.branch_id mismatch rejects;
- missing transcript_ref/version rejects;
- no loose ref/version/owner projection function exists.

### TOOL_RESPONSE_PAYLOAD
- self-consistent payload claiming COMMITTED with no source proof is not a valid adapter input;
- durable PROVISIONAL AgentToolResult + self-declared COMMITTED payload rejects;
- source_result mismatch rejects;
- invocation mismatch rejects;
- execution mismatch rejects;
- tool_call mismatch rejects;
- capability mismatch rejects;
- invocation owner missing rejects;
- invocation owner mismatch rejects;
- payload owner/session mismatch rejects;
- proven COMMITTED source bundle produces deterministic TRP ContextSourceRef.

## 14. Updated F2B pre-code gate

```text
P1-CTX-F2B-SOURCE-AUTHORITY-1:
  CONTRACT FIXED CANDIDATE

P1-CTX-F2B-TRP-COMMIT-AUTHORITY-1:
  CONTRACT FIXED CANDIDATE

F2B production code:
  STILL CLOSED pending independent re-audit

F2C / ASSET adapter:
  CLOSED pending Issue #47 canonical content-evidence handoff
```
