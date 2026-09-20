# Phase 6 Architecture — Capability Control Plane

## 1. Design objective

The system needs to distinguish capabilities that should execute inside the AI Runtime
from capabilities that belong to the user's environment.

Examples:

| Capability | Preferred location | Reason |
|---|---|---|
| `web.search` | SERVER | shared service/resource |
| `database.query` | SERVER | server credential and policy boundary |
| `filesystem.read` | CLIENT | local user filesystem |
| `git.status` | CLIENT | local repository |
| `shell.execute` | CLIENT | local machine |
| `browser.click` | CLIENT | local browser/session |
| `embedding.generate` | SERVER | centralized model resource |

The decision is a routing concern, not an AgentRuntime concern.

## 2. Three planes

### 2.1 Control Plane

Owns metadata and policy:

- capability definitions
- implementation registrations
- ownership
- authorization metadata
- lifecycle
- availability
- connection binding
- discovery

### 2.2 Execution Plane

Owns concrete execution:

- server Python drivers
- MCP drivers
- remote-client driver
- future declarative/skill execution

### 2.3 Transport Plane

Owns remote connectivity:

- connection identity
- WebSocket/session lifecycle
- heartbeat
- reconnect
- invocation multiplexing
- response correlation
- cancellation

## 3. Canonical object model

```text
CapabilityDefinition
    logical contract
        |
        +---- CapabilityImplementation
                 implementation_id
                 location
                 driver_kind
                 connection_id?
                 owner_id?
```

### CapabilityDefinition

Stable logical identity:

```text
id
version
name
description
input_schema
output_schema
kind
required_scopes
metadata
```

`id` identifies the capability, not one executable instance.

### CapabilityImplementation

Concrete execution binding:

```text
implementation_id
capability_id
version
location
driver_kind
owner_id
connection_id
state
metadata
```

`implementation_id` MUST be unique independently of `capability_id`.

## 4. Capability kinds

```text
TOOL
SKILL
AGENT
```

These are semantic categories, not synonyms for physical execution.

- `TOOL`: one callable operation.
- `SKILL`: reusable composition of capabilities/instructions.
- `AGENT`: execution actor owned by `AgentRuntime`.

A capability kind MUST NOT encode whether execution is remote.

## 5. Execution locations

```text
SERVER
CLIENT
MCP
DECLARATIVE
```

Examples:

```text
web.search
    location = SERVER

filesystem.read
    location = CLIENT
    connection = desktop-01
```

## 6. Ownership

Initial ownership model:

```text
SYSTEM
USER
CLIENT
WORKSPACE
```

A client registration is scoped to an authenticated owner. The server must not make
a client capability globally callable merely because a client announced it.

## 7. Connection relationship

Connection is not a capability.

```text
Connection
    |
    +-- exposes implementation A
    +-- exposes implementation B
    +-- exposes implementation C
```

Disconnect changes availability of implementations bound to that connection; it does
not delete their logical capability definitions.

## 8. Realtime message envelope

All realtime messages use a common correlation envelope:

```json
{
  "type": "capability.invoke",
  "message_id": "msg-001",
  "timestamp": "2026-01-01T00:00:00Z",
  "session_id": "sess-001",
  "connection_id": "conn-001",
  "execution_id": "exec-001",
  "invocation_id": "inv-001",
  "trace_id": "trace-001",
  "payload": {}
}
```

`message_id` identifies a transport message.
`invocation_id` identifies one capability invocation.

## 9. Remote invocation lifecycle

```text
AgentRuntime
  |
  v
ToolExecutionPort
  |
  v
CapabilityRuntime
  |
  v
routing(policy)
  |
  +-------------------+
  |                   |
SERVER              CLIENT
  |                   |
server driver       RemoteClientDriver
                      |
                 ConnectionMultiplexer
                      |
                capability.invoke
                      |
                    Client
                      |
          progress / result / error
```

Cancellation:

```text
server -> capability.cancel -> client
client -> capability.cancelled -> server
```

## 10. Safety invariants

1. Client definitions are metadata only; the server does not execute client code.
2. Client capabilities are not executable before connection/ownership/authorization
   checks succeed.
3. A stale connection cannot continue receiving new invocations.
4. A disconnected connection must fail or cancel pending invocations deterministically.
5. Duplicate terminal results must be idempotent.
6. `AgentRuntime` must remain unaware of physical execution location.
7. Existing `/v1/tools` and `/v1/agents` remain compatibility surfaces until migration
   is explicitly completed.

## 11. Non-goals of Phase 6.0

This foundation patch does not:

- replace `CapabilityRegistry`;
- add WebSocket endpoints;
- change `CapabilityRuntime.execute_capability`;
- add client authentication;
- migrate `/v1/tools`;
- migrate `/v1/agents`;
- implement live streaming;
- implement the remote driver.

Those changes belong to later Phase 6 slices.
