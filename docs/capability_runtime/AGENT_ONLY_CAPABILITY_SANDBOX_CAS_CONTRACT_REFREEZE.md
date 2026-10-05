# Agent-only + Capability Selection + Server Ephemeral Sandbox + CAS Persistence Boundary

**Status:** Contract re-freeze candidate  
**Repository:** boxs-51/assistant  
**Audit baseline:** main@3b53969fbdfb68a47fd2a4a0262d02fc51ea02b6  
**Canonical workspace:** Issue #156  
**Governance:** Issue #85 v2.5  
**Scope:** Agent-only chat execution, dynamic capability selection, target-aware capability routing, server-side ephemeral sandboxing, file/terminal/Python execution, Web download, CAS persistence, client/server implementation coexistence, and UBQ integration.  
**Non-goal:** This document does not implement production behavior. It freezes target semantics, migration boundaries, file disposition, quality gates, and issue decomposition.

---

## 0. Current-main certification refresh — 2026-10-05

This section supersedes any older repository-status assumptions while preserving the target architecture and normative contracts below.

~~~text
canonical main = 3b53969fbdfb68a47fd2a4a0262d02fc51ea02b6
current-main Architecture #2120 / 37264857889 = GREEN/GREEN
linux-full-suite = SUCCESS
windows-client-contracts = SUCCESS

AE-R12 #107 = COMPLETE / CLOSED / CANONICAL / HEALTHY
CTX-F5-3I-B7 = LANDED / CANONICAL / HEALTHY
UBQ-3 / UBQ-4 / UBQ-5A..5E = LANDED / CANONICAL / HEALTHY
UBQ-6A = FINAL-CERTIFIED / READY candidate at PR #263; NOT YET CANONICAL
CAS-F7-T persistence + A0 activation/enrollment contract = LANDED / CANONICAL / HEALTHY
CAS first producer = UNRESOLVED / GAP-HOLD; concrete enrollment = NONE; live activation = CLOSED
CAS-B1 #166 = RESERVED / HARD HOLD / NOT YET CANONICAL
~~~

Fresh current-source audit on this main confirms:

- `GatewayChatRequest.agent_enabled` still selects DIRECT versus AGENT compatibility behavior.
- `WorkflowRuntime` still contains DIRECT execution and DIRECT fallback when no Agent is resolved or the requested Agent is missing.
- `DirectChatRuntime` remains wired in `se/src/main.py`.
- `DefaultAgentContextAssembler` still assembles the full surviving Agent capability projection; no canonical production `CapabilitySelectionContext` / `CapabilityWorkingSet` exists yet.
- no canonical production `SandboxLease` / `SandboxProfile` exists yet.
- semantic `CapabilityInvocationTarget` / `ResourceScope` target classes are not yet the production routing authority.

Dependency disposition on this baseline:

- AE-R12 closure clears the prior Agent-runtime maturity dependency but transfers no production authority into Issue #156.
- landed AE-R12 execution/recovery semantics remain compatibility/boundary evidence; any future production edit to Agent execution/recovery requires the then-current canonical Agent execution owner/workspace.
- UBQ Issue #141 continues to own durable quota/accounting. Issue #156 MUST NOT reinterpret TaskBudget/UBQ authority. AOS-2 remains blocked until UBQ-6 is canonical.
- CAS Issue #74 continues to own durable asset lifecycle. F7-T A0 activation/enrollment contract is canonical/healthy, but the first production producer remains unresolved, concrete enrollment is empty, live activation is closed, and CAS-B1 #166 remains HARD HOLD pending its upstream CAS gates and its own claim.
- CTX-B7 is path/authority-disjoint from this contract and transfers no capability-selection/routing/sandbox authority.
- client UI hardening/conversation work transfers no server routing, Agent-only, target, or sandbox authority.

Canonical child sequence is:

~~~text
#158 AOS-1  Agent resolution/default-Agent compatibility foundation
  ↓
#159 DCS-1  CapabilitySelectionContext + WorkingSet + selected-tool assembly
  ↓
#160 DCS-2  Capability Groups + lazy expansion + UBQ visibility/schema-token integration
  ↓
#161 CRT-1  CapabilityInvocationTarget + target-aware routing/fingerprint
  ↓
#162 SBX-1  Ephemeral Sandbox contracts/manager/profile
  ↓
#163 SBX-2  sandbox-bound file.* + glob
  ↓
#164 SBX-3  sandbox-bound terminal.* + python.run
  ↓
#165 WEB-DL-1 bounded web.download → sandbox
  ↓
#166 CAS-B1 sandbox/provider-download → CAS persistence bridge
  ↓
#167 AOS-2 Agent-only cutover + DIRECT physical cleanup
~~~

