# AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2 — MCP Client Ownership Addendum

## Single-Event-Loop Ownership, Transactional Reload, Capability Registration Coherence, and Deterministic Shutdown

**Repository:** `boxs-51/assistant`  
**Audited GitHub baseline:** `1f6fd54a15872005a971927e8597f55c5a343f40`  
**Local working-tree note:** the Playwright/Windows Proactor ownership fix has been applied and validated locally by the user but is not yet pushed to GitHub.  
**Date:** 2026-09-20  
**Document status:** CONTRACT FREEZE / PRE-IMPLEMENTATION REVIEW  
**Implementation status:** **NO PATCH IN THIS DOCUMENT**  
**Parent roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`

---

# 0. Executive Decision

The MCP client ownership problem **does affect** `AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`.

It does **not** change the canonical domain semantics of:

```text
Task
Branch
Execution
Checkpoint
ResumeClaim
WAITING
TaskBudget
retry/fork/delegation lineage
```

However, it is a missing client-side runtime substrate required by the roadmap's existing guarantees for:

```text
R5  async/cancellation ownership
R6  remote invocation reconciliation/idempotency
R7  reconnect + capability-ready + resume
R14 real integration/fault-injection exit gates
```

The current roadmap already says:

```text
CL
= transport + local capability execution + local HITL + terminal replay
```

and R6 assumes that one remote invocation can safely execute on CL and later produce/replay one terminal outcome.

That assumption is not valid for an MCP-backed client capability while the current CL implementation:

1. creates MCP stdio resources on one event loop;
2. invokes the same `ClientSession` from another event loop/thread;
3. never closes the `AsyncExitStack`;
4. allows `/init` to create additional MCP adapter/process generations without retiring the previous generation;
5. has no deterministic MCP shutdown path;
6. mutates the local tool registry without coordinating capability re-registration with SE.

Therefore this document is a **normative addendum** to the parent roadmap rather than a separate roadmap.

No R0–R14 phase numbers are renumbered.

Recommended scheduling:

```text
Current R5-B TaskBudget work may continue.

MCP ownership hardening may be implemented in parallel with the remaining R5 work.

But the MCP ownership addendum exit gate MUST pass before R6 is considered complete,
and SHOULD pass before implementation of R6 reconciliation begins.

R7 MUST NOT rely on MCP-backed capability readiness until this addendum passes.
```

---

# 1. Evidence State

## 1.1 Playwright resource leak

The previously observed Windows warnings:

```text
BaseSubprocessTransport.__del__
_ProactorBasePipeTransport.__del__
ValueError: I/O operation on closed pipe
```

were traced to Playwright lifecycle/test isolation.

The local working tree has now passed:

```text
tools/v1/test/test_web_tool.py
12 passed
with ResourceWarning promoted to error
with PytestUnraisableExceptionWarning promoted to error
```

and:

```text
se/tests tools cl/tests
556 passed
```

with no remaining Proactor/subprocess unraisable warnings.

This closes the Playwright-specific defect.

It does **not** prove MCP stdio ownership because the default `cl/config/setting.json` at audited HEAD has no `mcp_servers` configuration and the current CL test suite does not start a real MCP stdio child process.

---

# 2. Current MCP Client Call Graph

Current startup:

```text
cl/src/main.py
    main()
      ↓
DynamicRegistry(...)
      ↓
registry.load_all()
      ↓
MCPManager.load_mcp_servers()
      ↓
MCPClientAdapter(...)
      ↓
loop.run_until_complete(adapter.connect())
      ↓
stdio_client(...)
      ↓
ClientSession(...)
      ↓
MCP child subprocess + pipes
```

Current remote MCP invocation path:

```text
SE
  ↓ capability.invoke
GatewayRealtimeClient receiver thread
  ↓
ClientCapabilityRuntime.handle_message()
  ↓
CapabilityDispatcher.dispatch()
  ↓
ThreadPoolExecutor / capability-worker-N
  ↓
LocalCapabilityExecutor.execute()
  ↓
registry MCP tool handler
  ↓
MCPClientAdapter._create_execution_handler().handler()
  ↓
asyncio.get_event_loop()
or asyncio.new_event_loop()
  ↓
loop.run_until_complete(self.session.call_tool(...))
```

The critical mismatch is:

```text
ClientSession + stdio streams were created on LOOP A

but

