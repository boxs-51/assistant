# Phase 6.2 — Connection Lifecycle

## Scope

Phase 6.2 introduces deterministic lifecycle management for transport
connections.

The lifecycle is:

```text
REGISTERED
    |
    v
 ACTIVE <------+
    |           |
    v           |
 STALE --------+
    |
    +-------> DISCONNECTED
                    |
                    v
                 REMOVED
```

## Invariants

### 1. Connection identity is independent from capability identity

```text
connection_id
    !=
capability_id
```

A connection may expose multiple capability implementations later.

### 2. Connection identity is independent from transport session identity

```text
connection_id
session_id
```

are separate identifiers.

`session_id` identifies the current transport/session context.
`connection_id` identifies the logical connection lifecycle.

### 3. Heartbeat is the source of liveness

An `ACTIVE` connection becomes `STALE` when:

```text
now - last_heartbeat_at >= stale_after_seconds
```

The stale eviction operation does not immediately destroy the logical
connection record.

### 4. Heartbeat can recover STALE

```text
STALE --heartbeat--> ACTIVE
```

This keeps reconnect/recovery semantics available for later phases without
implementing reconnect transport yet.

### 5. Disconnect does not remove capability definitions

Disconnect only changes connection state.

Capability definitions remain owned by the capability catalog.

Capability implementations bound to this connection can later be made
unavailable by routing/control-plane policy.

### 6. Phase 6.2 does not perform invocation

This phase does not implement:

- WebSocket multiplexing
- invocation correlation
- remote capability execution
- client self-registration
- cancellation
- progress/result routing
- AgentRuntime changes

Those belong to later phases.