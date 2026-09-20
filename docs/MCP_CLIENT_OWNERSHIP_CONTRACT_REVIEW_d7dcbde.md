# MCP Client Ownership Contract Review — Senior Architecture Review

**Repository:** `boxs-51/assistant`  
**Audited GitHub HEAD:** `d7dcbde5177fad17274b6049d80052bbb2e17b17` (`cap nhat R5 B1->B4`)  
**Date:** 2026-09-20  
**Status:** REVIEWED / CONTRACT CLEAN FOR V1 IMPLEMENTATION  
**Authorized V1 scope:** dedicated MCP loop/thread + per-adapter lifecycle task + deterministic close + cancellation handoff + real stdio tests  
**Explicitly excluded:** transactional `/init`, registry generation transaction, dispatcher quiesce implementation, capability-registration generation coherence, and any R1→R14 roadmap edit

---

## 1. Roadmap isolation decision

`docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md` remains frozen.

This review does **not**:

- renumber R1→R14;
- add a new prerequisite to any R phase;
- change any R1→R14 exit gate;
- change Task / Branch / Execution / WAITING / ResumeClaim semantics;
- change R5-B/C/D/E TaskBudget work;
- change R6/R7 protocol contracts.

The earlier MCP addendum language that made MCP ownership a mandatory prerequisite for R6/R7 is superseded by this review. MCP ownership is now an orthogonal CL runtime-hardening track. Possible deeper integration is recorded only in the separate `POST_R14_MCP_CLIENT_INTEGRATION_NOTE.md`.

---

## 2. Baseline findings still valid

At HEAD `d7dcbde5`, `cl/src/mcp_client/mcp_adapter.py` still:

1. enters `stdio_client()` through an `AsyncExitStack`;
2. enters `ClientSession` through the same stack;
3. has no deterministic close path;
4. creates/gets an event loop inside each synchronous MCP tool handler;
5. calls `self.session.call_tool()` from that caller loop;
6. creates ad-hoc event loops that are never closed;
7. has no MCPManager shutdown owner.

`DynamicRegistry` owns `MCPManager`, but has no shutdown API.

`cl/src/main.py` owns the application lifetime, but currently has no `try/finally` cleanup around `webview.start()`.

`CapabilityDispatcher` executes local capabilities in a `ThreadPoolExecutor`; MCP handlers therefore normally execute on `capability-worker-*`, not on the event loop that created the stdio session.

---

## 3. Critical correction: same event loop is not enough

The previous addendum froze:

```text
connect / call / close
must occur on one MCP event loop
```

That is necessary, but not sufficient.

The repository pins `mcp==2.2.0`. In that SDK, `stdio_client` owns an AnyIO task group across its async context, and `ClientSession.__aenter__()` enters another AnyIO task group that is exited by `ClientSession.__aexit__()`.

Therefore the stronger invariant is:

```text
stdio_client.__aenter__
stdio_client.__aexit__

ClientSession.__aenter__
ClientSession.__aexit__

must execute inside the SAME long-lived adapter lifecycle Task
on the SAME dedicated MCP event loop.
```

It is not sufficient to enter a context in Task A and later invoke `AsyncExitStack.aclose()` from Task B on the same event loop.

### Target ownership shape

```text
MCPManager
└── one dedicated thread
    └── one dedicated asyncio event loop
        ├── Adapter A lifecycle Task
        │   ├── enter stdio_client
        │   ├── enter ClientSession
        │   ├── initialize / discover
        │   ├── remain alive until close requested
        │   ├── exit ClientSession
        │   └── exit stdio_client
        │
        └── Adapter B lifecycle Task
            └── same invariant
```

Individual `call_tool()` operations may run as separate Tasks on the same MCP loop, but lifecycle enter/exit authority remains with the adapter lifecycle Task.

---

## 4. Race and deadlock audit

### RACE-1 — dispatcher cancellation does not stop a running MCP worker

Current dispatcher cancellation sets a `threading.Event` and calls `Future.cancel()` on the outer worker Future.

A worker already blocked in:

```text
run_coroutine_threadsafe(...).result()
```

cannot be stopped by cancelling that outer worker Future.

**Frozen correction:** the synchronous MCP bridge receives `cancel_event` and transfers cancellation to the concurrent Future returned by `asyncio.run_coroutine_threadsafe()`.

V1 may use bounded cancellation-event polling for cancellation responsiveness. Correct cleanup must not depend on sleep or polling; `MCPManager.shutdown()` separately cancels tracked MCP-loop futures and drains adapter lifecycle Tasks.

---

### RACE-2 — shutdown races submit before active-call registration

Possible sequence:

```text
worker submits MCP coroutine
shutdown starts
worker has not yet registered the concurrent Future
```