call_tool() can run on LOOP B created inside capability-worker-N
```

This is a direct single-event-loop ownership violation.

---

# 3. Exact Call-Site Audit

## 3.1 `cl/src/mcp_client/mcp_adapter.py`

### `MCPClientAdapter.__init__()` — baseline lines 13–17

Current:

```python
self.session: ClientSession = None
self._exit_stack = AsyncExitStack()
```

Finding:

```text
AsyncExitStack has no deterministic owner close path.
```

### `MCPClientAdapter.connect()` — baseline lines 19–49

Current resource acquisition:

```python
read_stream, write_stream = await self._exit_stack.enter_async_context(
    stdio_client(server_params)
)
self.session = await self._exit_stack.enter_async_context(
    ClientSession(read_stream, write_stream)
)
await self.session.initialize()
```

Findings:

- stdio child process belongs to the event loop executing `connect()`;
- partial initialization failure is not unwound explicitly;
- no loop-affinity assertion exists;
- no adapter close API exists.

### `_create_execution_handler()` — baseline lines 69–82

Current:

```python
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

result = loop.run_until_complete(
    self.session.call_tool(...)
)
```

Finding:

```text
P0 cross-event-loop ClientSession use.
```

The handler also creates loops that are never closed.

### `MCPManager.load_mcp_servers()` — baseline lines 90–114

Current:

```python
loop.run_until_complete(adapter.connect())
server_tools = loop.run_until_complete(adapter.get_mapped_tools())
...
self.adapters.append(adapter)
```

Findings:

- arbitrary caller thread owns MCP loop implicitly;
- `/init` may execute this from a UI worker thread;
- failed adapters may be abandoned after subprocess/session acquisition;
- repeated reload appends generations without retiring old adapters;
- manager has no `shutdown()`.

---

## 3.2 `cl/src/loader/registry.py`

### `DynamicRegistry.__init__()` — baseline line 21

```python
self.mcp_mgr = MCPManager()
```

This establishes:

```text
DynamicRegistry owns MCPManager
```

but no matching `DynamicRegistry.shutdown()` currently exists.

### `DynamicRegistry.load_all()` — baseline lines 28–44

Current MCP reload:

```python
mcp_cfg = self.settings.get("mcp_servers", {})
mcp_tools = self.mcp_mgr.load_mcp_servers(mcp_cfg)
self.tools.update(mcp_tools)
```

Finding:

```text
load_all mutates live registry incrementally and has no generation/rollback boundary.
```

### `execute_slash_command("/init")` — baseline lines 46–50

Current:

```python
if cmd == "/init":
    self.load_all()
```

Finding:

```text
each /init may create a new MCP subprocess generation without closing the old one.
```

No server capability re-registration is coordinated here.

---

## 3.3 `cl/src/ui/bridge.py`

### `UIBridge.submit_prompt()` worker — baseline lines 322–331

Current slash-command flow:

```python
if text and text.startswith("/"):
    res = self._engine.registry.execute_slash_command(text)
```

The slash command runs inside:

```text
threading.Thread(target=_worker, daemon=True)
```

Therefore `/init` may execute `MCPManager.load_mcp_servers()` on a transient UI worker thread.

This strengthens the cross-loop finding:

```text
startup MCP generation:
    may be created from main thread loop

reload MCP generation:
    may be created from arbitrary UI worker loop
```

The future implementation must not let the UI worker own MCP resources.

---

## 3.4 `cl/src/core/capability_dispatcher.py`

### `CapabilityDispatcher.__init__()` — baseline lines 39–58

Current local execution owner:

```python
self._executor = ThreadPoolExecutor(...)
```

MCP tool handlers therefore normally run on:

```text
capability-worker-N
```

not on the loop that created the MCP session.

### `dispatch()`

The dispatcher owns the lifetime of each local invocation `Future`.

It currently has no explicit reload quiesce contract.

### `shutdown()` — baseline lines 273–275

Current:

```python
self.reset_principal()
self._executor.shutdown(wait=False, cancel_futures=True)
```

Findings:

- queued work is cancelled;
- already-running worker functions may continue;
- shutdown returns before running workers drain;
- MCP transport may later be closed while a worker is still using it unless ordering is explicitly defined.

The MCP fix must therefore define a reload/shutdown barrier, not merely add `MCPClientAdapter.close()`.

---

## 3.5 `cl/src/core/local_capability_executor.py`

### `execute()` — baseline line 29 onward

Canonical path:

```text
resolve
validate
HITL
call target
validate JSON result
```

### `_call()` — baseline line 94 onward

The executor invokes the tool as a normal synchronous callable.

If it returns an awaitable, `execute()` currently uses:

```python
asyncio.run(result)
```

Contract consequence:

> MCP tools MUST NOT expose a raw async `ClientSession.call_tool()` coroutine through this boundary.

The MCP callable exposed to `LocalCapabilityExecutor` must remain a **synchronous bridge** that submits work to the dedicated MCP owner loop.

This avoids creating yet another event loop in a capability worker.

---

## 3.6 `cl/src/core/capability_runtime.py`

### `build_registration()` — baseline lines 36–122

The advertised capability set is built directly from:

```python
self.registry.tools
```

Therefore server registration is implicitly tied to a particular local registry generation, even though no generation identity is currently represented.

### `register()` — baseline line 124 onward

Current order:

```text
build payload
→ update dispatcher registration snapshot
→ send capability.register
→ wait capability.registered
```

Problem:

```text
dispatcher begins believing the new snapshot before server acknowledgement.
```

For transactional reload, this order is unsafe.

Required future order while dispatcher is quiesced:

```text
stage candidate registry
→ build candidate registration
→ send
→ wait capability.registered
→ atomically commit local candidate
→ commit dispatcher snapshot
→ resume dispatcher
```

If registration outcome is unknown, the current connection generation must not continue accepting invocations against an uncertain catalog.

---

## 3.7 `cl/src/core/client_runtime.py`

### `start()`

`ClientRuntime.start()` may call:

```python
self.registry.load_all()
```

when registry settings are empty.

The runtime therefore participates in registry bootstrap even though `cl.main` also calls `registry.load_all()` before constructing `ClientRuntime`.

Ownership is currently ambiguous.

### `_replace_realtime_generation()`

Rotates WebSocket generation and rebinds dispatcher/runtime.

MCP lifetime must **not** be tied to this connection generation.

Frozen rule:

```text
MCP process/session lifetime = client process / registry generation lifetime