Gate rules:

1. this parent contract MUST be merged/canonical before any child production CLAIM;
2. every child requires a fresh exact-main PRE-CLAIM and path/dependency audit;
3. no child may silently acquire UBQ, CAS, AE recovery, CTX, client, or Tools V1 authority;
4. #158/AOS-1 is the smallest first production stage after this parent becomes canonical: default-Agent resolution + explicit unknown-Agent failure/compatibility foundation only;
5. #158 MUST NOT delete DirectChatRuntime, force all chat through AgentRuntime, implement DCS/target routing/sandbox, or alter UBQ/CAS/AE recovery;
6. #167/AOS-2 remains HARD HOLD until #158→#166 are canonical, UBQ-6 is canonical, client/request DTO overlap is freshly audited, and current-main health is GREEN.


## 1. Executive decision

The platform SHALL converge on one canonical conversational execution authority:

~~~text
GatewayChatRequest
      ↓
Agent Resolution
      ↓
AgentRuntime
      ↓
Context Assembly
      ↓
Capability Selection
      ↓
InferenceRequest
      ↓
LLM
      ↓
CapabilityRuntime when a tool is called
~~~

DirectChatRuntime SHALL be retired after compatibility migration. A simple request still uses AgentRuntime but MAY finish after one inference with an empty visible tool set.

The architecture SHALL distinguish four independent questions:

1. What may this Agent ever use? → Agent capability envelope.
2. What should the model see in this iteration? → Dynamic Capability Selection / CapabilityWorkingSet.
3. Where and against what resource does the selected logical capability execute? → CapabilityInvocationTarget + CapabilityRoutingPolicy.
4. Where does durable data live? → CAS or another explicit persistence authority, never an ephemeral server sandbox.

Canonical invariants:

~~~text
AgentDefinition.tools != InferenceRequest.tools
~~~

~~~text
SERVER HOST IS NOT AN AGENT WORKSPACE
~~~

~~~text
sandbox://  = ephemeral, non-canonical
asset://    = durable, canonical
client://   = client-owned, hard-affinity
~~~

~~~text
CLIENT_LOCAL MUST NOT silently fall back to SERVER/SANDBOX.
~~~

~~~text
Any model-directed arbitrary filesystem/process/Python execution on SERVER
MUST execute inside an isolated ephemeral sandbox.
~~~

~~~text
Durable continuation MUST NOT depend on a sandbox path or sandbox process.
~~~

---

## 2. Exact-current-main audit

### 2.1 DIRECT and AGENT are still separate execution surfaces

Current GatewayChatRequest still contains agent_enabled plus agent_id, and execution_mode maps agent_enabled=false to DIRECT and true to AGENT.

WorkflowRuntime still falls back from AGENT to DIRECT when no Agent is specified or the requested Agent is not found.

DirectChatRuntime obtains all authorized DIRECT_READ_ONLY capabilities, converts the full set to model-visible tool definitions, and reuses that tuple across direct inference turns.

**Decision:** DIRECT is compatibility-only. Target runtime authority is AgentRuntime.

### 2.2 Agent projection currently means all allowed Agent tools

Current AgentCapabilityResolver.resolve() receives only agent_id and identity.

It loops over every AgentDefinition.tools item, filters by visibility/authorization/executability, and returns all survivors. DefaultAgentContextAssembler converts all of them into InferenceToolDefinition objects. AgentRuntime then sends snapshot.tools to every inference.

**Decision:** current resolver is an authorization/envelope projection, not a relevance selector. Dynamic selection requires a contract refreeze.

### 2.3 Tool-schema tokens are omitted from current context token estimate

ContextBuilderAdapter estimates token usage from message content only. Model-visible tool names, descriptions, and JSON schemas in the same snapshot are not included.

**Decision:** UBQ admission and accounting MUST include selected tool-schema input cost.

### 2.4 Logical capability / physical implementation separation already exists

CapabilityCatalog and CapabilityImplementation already support one logical capability_id with multiple SERVER, CLIENT, MCP, or DECLARATIVE implementations.

**Decision:** KEEP this separation. The LLM should see logical capabilities, not physical implementation variants.

### 2.5 CapabilityRoutingPolicy currently has topology but no resource semantics

Current CapabilityRequestContext carries owner_id, connection_id, and scopes.

CapabilityRoutingPolicy currently prioritizes same-connection CLIENT candidates, excludes foreign clients, then leaves non-client implementations as possible candidates.

**Decision:** connection affinity is not enough. Resource identity must be explicit before implementation routing.

### 2.6 file.* is bounded but not sandbox-root confined

tools/v1/file_tool.py already contains useful per-call limits for path length, file count, file bytes, write bytes, query count, regex/match limits, and output bounds.

