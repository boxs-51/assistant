# Phase 6 — Capability Control Plane

## Purpose

Phase 6 introduces a unified Capability Control Plane without coupling `AgentRuntime`
to the physical execution location of a capability.

The core invariant is:

> A Capability is a logical ability. An Implementation is where/how that ability is executed.

Supported execution locations:

- `SERVER`: executed by a server-side driver.
- `CLIENT`: executed by a client connected through `ConnectionRuntime`.
- `MCP`: executed through an MCP transport/driver.
- `DECLARATIVE`: composed by a higher-level Skill/Workflow definition.

Phase 6.0 in this patch is **foundation only**. It defines contracts and ownership
boundaries; it deliberately does not replace the current registry/runtime execution path.

## Architectural boundaries

```text
AgentRuntime
    |
    | ToolExecutionPort
    v
CapabilityRuntime
    |
    +--> Control Plane: catalog / policy / routing
    |
    +--> Execution Plane: selected implementation
    |
    +--> Connection Plane: remote client transport
```

`AgentRuntime` MUST NOT branch on `SERVER` vs `CLIENT`.

## Capability vs Implementation

A capability may have multiple implementations:

```text
filesystem.read
  ├── server implementation
  ├── desktop-01 client implementation
  └── desktop-02 client implementation
```

The catalog therefore cannot use `capability_id` as the identity of one executable
driver.

## Realtime contract

The eventual realtime connection multiplex must support at least:

```text
connection.register
connection.registered
connection.heartbeat
connection.state

capability.register
capability.unregister

capability.invoke
capability.progress
capability.result
capability.error
capability.cancel
capability.cancelled

assistant.delta
assistant.completed
assistant.error
```

Every invocation is correlated by `invocation_id` and must preserve `execution_id`,
`session_id`, `connection_id`, and `trace_id` where available.

## Phase sequence

```text
6.0 Foundation contracts       <- this patch
6.1 Catalog + ownership + policy
6.2 Connection lifecycle
6.3 Realtime multiplex
6.4 Remote client driver
6.5 Client self-registration
6.6 Tool/Agent compatibility migration
6.7 Skill/Workflow composition
6.8 End-to-end resilience/live tests
```

No existing Phase 5 AgentRuntime execution flow is changed by 6.0.