NOT

WebSocket connection_id lifetime
```

A normal reconnect must not destroy an MCP process that is still executing a local capability.

### `_drain_current_generation()`

Used for auth-principal generation changes.

Principal changes are stronger than ordinary WebSocket reconnect.

Old-principal local invocations must not survive and later emit into the new principal generation.

MCP bridge cancellation must therefore observe the dispatcher's cancellation event.

### `stop()` — baseline lines 253–261

Current:

```python
self.capabilities.shutdown()
self.realtime.close()
```

Missing:

```text
explicit reload quiesce
MCP shutdown coordination
reconnect thread deterministic join
registry/MCP owner shutdown
```

This addendum freezes ownership boundaries rather than requiring `ClientRuntime` itself to own `DynamicRegistry`.

Recommended authority:

```text
Application main/bootstrap owns DynamicRegistry lifecycle.
ClientRuntime owns realtime + CapabilityDispatcher lifecycle.
DynamicRegistry owns MCPManager lifecycle.
MCPManager owns the dedicated MCP loop/thread + adapter generations.
```

Thus final application shutdown ordering must span both `ClientRuntime.stop()` and `DynamicRegistry.shutdown()`.

---

## 3.8 `cl/src/main.py`

Current:

```text
registry.load_all()
...
webview.start(debug=True)
```

with no `try/finally`.

Required application-level ownership:

```text
create registry
load registry
create ClientRuntime
run UI
finally:
    client_runtime.stop()
    registry.shutdown()
```

The application layer is the only scope that naturally owns both objects.

---

# 4. P0/P1 Findings

| ID | Severity | Finding | Current consequence |
|---|---|---|---|
| MCP-O1 | P0 | `ClientSession` created on loop A and called from loop B | cross-loop failure/hang/undefined transport behavior |
| MCP-O2 | P0 | `AsyncExitStack` has no close path | stdio child process/pipe leak |
| MCP-O3 | P0 | partial adapter initialization is not rolled back | leaked subprocess after connect/discovery failure |
| MCP-O4 | P0 | repeated `/init` appends MCP generations | deterministic child-process growth |
| MCP-O5 | P0 | reload mutates local registry without SE capability-registration transaction | client/server capability catalog divergence |
| MCP-O6 | P0 | dispatcher can still have running worker while MCP resources are retired | use-after-close / terminal-outcome race |
| MCP-O7 | P0 | `/init` runs from arbitrary UI worker thread | resource owner depends on caller thread |
| MCP-O8 | P0 for R6/R7 | no real MCP-backed remote invocation/reconnect test | R6/R7 safety unproven for MCP |
| MCP-O9 | P1 | ad-hoc event loops are never closed | loop/thread resource debt |
| MCP-O10 | P1 | app main lacks final lifecycle cleanup | interpreter/OS teardown remains implicit |
| MCP-O11 | P1 | `CapabilityDispatcher.shutdown(wait=False)` is not a deterministic drain barrier | running work may outlive stop |
| MCP-O12 | P1 | registry bootstrap owner is ambiguous between `main` and `ClientRuntime.start()` | lifecycle behavior depends on call path |

---

# 5. Contract Freeze — Ownership Graph

The target authority graph is:

```text
CL application process
│
├── DynamicRegistry
│   │
│   └── MCPManager
│       │
│       ├── exactly one MCP owner thread
│       │   └── exactly one long-lived asyncio event loop
│       │
│       └── MCP generation
│           ├── MCPClientAdapter A
│           │   ├── AsyncExitStack
│           │   ├── stdio_client
│           │   └── ClientSession
│           └── MCPClientAdapter B...
│
└── ClientRuntime
    │
    ├── GatewayRealtimeClient
    ├── CapabilityRuntime
    └── CapabilityDispatcher
        └── capability worker pool