However path canonicalization resolves arbitrary host paths and does not prove containment under an execution-owned sandbox root.

**Decision:** KEEP bounded file-operation behavior; MIGRATE server execution to sandbox-root semantics.

### 2.7 terminal.* is bounded but host-bound

tools/v1/terminal_tool.py already has timeout, output limits, process identity tracking, process-tree observation, and cleanup behavior.

But it uses subprocess on the host, and absent cwd resolves to Path.cwd(). A caller-supplied cwd may resolve to any existing host directory.

**Decision:** KEEP lifecycle/bounds; MIGRATE server terminal execution behind an ephemeral sandbox driver. No ambient host cwd.

### 2.8 No canonical python.run exists

PythonCapabilityDriver executes registered Python callables; it is not arbitrary model-provided Python execution.

**Decision:** KEEP PythonCapabilityDriver as a trusted callable driver. If model-directed Python is required, ADD an explicit python.run logical capability backed by the sandbox runtime.

### 2.9 Web download is intentionally absent

Current Web Tool exposes web.search, web.search_many, web.read, and web.read_many. Browser contexts disable downloads, and canonical web.read rejects unsupported binary/media content.

**Decision:** do not overload web.read. ADD a separate bounded web.download capability.

### 2.10 CAS already owns durable user file lifecycle

AssetService already provides bounded ingest_stream(), durable object storage, owner checks, staging/finalization, hashes, sizes, and canonical asset:// URIs.

**Decision:** KEEP CAS as durable authority. Sandbox storage MUST NOT become a second durable object store.

---

## 3. Canonical architecture

~~~text
GatewayChatRequest
        ↓
Agent Resolver
  explicit agent_id
  or configured default Agent
        ↓
AgentRuntime
        ↓
Agent capability envelope
  = maximum tools the Agent may use
        ↓
Availability + Authorization
        ↓
Dynamic Capability Selection
  rules / semantic router / planner fallback
        ↓
CapabilityWorkingSet
  active groups
  visible logical tools
  lazy expansion
        ↓
UserResourceBudget admission
        ↓
InferenceRequest.tools
  = minimum selected model-visible set
        ↓
LLM
        ↓
logical tool call
        ↓
CapabilityInvocationTarget
        ↓
CapabilityRoutingPolicy
        ↓
physical implementation
   SERVER / CLIENT / MCP / DECLARATIVE
        ↓
execution boundary
   trusted process / ephemeral sandbox /
   remote endpoint / declarative
        ↓
ToolResult
        ↓
explicit persistence if needed
        ↓
CAS asset://
~~~

---

## 4. Agent-only execution contract

### 4.1 Single conversational authority

All online server conversational requests SHALL execute through AgentRuntime.

Missing explicit agent_id SHALL resolve to a configured canonical default Agent.

Unknown explicit agent_id SHALL fail with a clear Agent-not-found result. It SHALL NOT silently execute through DIRECT.

### 4.2 Fast path

Agent-only does not imply a multi-iteration loop.

For a request requiring no tools:

~~~text
AgentRuntime
→ context assembly
→ selected tools = []
→ one inference
→ final response
~~~

### 4.3 AgentDefinition.tools semantics

AgentDefinition.tools SHALL mean:

> maximum logical capability envelope this Agent is allowed to use.

It SHALL NOT mean:

> exact tools inserted into every inference request.

Canonical relationship:

~~~text
InferenceRequest.tools
⊆ selected eligible tools
⊆ AgentDefinition.tools
~~~

---

## 5. Dynamic Capability Selection contract

### 5.1 Selection is distinct from authorization and routing

Selection answers:

> Which logical capabilities should the model see now?

Authorization answers:

> Which logical capabilities may this identity/Agent use?

Routing answers:

> Which physical implementation should execute a selected logical capability against the intended resource?

These responsibilities MUST remain separate.

### 5.2 CapabilitySelectionContext

The selector SHALL receive enough information to reason about the current iteration:

~~~text
CapabilitySelectionContext
  owner_user_id
  agent_id
  execution_id
  iteration
  current messages / intent summary
  prior tool results
  Agent maximum capability envelope
  authorized + available capabilities
  active CapabilityWorkingSet
  connection/stable-client context
  semantic resource targets if already resolved
  applicable UserResourceBudget visibility constraints
~~~

### 5.3 CapabilityWorkingSet

The execution SHALL maintain a logical working set across iterations:

~~~text
CapabilityWorkingSet
  active_groups
  visible_capability_ids
  reason/provenance
  revision
~~~

The working set MAY expand lazily when new task needs are established.

The model SHALL NOT need to receive every registered implementation or every Agent-allowed capability.

