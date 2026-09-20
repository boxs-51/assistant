# Post-R14 MCP Client Integration Note

**Repository:** `boxs-51/assistant`  
**Related roadmap:** `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`  
**Status:** DEFERRED / DO NOT MODIFY R1→R14  
**Date:** 2026-09-20

---

## 1. Decision

The active R1→R14 roadmap remains unchanged.

MCP client ownership hardening is an orthogonal CL runtime improvement and must not retroactively:

- change R1→R14 numbering;
- add new R-phase prerequisites;
- rewrite R5/R6/R7 exit gates;
- alter Task / Branch / Execution / WAITING semantics;
- alter protocol migration contracts already frozen by the roadmap.

The earlier MCP addendum contained scheduling language that made MCP ownership a prerequisite for R6/R7. That scheduling language is superseded by the senior review.

---

## 2. Why retain a separate post-roadmap note?

Several future coherence improvements involve the same CL components used by continuation/reconciliation:

```text
DynamicRegistry
CapabilityRuntime
CapabilityDispatcher
ClientRuntime
MCPManager
```

They are useful, but they do not require changing the active Agent roadmap.

After R14 completes, an independent hardening stream may implement:

```text
POST-R14 MCP-H1
transactional DynamicRegistry generation reload

POST-R14 MCP-H2
CapabilityDispatcher quiesce + deterministic drain barrier

POST-R14 MCP-H3
capability.register commit-after-ACK generation coherence

POST-R14 MCP-H4
MCP-backed reconnect/reconciliation fault-injection coverage
```

This is a new post-roadmap integration stream, not a revision of historical R1→R14 contracts.

---

## 3. Allowed now in MCP ownership V1

```text
dedicated MCP owner thread/event loop
per-adapter lifecycle Task
deterministic stdio/session close
partial-startup rollback
worker cancellation handoff
application final shutdown
real stdio subprocess tests
fail-closed repeated /init while MCP is active
```

---

## 4. Explicitly deferred until post-R14

```text
transactional /init
registry generation IDs
candidate registry snapshots
CapabilityDispatcher QUIESCED state
wait-for-idle reload barrier
candidate capability.register
commit-after-server-ACK
unknown registration-outcome recovery
MCP-specific continuation/reconciliation integration
```

---

## 5. No reverse dependency

Dependency direction is explicitly:

```text
R1
 ↓
...
 ↓
R14
 ↓
roadmap complete

then, if desired:

POST-R14 MCP generation/reload integration
```

Not:

```text
MCP ownership
→ rewrite R6/R7
→ modify existing roadmap
```

---

## 6. Compatibility rule for future work

When post-R14 work begins, it must consume the final R1→R14 contracts as fixed inputs.

If a future MCP integration design conflicts with those contracts:

```text
change the MCP integration design
```

rather than silently redefining Agent semantics.

Only a separately reviewed and explicitly approved roadmap revision may alter historical R1→R14 contracts.