```

Hard invariant:

```text
MCPManager is the sole owner of the MCP event loop.

All of these operations execute on that same loop:

stdio_client enter
ClientSession enter
ClientSession.initialize
ClientSession.list_tools
ClientSession.call_tool
ClientSession exit
stdio_client exit
AsyncExitStack.aclose
```

Forbidden:

```text
MCP handler -> asyncio.new_event_loop()
MCP handler -> asyncio.run()
MCP handler -> loop.run_until_complete(ClientSession...)
worker thread directly touching ClientSession
```

---

# 6. Dedicated MCP Event-Loop Contract

## 6.1 Lifetime

The MCP manager starts one owner thread/loop lazily when at least one enabled MCP server must be connected.

The loop remains alive while any MCP generation is active or being staged.

The loop is stopped only by:

```text
DynamicRegistry.shutdown()
```

or a fully failed bootstrap that has no active generation.

## 6.2 Cross-thread invocation

A synchronous MCP tool callable executes from a normal CL worker thread.

It submits the actual async MCP call to the owner loop with a thread-safe bridge equivalent to:

```text
asyncio.run_coroutine_threadsafe(...)
```

The worker may synchronously wait for the returned concurrent future.

The worker does not own or manipulate the MCP event loop.

## 6.3 Loop-affinity assertions

Every adapter async operation should assert that it executes on the manager-owned loop.

A violation must fail immediately with a stable internal error rather than creating a fallback loop.

---

# 7. MCPClientAdapter Contract

`MCPClientAdapter` becomes an owner-loop-only async resource.

Required conceptual API:

```text
async connect()
async discover_tools()
async call_tool(name, arguments)
async close()
```

Rules:

```text
connect is idempotent or rejects double connect deterministically
close is idempotent
close sets session=None
close unwinds ClientSession before stdio transport
all close paths use the same owner loop
```

Partial initialization:

```text
create temporary AsyncExitStack
enter stdio
enter session
initialize
discover

if any step fails:
    await temporary_stack.aclose()
    publish nothing

if all steps succeed:
    transfer stack to adapter/generation ownership
```

No partially connected adapter is added to the live generation.

---

# 8. MCP Tool Handler Contract

The tool callable placed in `DynamicRegistry.tools` remains synchronous.

Conceptual signature:

```text
handler(
    ...tool arguments...,
    invocation_id=None,
    connection_id=None,
    session_id=None,
    cancel_event=None,
)
```

It must not expose a coroutine to `LocalCapabilityExecutor`.

The handler submits:

```text
manager.invoke(
    generation_id,
    server_name,
    tool_name,
    arguments,
    cancel_event,
    correlation
)
```

to the manager-owned loop.

Cancellation:

```text
cancel_event set
→ cancel the run_coroutine_threadsafe future
→ owner-loop MCP call receives cancellation
→ handler terminates with stable cancellation error
```

A normal WebSocket disconnect is **not** equivalent to application cancellation.

R6 requires local work to be allowed to finish after a transport loss so its terminal outcome can be replayed.

Therefore:

```text
connection loss:
    MCP call may continue

principal reset / explicit invocation cancel / app shutdown:
    MCP call is cancelled
```

---

# 9. Transactional MCP/Registry Reload Contract

`/init` must no longer mean:

```text
mutate live dictionaries
spawn more MCP adapters
return success
```

It becomes a generation transaction.

## 9.1 Candidate phase

Build a candidate without changing live state:

```text
candidate settings
candidate local tools
candidate skills
candidate MCP generation
candidate combined tool map
```

All fallible work occurs here.

No remote invocation may see the candidate yet.

## 9.2 Quiesce barrier

Before live generation replacement:

```text
CapabilityDispatcher.quiesce_reload()
```

means:

```text
reject new capability.invoke with retryable CLIENT_RELOADING
do not silently drop frames
allow already-running invocations to finish
do not cancel side-effecting work merely for reload
```

Reload waits for current local invocations to drain.

If they do not drain before `reload_drain_timeout`:

```text
abort candidate
close candidate MCP generation
keep old generation
resume dispatcher
return RELOAD_BUSY / timeout
```

This avoids retiring an MCP adapter while a worker is still executing it.

## 9.3 Capability registration transaction

While dispatcher remains quiesced:

```text
candidate registry
→ build candidate capability.register payload
→ send capability.register
→ wait capability.registered ACK
```

Only after ACK:

```text
commit candidate DynamicRegistry generation
commit dispatcher registered-capability snapshot
retire/close old MCP generation
resume dispatcher
```

## 9.4 Registration failure

If send/ACK fails:

```text
do not publish candidate
close candidate generation
retain old local generation
```

Because the remote registration outcome may be unknown after a transport failure:

```text
the current realtime generation must be considered unsafe
```

Required recovery:

```text
close/replace WebSocket generation
reconnect
register the last committed local registry generation
only then return ClientRuntime to READY
```

Do not resume dispatcher on a catalog whose server-side registration status is uncertain.

## 9.5 Commit point

All expensive/fallible preparation must happen before capability registration.

After ACK, local commit should be an in-memory pointer/snapshot swap designed to be effectively non-failing.

This minimizes the distributed commit gap.

---

# 10. `/init` Orchestration Contract

`DynamicRegistry.execute_slash_command("/init")` is not the correct long-term owner because it cannot coordinate SE registration.

Required call-site direction:

```text
UIBridge
  ↓