**Frozen rule:** manager admission state and active Future tracking use one state authority. After `_closing=True`, no new MCP invocation is accepted. If shutdown wins after submit but before full registration, the caller cancels the just-created Future and fails closed.

---

### DEADLOCK-1 — manager joins its own owner thread

Forbidden:

```text
MCP owner thread
→ MCPManager.shutdown()
→ owner_thread.join()
```

**Frozen rule:** synchronous `shutdown()` called from the MCP owner thread raises `RuntimeError` rather than attempting self-join.

---

### DEADLOCK-2 — manager lock held while waiting on owner-loop work

No manager state/lifecycle lock may be held while blocking on:

```text
concurrent_future.result()
thread.join()
adapter async close completion
```

Owner-loop tasks must never depend on a lock that a waiting caller is holding.

---

### DEADLOCK-3 — loop startup waits while holding state lock

Forbidden:

```text
caller holds state lock
starts owner thread
waits for ready Event

owner thread needs same state lock
to publish loop / set ready
```

**Frozen rule:** decide/start under lock, release lock, wait for owner-ready Event, then reacquire only to inspect final state.

---

### RACE-3 — startup timeout abandons partially entered stdio resources

A timeout may arrive during:

```text
stdio spawn
ClientSession enter
initialize
list_tools
```

**Frozen rule:** startup cancellation cancels the adapter lifecycle Task. The lifecycle Task itself unwinds every context it entered. No unrelated Task calls `__aexit__()` for it.

---

### RACE-4 — close while `call_tool()` is active

Forbidden:

```text
close stdio
then cancel active calls
```

Required order:

```text
reject new calls
cancel tracked owner-loop call Tasks
await/gather their termination
signal adapter lifecycle close
lifecycle Task exits ClientSession
lifecycle Task exits stdio_client
```

---

### RACE-5 — application shutdown after non-draining dispatcher shutdown

Current `CapabilityDispatcher.shutdown()` uses:

```python
self._executor.shutdown(wait=False, cancel_futures=True)
```

V1 deliberately does not redesign the dispatcher.

V1 shutdown safety relies on this order:

```text
ClientRuntime.stop()
→ reset_principal sets existing invocation cancel_event objects
→ dispatcher executor stops accepting queued work

then

DynamicRegistry.shutdown()
→ MCPManager cancels tracked MCP-loop futures
→ adapter call Tasks drain
→ adapter lifecycle Tasks close stdio
```

A capability worker may still be unwinding after MCP shutdown, but it no longer owns or keeps the MCP transport alive.

Full dispatcher wait/drain remains deferred.

---

### RACE-6 — repeated `/init` creates another MCP generation

Transactional `/init` is explicitly out of V1.

However V1 must not retain the current deterministic resource leak.

**Frozen V1 fail-closed guard:**

```text
DynamicRegistry.load_all()
AND MCPManager already has active adapters
→ raise before settings/tools/skills mutation
```

This is not transactional reload. It only prevents another MCP process generation from being appended.

When no MCP adapters are active, current local-only reload behavior may remain.

---

### RACE-7 — internal invocation metadata leaks into MCP tool arguments

`LocalCapabilityExecutor` injects:

```text
invocation_id
connection_id
session_id
cancel_event
```

when a target accepts `**kwargs`.

The current MCP handler accepts only `**kwargs`, so these internal values are forwarded to the MCP server as tool arguments.

**Frozen handler signature:**

```python
def handler(
    *,
    invocation_id=None,
    connection_id=None,
    session_id=None,
    cancel_event=None,
    **tool_arguments,
) -> str:
    ...
```

Correlation/cancellation fields are consumed locally and never sent in MCP `tools/call.arguments`.

---

## 5. Exact V1 API freeze

### 5.1 `MCPManager`

```python
class MCPManager:
    def __init__(
        self,
        *,
        startup_timeout_seconds: float = 15.0,
        shutdown_timeout_seconds: float = 15.0,
        cancellation_poll_seconds: float = 0.05,
    ) -> None:
        ...

    @property
    def owner_thread_id(self) -> int | None:
        ...

    @property
    def has_active_adapters(self) -> bool:
        ...

    @property
    def is_running(self) -> bool:
        ...

    def load_mcp_servers(
        self,
        mcp_servers_config: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        ...

    def invoke(
        self,
        adapter: "MCPClientAdapter",
        tool_name: str,
        arguments: dict[str, object],
        *,
        cancel_event: threading.Event | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        ...

    def shutdown(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        ...
```

`load_mcp_servers()` remains synchronous to preserve the existing `DynamicRegistry` call path.

No caller receives direct ownership of the MCP event loop.

---

### 5.2 `MCPClientAdapter`

Conceptual internal API:

```python
class MCPClientAdapter:
    async def start(self) -> dict[str, dict[str, object]]:
        ...

    async def connect(self) -> None:
        """Compatibility facade; owner-loop only."""
        ...

    async def get_mapped_tools(self) -> dict[str, dict[str, object]]:
        ...

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> str:
        ...

    async def close(self) -> None:
        ...
```

All async methods above are owner-loop only.

Only `MCPManager` may submit them.

The adapter lifecycle Task owns the actual nested async contexts.

---

### 5.3 `DynamicRegistry` V1

```python
class DynamicRegistry:
    def load_all(self) -> None:
        ...

    def shutdown(self) -> None:
        ...
```

Additional V1 invariant:

```text
load_all() with active MCP adapters
→ RuntimeError before live registry mutation
```

No `prepare_reload()`, `commit_reload()`, or `abort_reload()` belongs to V1.

---

## 6. Exact `CapabilityDispatcher` API — deferred design only

This API is reviewed and frozen only as a possible future post-roadmap integration surface.

**It is not implemented by MCP ownership V1.**

```python
class DispatcherLifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    QUIESCED = "QUIESCED"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    CLOSED = "CLOSED"


class CapabilityDispatcher:
    def quiesce(
        self,
        *,
        reason: str,
    ) -> int:
        ...

    def wait_for_idle(
        self,
        *,
        timeout: float | None = None,
    ) -> bool:
        ...

    def resume(
        self,
        *,
        expected_epoch: int,
    ) -> None:
        ...

    def shutdown(
        self,
        *,
        wait: bool = True,
        timeout: float | None = None,
    ) -> None:
        ...
```

Future constraints:

- `wait_for_idle()` should use a `threading.Condition`, not sleep polling.
- `_complete()` should notify that condition after removing an invocation.
- no dispatcher lock may be held while waiting for a worker;
- `ThreadPoolExecutor.shutdown(wait=True)` must never be called from one of its own worker threads;
- terminal replay while QUIESCED must be specified separately from admission of new work.

Again: none of the above is in V1.

---

## 7. Application lifetime ownership

`cl/src/main.py` is the natural owner of both:

```text
DynamicRegistry
ClientRuntime
```

V1 final cleanup order:

```text
try:
    webview.start(...)
finally:
    try:
        client_runtime.stop()
    finally:
        registry.shutdown()
```

This guarantees MCP cleanup even if `ClientRuntime.stop()` raises.

Ordinary WebSocket reconnect must not call `DynamicRegistry.shutdown()`.

---

## 8. MCP SDK 2.2 compatibility

The repository pins:

```text
mcp==2.2.0
```

V2 tool models use snake-case Python attributes.

The V1 adapter should read:

```text
tool.input_schema
```

with a compatibility fallback to legacy:

```text
tool.inputSchema
```

This is a compatibility correction only; it does not change tool semantics.

---

## 9. V1 real-stdio test contract

### Real subprocess ownership test

```text
pytest process
→ MCPManager owner thread/loop
→ stdio_client
→ real child Python MCP server
→ list_tools
→ capability-style worker-thread call
→ MCPManager.shutdown
→ child reaped
```

Assertions:

```text
worker thread != MCP owner thread
tool result returned
child PID exists while connected
child PID is gone after shutdown
manager owner thread is stopped
```

### Cancellation handoff test

```text
worker invokes long MCP call
child writes deterministic "started" marker
test sets cancel_event
sync bridge cancels owner-loop Future
worker unblocks
manager shutdown reaps child
```

The marker is only test synchronization. Production resource cleanup does not depend on a sleep.

### Failed-startup rollback test

```text
child exits during startup
→ no adapter published
→ manager shutdown remains clean
→ owner loop stops
```

### Windows warning gate

```powershell
-W error::ResourceWarning
-W error::pytest.PytestUnraisableExceptionWarning
```

Expected:

```text
0 BaseSubprocessTransport.__del__
0 _ProactorBasePipeTransport.__del__
0 unclosed transport
0 surviving MCP child process
```

---

## 10. Review result

```text
cross-loop ClientSession use          RESOLVED BY DESIGN
same-task context ownership           RESOLVED BY DESIGN
startup rollback                      RESOLVED BY DESIGN
call cancellation handoff             RESOLVED BY DESIGN
shutdown vs invocation race           RESOLVED BY DESIGN
load vs shutdown serialization        RESOLVED BY DESIGN
owner-thread self-join                GUARDED
lock/wait deadlock                    GUARDED
partial initialization                GUARDED
runtime metadata leakage              FIXED IN V1 DESIGN
unsafe repeated MCP /init             FAIL-CLOSED IN V1
transactional /init                   DEFERRED
dispatcher quiesce/drain              DEFERRED
R1→R14 roadmap changes                NONE
```

**Senior review disposition: APPROVED FOR NARROW MCP OWNERSHIP V1 PATCH.**
