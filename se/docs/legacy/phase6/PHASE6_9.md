# Document 1: Phase 6.9 — Capability Execution Boundary Completion

## Status

Target patch for commit `960e5f01ff53aa5968fc7344d1964fa5c5a426c3` (2026-09-15).

Phase 6.9 closes three P0 gaps identified in the audit:

1. `CapabilityRoutingPolicy` was defined but not injected by the application bootstrap.
2. `/v1/capabilities/tools` created only a logical definition, so catalog-based execution could have no executable `SERVER` implementation.
3. `connection_id` could be carried implicitly in metadata, allowing execution context, routing context, and remote driver context to diverge.

---

## Contract after Phase 6.9

```text
AgentExecutionContext
    connection_id
        │
        ├── ToolExecutionRequest.connection_id
        │
        ▼
CapabilityToolExecutionAdapter
        │
        ├── invariant: request.connection_id == context.connection_id
        │
        ▼
CapabilityRuntime.execute_capability(connection_id=...)
        │
        ▼
CapabilityRoutingPolicy
        │
        ├── matching CLIENT implementation for active connection
        ├── never another CLIENT connection
        └── SERVER/MCP fallback where policy allows
        │
        ▼
RemoteClientDriver
        │
        └── selected implementation.connection_id == context.connection_id
```

### P0.1 Bootstrap Routing Policy

`main.py` now constructs:

```python
CapabilityRoutingPolicy(
    connection_availability=connection_runtime.registry,
)
```

And injects it into `CapabilityRuntime`. This makes the production bootstrap match the Phase 6.6 test topology.

### P0.2 Server Tool Implementation

The canonical `/v1/capabilities/tools` endpoint now refuses to claim a tool is executable unless a real executable server driver already exists in `CapabilityRegistry`.

For executable server tools, it creates:

```text
implementation_id = server:<capability_id>
location         = SERVER
owner_type       = SYSTEM
driver_kind      = SERVER_REGISTRY
state            = ENABLED
```

The catalog therefore no longer contains a routable capability definition without a concrete implementation for server-owned tools.

Remote/client tools continue to use the WebSocket `capability.register` path from Phase 6.5.

### P0.3 Connection Invariant

`connection_id` is explicit in:
- `AgentExecutionContext`
- `CapabilityExecutionContext`
- `ToolExecutionRequest`

The runtime rejects conflicting explicit and metadata connection IDs.

A tool request whose connection differs from the agent execution context is rejected before capability execution.

`RemoteClientDriver` additionally verifies that the selected implementation connection matches the capability execution context.

Resume compatibility is preserved by inheriting a missing legacy `ToolExecutionRequest.connection_id` from the active `AgentExecutionContext`.

### Routing Rule

With an execution connection:
1. `CLIENT` implementation bound to current connection.
2. Non-client implementation (`SERVER`/`MCP`/`DECLARATIVE` if supported).
3. Foreign `CLIENT` implementation is excluded.

This prevents the session from silently jumping from desktop A to desktop B.

---

## Test Matrix

### Unit Tests
- Connection-affine client selection
- Foreign client exclusion
- Server fallback under a connection-bound execution
- Tool-request/context connection mismatch
- Explicit/metadata connection conflict

### Integration Tests
- Capability runtime sends `capability.invoke` through the active connection
- Inbound `capability.result` resolves the exact invocation
- Server tool registration creates a catalog implementation
- Non-executable HTTP tool registration is rejected

### E2E Flow

```text
AgentRuntime
  -> tool_call
  -> CapabilityToolExecutionAdapter
  -> CapabilityRuntime
  -> CapabilityRoutingPolicy
  -> RemoteClientDriver
  -> WebSocket client
  -> capability.result
  -> AgentRuntime
  -> next inference iteration
  -> final answer
```

The E2E test asserts the same `connection_id`, `execution_id`, and `invocation_id` through the remote invocation boundary.

---

## Important Compatibility Note

`ToolRegistry` remains a metadata/compatibility registry. The Phase 6.9 change does not put tool execution logic into `ToolRegistry`.

**Server-side execution authority remains:**
```text
CapabilityRegistry -> BaseCapabilityDriver
```

**Client-side execution authority remains:**
```text
ConnectionRuntime -> RealtimeMultiplexer -> RemoteClientDriver -> client
```

---