ClientRuntime.reload_registry()
  ↓
CapabilityDispatcher quiesce
  ↓
DynamicRegistry.prepare_reload()
  ↓
CapabilityRuntime.register(candidate)
  ↓
DynamicRegistry.commit_reload()
  ↓
CapabilityDispatcher commit snapshot/resume
```

Recommended exact change later:

```text
cl/src/ui/bridge.py::submit_prompt()
```

special-cases `/init` and calls:

```text
self._client_runtime.reload_registry()
```

instead of directly calling `registry.execute_slash_command("/init")`.

Other informational slash commands may remain in `DynamicRegistry`.

---

# 11. Capability Registration Coherence Contract

The roadmap R7 flow requires:

```text
connection registered
→ capabilities registered
→ READY
→ resume allowed
```

For MCP reload, "capabilities registered" must mean:

```text
the SE catalog and the CL executable registry refer to the same committed generation.
```

Current `CapabilityRuntime.register()` updates the dispatcher snapshot before ACK.

Future contract:

```text
build payload from explicit registry candidate/current snapshot

send register
wait ACK

then:
    commit dispatcher registration snapshot
```

During reload the dispatcher is quiesced, preventing the ACK-to-local-commit race from accepting an invocation too early.

A `PendingResumeTicket` in R7 must never auto-resume merely because the socket is READY if the required MCP capability belongs to a candidate/retired/unregistered generation.

---

# 12. Deterministic Shutdown Contract

Application shutdown authority:

```text
cl/src/main.py
```

owns both `ClientRuntime` and `DynamicRegistry`.

Required order:

```text
1. mark ClientRuntime stopping
2. quiesce CapabilityDispatcher for terminal shutdown
3. stop/close realtime ingress so no new capability.invoke can arrive
4. signal cancellation to remaining local invocations
5. cancel/drain MCP-backed calls
6. finish CapabilityDispatcher shutdown
7. DynamicRegistry.shutdown()
8. MCPManager closes every active/staged generation on MCP owner loop
9. AsyncExitStack.aclose() drains ClientSession + stdio
10. stop MCP event loop
11. join MCP owner thread
12. verify reconnect/realtime threads are terminated
```

The implementation may split these actions between:

```text
ClientRuntime.stop()
DynamicRegistry.shutdown()
MCPManager.shutdown()
```

but the application-level ordering above is normative.

No correctness rule may depend on:

```text
sleep(...)
garbage collection
interpreter shutdown
daemon thread disappearance
Windows process teardown
```

---

# 13. `ClientRuntime.stop()` Contract

`ClientRuntime.stop()` owns:

```text
realtime generation
reconnect activity
CapabilityRuntime
CapabilityDispatcher
```

It does **not** own the shared registry object itself.

Therefore it should not directly destroy `DynamicRegistry`.

This keeps registry/MCP usable by other local application components until the application owner chooses final shutdown.

Required stop semantics:

```text
_stopping=True
_suppress_reconnect=True
dispatcher terminal quiesce
realtime.close()
dispatcher cancel/drain
reconnect thread stop/join
state=STOPPED
```

`cl.main` then calls:

```text
registry.shutdown()
```

after `ClientRuntime.stop()`.

Ordinary WebSocket reconnect and `_replace_realtime_generation()` must not shut down MCP.

---

# 14. Reload vs Shutdown Semantics

These two operations are intentionally different.

## Reload

```text
quiesce new work
drain existing work naturally
do NOT cancel side effects just to reload
stage new generation
register
swap
retire old
resume
```

## Shutdown

```text
reject new work
cancel remaining work
drain/cancel MCP futures
close all MCP generations
stop loop/thread
```

This distinction is required by R6's side-effect reconciliation semantics.

---

# 15. Relation to Parent Roadmap

## 15.1 Section 31 — Async Task Ownership

The parent rule:

> component creating a child task owns it until await/cancel+drain/transfer

must be extended for CL resource ownership:

```text
The component entering an async resource context owns that context until
deterministic exit on the same owning loop.