### 5.4 Grouping

Recommended logical groups:

~~~text
filesystem.read
  file.read
  file.search
  glob.find

filesystem.write
  file.write
  file.append
  file.replace

compute
  terminal.run
  terminal.launch
  python.run

web.read
  web.search
  web.search_many
  web.read
  web.read_many

web.download
  web.download

asset.persistence
  asset.persist
  asset.metadata
~~~

Groups are selection metadata, not authorization grants.

### 5.5 UBQ integration

The selected tool set SHALL participate in UBQ admission.

Future policy MAY include:

~~~text
max_visible_tools
max_active_groups
selected_tool_schema_input_tokens
~~~

These are separate from total/per-capability tool-call quota already owned by UBQ.

---

## 6. CapabilityInvocationTarget contract

A logical tool call MUST carry stable semantic resource intent separately from transient physical routing.

Conceptual contract:

~~~text
CapabilityInvocationTarget
  resource_scope:
    SANDBOX
    CLIENT_LOCAL
    ASSET
    EXTERNAL
    INTERNAL_TRUSTED

  resource_ref optional
  owner_user_id
  stable_client_id optional
  connection_id optional/transient
  sandbox_lease_id optional
  fallback_policy:
    NONE
    SEMANTICALLY_EQUIVALENT_ONLY
~~~

A transient connection_id is not sufficient semantic identity.

### 6.1 Canonical resource families

~~~text
sandbox://<lease>/<relative-path>   ephemeral
asset://<asset-id>                  durable
client://<stable-client>/<path>     client-owned
~~~

The exact URI grammar may be finalized during implementation, but resource scope MUST be explicit.

### 6.2 Semantic fingerprint

Current capability request fingerprinting uses capability_id, capability_version, and arguments.

Target scope/identity MUST also participate when it changes the meaning of the request.

For example:

~~~text
file.read + CLIENT_LOCAL + stable_client=A
~~~

is not semantically the same request as:

~~~text
file.read + SANDBOX
~~~

A new transient connection generation SHOULD NOT by itself change stable semantic identity when R6/R7 continuation permits reconnect for the same client installation.

---

## 7. CapabilityRoutingPolicy re-freeze

### 7.1 Responsibility

CapabilityRoutingPolicy SHALL select a physical implementation only after:

1. the logical capability has been selected;
2. authorization has passed;
3. the semantic resource target is known.

It SHALL NOT decide task relevance.

It SHALL NOT reinterpret the requested resource merely to find an available implementation.

### 7.2 Candidate filtering order

Recommended order:

~~~text
logical capability_id
  ↓
routable implementations
  ↓
authorization/scopes
  ↓
resource-scope compatibility
  ↓
owner compatibility
  ↓
stable-client compatibility
  ↓
connection availability / hard affinity
  ↓
execution-boundary compatibility
  ↓
explicit semantic-equivalence fallback
  ↓
selected implementation
~~~

### 7.3 CLIENT_LOCAL

For CLIENT_LOCAL:

~~~text
same user
AND same stable client installation
AND active compatible connection
AND capability registered on that client
~~~

are required.

If unavailable:

~~~text
WAITING_FOR_CONNECTION
or CAPABILITY_TARGET_UNAVAILABLE
~~~

according to Agent continuation policy.

A SERVER implementation SHALL NOT be selected as fallback simply because it shares capability_id.

### 7.4 SANDBOX

For SANDBOX:

only an implementation explicitly capable of isolated sandbox execution may execute.

A raw trusted-process SERVER implementation SHALL NOT be selected for arbitrary model-controlled file/process/Python execution.

### 7.5 Equivalent fallback

Cross-location fallback is allowed only when explicitly frozen as semantically equivalent for the same resource scope.

A possible allowed case:

~~~text
web.search CLIENT implementation
→ unavailable
→ web.search SERVER implementation
~~~

if both implement the same external-Web semantics and policy authorizes fallback.

Forbidden case:

~~~text
client://desktop/report.txt
→ client unavailable
→ server filesystem file.read
~~~

---

## 8. Server execution boundary

### 8.1 Physical location and isolation are separate dimensions

KEEP CapabilityExecutionLocation as topology:

~~~text
SERVER
CLIENT
MCP
DECLARATIVE
~~~

Do not reinterpret WORKSPACE as a physical location.

ADD an execution-boundary dimension, conceptually:

~~~text
CapabilityExecutionBoundary
  TRUSTED_PROCESS
  EPHEMERAL_SANDBOX
  REMOTE_ENDPOINT
  DECLARATIVE
~~~

Therefore a SERVER implementation may be:

~~~text
SERVER + TRUSTED_PROCESS
~~~

or:

