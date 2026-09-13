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
6.2 Connection lifecycle       <- lifecycle + liveness
6.3 Realtime multiplex       <- correlation + timeout/cancel + transport boundary
6.4 Remote client driver
6.5 Client self-registration
6.6 Tool/Agent compatibility migration
6.7 Skill/Workflow composition
6.8 End-to-end resilience/live tests
```

No existing Phase 5 AgentRuntime execution flow is changed by 6.0.

## Phase 6.1 / 6.2 exit boundary

Before Phase 6.3:

```text
CapabilityCatalog

   |
   v

CapabilityRoutingPolicy

   |
   +---- ownership/auth
   |
   +---- implementation lifecycle
   |
   +---- connection availability
                 |
                 v
       ConnectionLifecycleRegistry

```

Client implementations are routable only while their bound connection is
ACTIVE.

STALE, DISCONNECTED, and REMOVED connections must reject new routing.

Phase 6.3 is responsible for realtime transport and invocation correlation.

## Phase 6.3 exit boundary

Phase 6.3 owns the realtime correlation boundary, but not client capability
registration or remote execution policy:

```text
capability.invoke
      |
      v
RealtimeMultiplexer
      |
      +--> ConnectionRegistry (ACTIVE gate)
      +--> ConnectionMultiplexer (invocation correlation)
      +--> timeout / cancel
      +--> inbound result/error/progress
      +--> disconnect -> fail pending
```

Phase 6.4 begins when a reusable `RemoteClientDriver` invokes this realtime
boundary from the execution plane.

## Phase 6.4 status

Phase 6.4 adds the reusable `RemoteClientDriver` adapter. It constructs the
`capability.invoke` envelope from `CapabilityExecutionContext`, preserves
execution/session/connection/invocation/trace correlation, applies the
context deadline, and sends `capability.cancel` when execution is cancelled.

The driver does not change `AgentRuntime`, capability routing, client
registration, or the legacy capability registry. Those migrations remain in
later Phase 6 slices.

## Phase 6.5 status

Phase 6.5 adds `ClientCapabilityRegistrationService` for authenticated
client self-registration. Registration requires an ACTIVE connection owned by
the request owner, and every advertised implementation must be a
`CLIENT`/`REMOTE_CLIENT` binding. Re-registering the same contract is
idempotent; conflicting definitions or implementation IDs are rejected before
catalog mutation.

Disconnect cleanup transitions all implementations bound to the connection to
`REMOVED` while retaining logical capability definitions. The connection
transport or lifecycle owner must call `unregister_connection()` during
disconnect handling.