The component creating a long-lived event loop owns the loop and its thread
until all resources bound to that loop have been closed.
```

This is a resource-ownership extension, not a change to Agent task semantics.

## 15.2 Sections 39–44 — Remote unknown outcome and terminal replay

MCP-backed local capability execution is one concrete implementation of the "CL executes side effect" box.

R6 reconciliation is invalid if MCP invocation can:

```text
fail from cross-loop access
be killed by leaked/retired transport
outlive principal shutdown incorrectly
```

Therefore this addendum is a prerequisite to treating MCP-backed capability outcomes as reliable R6 evidence.

## 15.3 Section 54 — PendingResume Registry

Auto resume for `WAITING(CONNECTION)` must also validate:

```text
required capability is registered
AND its local MCP generation is active
AND dispatcher is not reloading/quiesced
```

## 15.4 Section 65 — Client Impact

Extend the parent roadmap's CL affected list with:

```text
DynamicRegistry
MCPManager
MCPClientAdapter
LocalCapabilityExecutor MCP bridge
application shutdown ownership
registry generation / capability registration transaction
```

## 15.5 Phase R5

This addendum strengthens the meaning of:

```text
async ownership
cancellation propagation
no orphan work
```

for client subprocess resources.

It does not change TaskBudget schema/counters/CAS.

Therefore current R5-B TaskBudget work can continue.

## 15.6 Phase R6

Before R6 can claim:

```text
one invocation
one side effect
one replayable terminal outcome
```

the real MCP-backed path must pass the same-process disconnect/reconnect tests.

R6 should add MCP-backed variants of relevant remote invocation tests.

## 15.7 Phase R7

R7 capability-ready/resume semantics must bind to the **last committed registered registry generation**, not merely the existence of a connected WebSocket.

Reload failure with unknown capability registration state must prevent resume until a clean reconnect/re-registration occurs.

## 15.8 Phase R14

Add MCP-specific fault injection:

```text
child fails during initialize
child dies during list_tools
child dies during call_tool
reload while invocation active
capability.register ACK lost during reload
shutdown during call_tool
repeated /init
Windows process exit with MCP active
```

---

# 16. What This Addendum Does NOT Change

No changes are proposed here to:

```text
TaskBudget
TaskBranch
AgentExecution schema
WAITING enum
WaitReason enum
ResumeClaim persistence
Checkpoint persistence
CapabilityInvocation durable schema
R6 idempotency classifications
ClientInvocationLedger schema
R8/R9 branching semantics
provider retry/fallback
server MCP manager
```

The SE `GatewayMcpManager` already has a materially better ownership structure:

```text
lifecycle task
→ async with stdio_client
→ async with ClientSession
→ cancel + await task during stop
```

No equivalent SE MCP P0 was found in this audit.

---

# 17. Exact Future Call-Site Plan

This is a design plan only. No patch is included.

## A. `cl/src/mcp_client/mcp_adapter.py`

Future responsibilities:

```text
MCP loop/thread owner
MCP generation representation
adapter owner-loop-only methods
thread-safe sync invocation bridge
partial-connect rollback
idempotent close
manager shutdown
```

Remove all ad-hoc:

```text
asyncio.get_event_loop()
asyncio.new_event_loop()
loop.run_until_complete()
```

from MCP invocation/load paths.

## B. `cl/src/loader/registry.py`

Introduce conceptual operations:

```text
prepare_reload()
commit_reload()
abort_reload()
shutdown()
```

`load_all()` becomes initial-generation bootstrap rather than repeated in-place mutation.

A registry candidate must expose the candidate tool view before commit so `CapabilityRuntime` can build registration from it.

## C. `cl/src/ui/bridge.py`

Change `/init` ownership from:

```text
registry.execute_slash_command("/init")
```

to:

```text
ClientRuntime.reload_registry()
```

so the reload can coordinate registry + server registration + dispatcher state.

## D. `cl/src/core/capability_dispatcher.py`

Add explicit states:

```text
ACTIVE
RELOAD_QUIESCED
SHUTTING_DOWN
CLOSED
```

Required conceptual methods:

```text
quiesce_for_reload()
wait_for_idle(timeout)
resume_after_reload()
begin_shutdown()
shutdown/drain()
```

New invocation during reload must receive a retryable stable error instead of being silently executed against a changing catalog.

## E. `cl/src/core/local_capability_executor.py`

Keep the MCP target synchronous.

No raw MCP coroutine may reach:

```python
asyncio.run(result)
```

Optional hardening:

```text
metadata execution_kind/source == MCP
→ assert target returned non-awaitable bridge result
```

## F. `cl/src/core/capability_runtime.py`

Support registration built from an explicit candidate/current tool view.

Move dispatcher snapshot commit until after `capability.registered` ACK.

Expose registration failure distinctly enough for `ClientRuntime.reload_registry()` to force connection-generation recovery when server state is uncertain.

## G. `cl/src/core/client_runtime.py`

Add reload coordinator:

```text
reload_registry()
```

It owns the transaction across:

```text
dispatcher
registry candidate
capability register
realtime recovery
READY state
```

Strengthen `stop()` as the deterministic realtime/dispatcher drain half of app shutdown.

Do not close MCP on ordinary reconnect.

## H. `cl/src/main.py`

Wrap UI lifetime in `try/finally`.

Normative final ownership:

```text
try:
    webview.start(...)