~~~text
SERVER + EPHEMERAL_SANDBOX
~~~

### 8.2 Trusted server process

TRUSTED_PROCESS is allowed only for bounded internal/control-plane capabilities that do not expose arbitrary model-controlled host paths, commands, or code.

Examples may include:

~~~text
skill.load
CAS application services
bounded internal policy/metadata operations
existing bounded Web read/search service under its network policy
~~~

### 8.3 Mandatory sandbox class

The following model-directed server work SHALL use EPHEMERAL_SANDBOX:

~~~text
file.* against temporary server data
glob.find against temporary server data
terminal.run
terminal.launch
python.run
archive extraction
format conversion
model-directed build/test commands
binary download materialization before persistence
~~~

---

## 9. Sandbox contract

### 9.1 Lease ownership

Sandbox authority SHALL be execution/operation scoped, not a permanent per-user filesystem.

Conceptual model:

~~~text
SandboxLease
  sandbox_id
  execution_id
  owner_user_id
  profile_id
  state
  created_at
  expires_at optional
~~~

### 9.2 Lifecycle

~~~text
CREATED
  ↓
ACTIVE
  ↓
QUIESCING
  ↓
CLEANUP
  ↓
DESTROYED
~~~

Durable Agent state MUST assume the sandbox may disappear after interruption/restart unless a later explicit lease-recovery contract proves otherwise.

### 9.3 SandboxProfile minimum dimensions

~~~text
filesystem.max_bytes
filesystem.max_files
filesystem.root
filesystem.symlink_policy

process.max_processes
process.max_memory_bytes
process.cpu_quota
process.allowed_interpreters
process.environment_allowlist

io.max_stdout_bytes
io.max_stderr_bytes
io.max_total_output_bytes

network.mode = NONE | RESTRICTED_EGRESS | EGRESS
network.allowed_hosts optional

secrets = NONE by default
~~~

SandboxPolicy, timeout/deadline policy, and UserResourceBudget are separate authorities.

### 9.4 Host filesystem invariant

A model-directed sandboxed server tool MUST NOT receive ambient authority over:

~~~text
repository checkout by ambient cwd
server source tree
server config
system temp outside its lease
user/service-account home
secrets/config mounts
arbitrary absolute path
~~~

---

## 10. file.* re-freeze

### 10.1 Logical IDs

KEEP logical IDs:

~~~text
file.read
file.search
file.write
file.append
file.replace
glob.find
~~~

### 10.2 Server semantics

On SERVER these capabilities SHALL operate only within the current sandbox lease or an explicitly materialized sandbox resource.

Server path resolution SHALL be:

~~~text
sandbox_root / relative_path
→ canonicalize/realpath
→ verify descendant of sandbox_root
→ execute
~~~

Reject:

~~~text
../ escape
absolute host path
symlink escape
junction/reparse escape where applicable
invalid/NUL path
~~~

### 10.3 Client semantics

CLIENT implementations may keep client-local workspace semantics under client policy, but invocation target SHALL identify CLIENT_LOCAL and stable-client affinity.

### 10.4 Existing limits

KEEP current File Tool operation-level limits. They complement but do not replace sandbox quotas or UBQ.

---

## 11. terminal.* re-freeze

KEEP logical IDs:

~~~text
terminal.run
terminal.launch
~~~

KEEP useful current behavior:

- bounded timeout;
- stdout/stderr and aggregate output limits;
- process identity tracking;
- process-tree cleanup;
- cancellation/termination behavior.

MIGRATE execution semantics:

- no Path.cwd() authority;
- default cwd = sandbox root;
- supplied cwd must remain inside sandbox;
- process environment is explicit/allowlisted;
- process count/memory/CPU are bounded by sandbox profile;
- terminal.launch process lifetime cannot escape SandboxLease lifetime unless a later explicit durable-job capability is frozen.

terminal.launch is not a durable background job system.

---

## 12. Python re-freeze

KEEP PythonCapabilityDriver as a trusted driver for registered Python callables.

DO NOT reinterpret it as arbitrary code execution.

ADD if required:

~~~text
python.run
~~~

python.run SHALL:

- execute under EPHEMERAL_SANDBOX;
- use an allowed interpreter/runtime;
- inherit no ambient host secrets;
- default to network NONE unless explicitly permitted;
- obey sandbox memory/process/disk/output limits;
- return structured output/artifact references;
- treat produced files as sandbox data until explicitly persisted.

---

## 13. Web download re-freeze

KEEP current web.search/read behavior and current network safety policy.

Do not broaden web.read into a binary downloader.

ADD separately:

~~~text
web.download
~~~

web.download SHALL:

1. apply the existing URL/DNS/redirect/private-network safety family;
2. enforce a hard streamed byte limit;
3. enforce timeout/cancellation;
4. validate MIME/filename metadata;
5. materialize only into sandbox-controlled storage or stream directly into an explicit persistence bridge;
6. never accept arbitrary host output paths.

A download is not durable merely because bytes reached server disk.

---

## 14. CAS persistence boundary

CAS remains the canonical durable user-owned file authority.

Canonical rule:

~~~text
sandbox:// = temporary computation state
asset://   = durable persisted state
~~~

### 14.1 Explicit persistence

A file becomes durable only by crossing an explicit application boundary, conceptually:

~~~text
sandbox://S1/result.csv
  ↓
sandbox-to-CAS bridge / asset.persist
  ↓
AssetService.ingest_stream()
  ↓
asset://A1
~~~

### 14.2 Continuation

Durable checkpoints/transcripts SHALL NOT depend on:

~~~text
/workspace/result.csv
sandbox://expired-lease/...
PID/process handle
~~~

If later continuation needs the bytes, persist them first and retain asset:// identity.

### 14.3 Provider files

Provider-native file download is a separate path. Agent-facing materialization SHOULD stream large provider bytes to sandbox/CAS rather than embedding raw bytes into transcript/checkpoint state.

---

## 15. UserResourceBudget, SandboxPolicy, and TimeoutPolicy

These authorities SHALL remain separate.

~~~text
UserResourceBudget
  compute units
  inference calls
  input/output/total tokens
  total tool calls
  per-capability tool calls
  optional cost/storage-transfer dimensions

SandboxPolicy
  filesystem bytes/files
  memory
  CPU
  process count
  output bytes
  network
  interpreter/runtime
  secret exposure

TimeoutPolicy
  execution deadline
  iteration timeout
  inference timeout
  tool timeout
~~~

Budget answers “how much may this user consume?”

Sandbox answers “what may this execution touch and how large may one isolated execution become?”

Timeout answers “how long may this operation remain active?”

---

## 16. File disposition

### KEEP

| Path / surface | Decision |
|---|---|
| se/src/runtimes/agent/contracts/inference.py | KEEP provider-neutral InferenceRequest/ToolDefinition contract; receives final selected tools. |
| se/src/runtimes/agent/adapters/inference.py | KEEP serializer-only responsibility. Do not put capability selection here. |
| se/src/runtimes/capability/catalog.py | KEEP logical-definition / multi-implementation catalog authority. |
| se/src/runtimes/capability/drivers/python_driver.py | KEEP trusted registered-callable driver. |
| se/src/runtimes/capability/drivers/remote_client_driver.py | KEEP remote-client execution boundary. |
| se/src/application/assets/service.py | KEEP canonical durable asset lifecycle authority. |
| se/src/application/assets/contracts.py | KEEP asset:// descriptor/content semantics. |
| object-storage interfaces/drivers and CAS repositories | KEEP durable storage authority. |
| tools/v1/web_tool/network_policy.py and bounded Web safety logic | KEEP. |
| tools/v1/file_tool.py bounded operation logic | KEEP algorithms/limits, but server path authority migrates. |
| tools/v1/terminal_tool.py timeout/output/process-tree logic | KEEP algorithms/limits, but server execution authority migrates. |

### MIGRATE