finally:
    client_runtime.stop()
    registry.shutdown()
```

---

# 18. Required Tests Before Implementation Is Accepted

## 18.1 Unit ownership tests

Must prove:

```text
connect/list/call/close all execute on same owner loop
worker threads never create an MCP event loop
MCP close is idempotent
partial connect failure closes acquired contexts
```

## 18.2 Real stdio lifecycle test

Start a real local MCP stdio server.

Exercise:

```text
connect
discover
call
close
```

On Windows run with:

```text
ResourceWarning -> error
PytestUnraisableExceptionWarning -> error
```

Expected:

```text
0 BaseSubprocessTransport.__del__
0 _ProactorBasePipeTransport.__del__
0 unclosed transport
0 surviving MCP child process
```

## 18.3 Worker-thread test

Invoke an MCP capability through real:

```text
CapabilityDispatcher
→ ThreadPoolExecutor
→ LocalCapabilityExecutor
→ MCP sync bridge
→ owner loop
```

Assert the MCP session is touched only on owner-loop thread.

## 18.4 Partial initialization tests

Inject failure at:

```text
stdio entered
session entered
initialize
list_tools
```

Every case must leave:

```text
0 published adapter
0 surviving child process
0 open AsyncExitStack
```

## 18.5 Transactional reload tests

Required:

```text
G1 active
prepare G2 succeeds
registration ACK succeeds
→ G2 committed
→ G1 closed
```

and:

```text
G1 active
prepare G2 fails
→ G1 unchanged
```

and:

```text
G1 active
G2 staged
registration ACK fails/connection lost
→ G2 discarded
→ connection regenerated
→ G1 re-registered before READY
```

## 18.6 Reload-with-active-invocation test

```text
MCP invocation active on G1
/init requested
```

Expected:

```text
dispatcher quiesces
reload waits

if invocation drains in time:
    reload proceeds

if not:
    reload aborts
    G1 remains active
```

No forced duplicate side effect.

## 18.7 Repeated `/init` test

Run `/init` repeatedly.

Expected after every successful generation swap:

```text
exactly one committed MCP generation
no monotonically increasing child process count
no leaked event loops
```

## 18.8 Shutdown test

Shutdown during active MCP call.

Expected:

```text
new dispatch rejected
MCP future cancelled/drained
AsyncExitStack closed
child process exits
owner loop stops
owner thread joins
no unraisable warnings
```

## 18.9 R6 integration test extension

Real flow:

```text
Agent/SE
→ real TCP WebSocket
→ CL CapabilityDispatcher
→ real MCP stdio tool
→ terminal result
```

Then simulate result-send loss/reconnect.

Expected:

```text
tool side effect executes once
same invocation terminal outcome replayed
```

Client-restart durability remains R6 `ClientInvocationLedger` scope and is not implemented by this addendum.

## 18.10 R7 integration test extension

After registry/MCP reload:

```text
connection READY
candidate capability registered
local generation committed
```

Only then may a waiting execution whose pending capability is MCP-backed auto-resume.

---

# 19. Addendum Exit Gate

This addendum is complete only when all are true:

```text
[ ] exactly one dedicated MCP owner loop exists per MCPManager
[ ] MCP owner loop has exactly one owner thread
[ ] no MCP ClientSession call occurs from a non-owner loop
[ ] no ad-hoc event loop is created by MCP tool handlers
[ ] every AsyncExitStack is deterministically closed
[ ] partial MCP initialization cannot leak a child process
[ ] repeated /init cannot accumulate adapter/process generations
[ ] reload is generation-based and transactional
[ ] dispatcher is quiesced during registry/catalog generation transition
[ ] reload never retires resources used by an active invocation
[ ] capability registration snapshot commits only after server ACK
[ ] uncertain reload registration forces connection-generation recovery
[ ] normal WebSocket reconnect does not destroy MCP runtime
[ ] app shutdown cancels/drains MCP work before closing MCP transport
[ ] MCP event loop stops only after adapters are closed
[ ] MCP owner thread is joined
[ ] real stdio Windows test has zero ResourceWarning/unraisable transport warning
[ ] real dispatcher→MCP worker-thread test passes
[ ] real repeated reload test shows no child-process growth
[ ] R6 has at least one MCP-backed remote invocation/reconnect test
[ ] R7 capability-ready gate recognizes committed MCP registry generation
```

---

# 20. Phase Integration Decision

Do **not** create a new numbered roadmap phase between R5 and R6.

Track this as:

```text
R5-MCP — Client MCP Resource Ownership Hardening
```

or:

```text
R5-X — CL MCP Single-Loop Ownership Prerequisite
```

inside implementation planning.

Recommended dependency:

```text
R5-A async Agent ownership        COMPLETE
R5-B TaskBudget                   may continue
remaining R5 work                 may continue
        │
        ├──────────────┐
        │              │
        ▼              ▼
  R5-MCP ownership   other R5 work
        │              │
        └──────┬───────┘
               ▼
          R5 exit gate
               ▼
              R6
               ▼
              R7
```

This preserves the parent roadmap's phase numbering and avoids coupling TaskBudget work to an unrelated CL subprocess fix.

---

# 21. Frozen Decisions

```text
D-MCP-1
MCP stdio resources have one dedicated event-loop owner.

D-MCP-2
No tool worker may directly drive ClientSession with its own event loop.

D-MCP-3
MCP tool callables exposed to LocalCapabilityExecutor remain synchronous bridges.

D-MCP-4
DynamicRegistry owns MCPManager lifetime.

D-MCP-5
Application main owns final ordering between ClientRuntime.stop and DynamicRegistry.shutdown.

D-MCP-6
Ordinary WebSocket reconnect does not restart/close MCP.

D-MCP-7
Principal reset/app shutdown may cancel MCP invocation; transport disconnect alone does not.

D-MCP-8
/init is a transactional registry generation replacement.

D-MCP-9
Reload quiesces new remote invocation and drains existing work before generation swap.

D-MCP-10
Candidate capability registration must ACK before local generation/snapshot commit.

D-MCP-11
Unknown registration outcome forces a clean connection-generation recovery.

D-MCP-12
R6/R7 are not considered fully proven for MCP-backed capabilities until real stdio E2E tests pass.

D-MCP-13
No sleep/timing workaround is accepted as resource cleanup.

D-MCP-14
No patch is authorized by this document; it is contract/audit evidence only.
```

---

# 22. Recommended Next Coding Scope — When Authorized

The first MCP implementation patch should remain narrow:

```text
1. dedicated MCP loop/thread ownership
2. adapter deterministic close + partial-failure rollback
3. sync invocation bridge
4. MCPManager shutdown
5. focused real-stdio ownership tests
```

The second patch should handle:

```text
1. transactional DynamicRegistry generation
2. dispatcher reload quiesce/drain
3. ClientRuntime.reload_registry
4. capability registration commit-after-ACK
5. /init orchestration
6. reload/shutdown integration tests
```

The R6/R7-specific MCP E2E extensions should then be added to their own phase work so this infrastructure hardening does not prematurely implement reconciliation, ClientInvocationLedger, ResumeClaim, or durable resume semantics.

---

# 23. Final Review Result

The parent `AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md` remains architecturally valid.

No change is required to:

```text
Task/Branch/Execution cardinality
WAITING semantics
TaskBudget
R6 unknown-outcome model
R7 ResumeClaim model
R8/R9 branch semantics
```

But its existing CL assumptions are incomplete without an explicit MCP resource-ownership layer.

Therefore this addendum should be treated as a prerequisite extension to:

```text
Section 31  Async Task Ownership
Section 41–44 Remote terminal outcome/idempotency
Section 54  PendingResume readiness
Section 65  Client Impact
Phase R5    ownership hardening
Phase R6    remote reconciliation evidence
Phase R7    capability-ready resume gate
Phase R14   fault injection / real E2E
```

**Final disposition:**

```text
Playwright Proactor ownership leak:
    CLOSED by local working-tree evidence

MCP client ownership:
    CONTRACT FROZEN BY THIS ADDENDUM
    IMPLEMENTATION NOT STARTED

Current R5-B TaskBudget:
    NOT BLOCKED by this addendum

R6:
    MUST NOT claim MCP-backed remote-safety completion until addendum exit gate passes

R7:
    MUST use only committed/registered local capability generation for MCP-backed resume