| Path / surface | Required migration |
|---|---|
| se/src/runtimes/agent/contracts/context_assembly.py | Expand selection contract beyond agent_id + identity. |
| se/src/runtimes/agent/capabilities.py | Separate maximum eligible envelope from per-iteration selected visibility. |
| se/src/runtimes/agent/assembly.py | Assemble selected working set only. |
| se/src/runtimes/agent/adapters/context.py | Carry selection state and include tool-schema token estimate. |
| se/src/runtimes/agent/runtime.py | Carry CapabilityWorkingSet/target state through iterations/checkpoints without persisting ephemeral sandbox authority. |
| se/src/domain/schemas/agent.py | Clarify AgentDefinition.tools = maximum envelope. |
| se/src/runtimes/capability/contracts/implementation.py | Add execution-boundary metadata/contract without conflating topology with sandbox. |
| se/src/runtimes/capability/policy.py | Add target/resource-aware routing and remove DIRECT-specific visibility semantics. |
| se/src/runtimes/capability/runtime.py | Accept/propagate stable CapabilityInvocationTarget and sandbox execution context. |
| se/src/runtimes/capability/fingerprint.py | Include stable semantic target fields when target changes request meaning. |
| se/src/runtimes/capability/local_tool_loader.py | Register model-controlled server file/process tools as sandbox-bound implementations rather than raw host-process authority. |
| tools/v1/file_tool.py | Server path resolution becomes sandbox-root constrained. |
| tools/v1/terminal_tool.py | Server cwd/process execution becomes sandbox-bound. |
| tools/v1/find_by_glob.py | Server root becomes sandbox-bound. |
| tools/v1/web_tool/* | Add separate bounded download path; keep search/read behavior. |
| se/src/domain/schemas/request.py | Migrate agent_enabled/DIRECT-vs-AGENT public semantics. |
| se/src/runtimes/workflow/runtime.py | Default Agent resolution; remove DIRECT fallback after compatibility stage. |
| se/src/main.py / se/src/application/container.py | Add selector/sandbox composition; remove direct runtime wiring at cleanup stage. |
| client request/UI fields using agent_enabled | Migrate with compatibility window. |
| tests freezing all-agent-tools visibility | Rewrite to selected-working-set semantics. |
| DIRECT execution-profile tests | Rewrite to Agent-only/default-Agent semantics. |

### SUPERSEDE

| Surface | Superseding authority |
|---|---|
| DIRECT-vs-AGENT execution-mode design | Agent-only execution contract in this document. |
| “all surviving agent.tools are assembled into every InferenceRequest” regression assumption | Dynamic Capability Selection + CapabilityWorkingSet. |
| same-connection-client-first routing as sufficient semantics | CapabilityInvocationTarget + resource-aware routing. |
| arbitrary server-local file path semantics for model-controlled File Tool | server ephemeral sandbox path authority. |
| ambient host cwd for model-controlled terminal execution | sandbox-root cwd. |

### DELETE after compatibility gates

| Surface | Delete condition |
|---|---|
| se/src/runtimes/chat/direct.py | No production caller; Agent-only tests green. |
| DirectChatRuntime exports/wiring | Same gate. |
| ApplicationContainer.direct_chat_runtime | Same gate. |
| DIRECT fallback branches in WorkflowRuntime | Default Agent semantics active. |
| CapabilityAccessProfile.DIRECT_READ_ONLY | No DIRECT consumer remains. READ-only remains an effect/policy concept. |
| client UI “Use server Agent” / DIRECT toggle semantics | client/server migration released together. |

---

## 17. New production components to add

Recommended boundaries:

~~~text
se/src/runtimes/agent/contracts/capability_selection.py
  CapabilitySelectionContext
  CapabilitySelectionResult
  CapabilityWorkingSet
  CapabilityGroup

se/src/runtimes/agent/capability_selection.py
  CapabilitySelectionPolicy / resolver

se/src/runtimes/capability/contracts/target.py
  CapabilityInvocationTarget
  ResourceScope
  FallbackPolicy

se/src/runtimes/sandbox/contracts.py
  SandboxLease
  SandboxProfile
  SandboxState

se/src/runtimes/sandbox/manager.py
  acquire / materialize / destroy

se/src/runtimes/sandbox/driver.py
  bounded file/command/Python execution bridge

se/src/application/assets/sandbox_bridge.py
  sandbox:// → AssetService.ingest_stream

tools/v1/web_tool/downloader.py
  bounded binary download stream using existing NetworkPolicy
~~~

Do not place selection inside ProviderInferenceAdapter.

Do not place sandbox persistence ownership inside CapabilityCatalog.

Do not make sandbox storage a CAS repository.

---

## 18. Migration sequence and quality gates

### A. Contract freeze

Freeze:

- Agent-only target;
- AgentDefinition.tools semantics;
- CapabilitySelectionContext;
- CapabilityInvocationTarget;
- execution-boundary dimension;
- sandbox/CAS boundary;
- compatibility behavior.

### B. Capability Selection foundation

Implement:

- CapabilityWorkingSet;
- selected-tool context assembly;
- zero-tool Agent fast path;
- tool-schema token estimation;
- visibility constraints compatible with UBQ.

Required gates:

~~~text
simple request → tools=[]
Agent envelope may contain N tools while inference exposes only selected subset
tool outside selected/allowed set cannot execute
~~~

### C. Target-aware routing

Implement:

- resource scope;
- stable client identity;
- target-aware routing;
- semantic fingerprint update;
- explicit fallback policy.

Required gates:

~~~text
CLIENT_LOCAL never falls back to server
foreign client remains excluded
same stable client may reconnect according to R6/R7
~~~

### D. Server sandbox foundation

Implement:

- SandboxProfile;
- SandboxLease;
- sandbox manager;
- filesystem containment;
- process isolation and cleanup;
- default-deny host/environment/network boundaries.

### E. File / Terminal / Python migration

Migrate:

~~~text
file.*
glob.find
terminal.*
python.run
~~~

Required gate:

~~~text
all arbitrary model-directed server local operations run only in sandbox
~~~

### F. Web download + CAS bridge

Implement:

~~~text
web.download
sandbox materialization
sandbox/provider-download → CAS persistence bridge
~~~

### G. Agent-only cutover

All online chat uses AgentRuntime.

### H. Compatibility cleanup

Delete DIRECT runtime, DIRECT visibility profile, old client toggle semantics, and obsolete tests/docs authority.

---

## 19. Required test matrix

### Agent-only

- no-tool request → one inference, tools=[];
- default Agent resolution;
- unknown explicit Agent → clear error;
- no DIRECT fallback.

### Capability selection

- large Agent envelope with zero/small visible subset;
- selection cannot exceed Agent envelope;
- lazy expansion;
- visibility/token constraints;
- tool-schema tokens included in admission estimate.

### Routing

- same logical capability with CLIENT + SERVER implementations;
- CLIENT_LOCAL routes only to required stable client;
- disconnect → WAITING/TARGET_UNAVAILABLE;
- no client-filesystem fallback to server;
- explicit semantically-equivalent fallback only where declared.

### Sandbox filesystem

- relative path succeeds;
- ../ escape denied;
- absolute host path denied;
- symlink/junction escape denied;
- host repository untouched;
- destroyed lease invalidates sandbox resources.

### Terminal/Python

- cwd defaults to sandbox root;
- timeout/cancel destroys process tree;
- stdout/stderr bounded;
- memory/process/disk quotas enforced;
- no inherited host secrets;
- network default deny;
- result files remain ephemeral until persisted.

### Web download

- private/loopback/link-local targets denied;
- redirects revalidated;
- max bytes enforced during stream;
- no host download directory selection;
- sandbox artifact produced;
- cancellation/failure cleans partial state.

### CAS / continuation

- checkpoint does not require sandbox path;
- durable output resumes from asset://;
- sandbox loss does not corrupt durable Agent state;
- owner mismatch denied.

---

## 20. Cross-track ownership

This parent contract MUST NOT silently seize authority from active tracks.

- UBQ Issue #141 and active UBQ stages own durable user quota/accounting.
- CAS Issue #74 owns canonical durable asset lifecycle.
- Landed AE-R12 #107 semantics remain durable Agent execution/recovery boundary evidence; future production changes require the then-current canonical Agent execution owner/workspace and do not reopen #107 implicitly.
- Tools V1 owns current logical tool IDs and bounded tool-level behavior until explicit migration stages are claimed.
- Policy #85 v2.5 remains merge/integration governance.

Implementation issues SHALL claim exact-main baselines and declare overlap before production edits.

---

## 21. Recommended implementation issue decomposition

Recommended independent stages:

~~~text
#158 AOS-1   Agent resolution/default-Agent compatibility foundation
#159 DCS-1   CapabilitySelectionContext + WorkingSet + selected-tool assembly
#160 DCS-2   Capability Groups + lazy expansion + UBQ visibility/schema-token integration
#161 CRT-1   CapabilityInvocationTarget + target-aware routing + fingerprint migration
#162 SBX-1   Ephemeral Sandbox contracts/manager/profile
#163 SBX-2   file.* + glob server sandbox migration
#164 SBX-3   terminal.* + python.run sandbox migration
#165 WEB-DL-1 bounded web.download
#166 CAS-B1  sandbox/provider download → CAS persistence bridge
#167 AOS-2   DIRECT physical deletion + client/UI compatibility cleanup
~~~

These labels are decomposition names inside Issue #156 until/unless repository namespace governance explicitly allocates them as global roadmap namespaces.

---

## 22. Target example

User request:

> Download a CSV, run Python analysis, and keep the result.

Target execution:

~~~text
AgentRuntime
  ↓
Capability Selection
  visible:
    web.download
    python.run
    asset.persist
  ↓
web.download
  target = SANDBOX
  ↓
sandbox://S1/input.csv
  ↓
python.run
  sandbox = S1
  ↓
sandbox://S1/result.csv
  ↓
asset.persist
  ↓
AssetService.ingest_stream
  ↓
asset://A1
  ↓
checkpoint/final response stores asset://A1
  ↓
Sandbox S1 destroyed
~~~

At no point does the model receive authority over an arbitrary server-host path, and the durable result survives sandbox cleanup/restart because persistence crossed the CAS boundary explicitly.

---

## 23. Acceptance statement

This contract is ready to act as the parent architectural freeze when the following statement is accepted as canonical:

> All conversational work executes through AgentRuntime; the model sees only the minimum selected logical capabilities required for the current iteration; arbitrary model-directed local compute on the server executes inside an isolated ephemeral sandbox; semantic resource targets prevent unsafe client/server fallback; and any file that must survive the sandbox crosses an explicit persistence boundary into CAS.
