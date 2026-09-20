# CL ↔ SE Architecture Contract v1

This document records the R0–R7 decisions implemented from the architecture
audit baseline `c75e465443df42d610b5ae3cb2d121491c9afa3e`.

## Execution authority

- SE `AgentRuntime` is the only online agent-loop authority.
- CL `AgentEngine` is retained only for explicit `LOCAL_OFFLINE` mode.
- Client-side side effects always pass the CL risk and HITL boundary, including
  invocations originating from SE.
- Realtime self-registration is for client-executable tools. Server-owned skill
  and agent semantics remain on SE.

## Identity and affinity

| Identifier | Authority | Lifetime | Routing use |
| --- | --- | --- | --- |
| `user_id` | Authenticated SE identity | Login/principal | Ownership only |
| `client_id` | Persisted CL installation ID | App restarts and auth sessions | Same-installation resume policy |
| `connection_id` | CL, one value per WebSocket generation | One live socket | Required hard affinity for remote tools |
| `session_id` | Domain conversation/task | Conversation/task | Context correlation only |

Forbidden mappings include `session_id -> connection_id`, `user_id -> client_id`,
and reconnecting with a previous `connection_id`. SE validates that an explicit
connection is active and owned by the authenticated principal before accepting
chat or multi-agent execution.

## Realtime protocol v1

Every envelope carries `protocol_version: 1`. Correlated capability messages
require `connection_id` and `invocation_id`. The canonical terminal payloads are:

```json
{"type":"capability.result","payload":{"output":"<exact value>"}}
{"type":"capability.error","payload":{"code":"...","message":"...","details":{},"retryable":false}}
{"type":"capability.cancelled","payload":{}}
```

`output` preserves dictionaries, strings, numbers, lists, and null without an
additional wrapper. Registration is acknowledged with `capability.registered`.

## Invocation invariants

- One running invocation ID maps to one execution.
- A duplicate received while running is ignored; a terminal duplicate replays
  the cached exact result/error/cancelled outcome.
- Terminal outcomes are bounded by size and TTL and survive socket-generation
  changes within the CL process.
- Local schema validation, risk analysis, HITL, cancellation checks, and result
  validation occur before a result is returned to SE.
- Queue concurrency is bounded; disconnect cancels/detaches active work without
  converting unsafe side effects into automatic retries.

## Reconnect and resume

CL reconnect transitions through a bounded exponential backoff, creates a fresh
`connection_id`, registers the connection, publishes a full capability snapshot,
and becomes READY only after acknowledgement. Stale-generation callbacks cannot
mutate the new generation.

SE persists pending tool calls (including `iteration`), `connection_id`, and the
origin `client_id`. A waiting execution is exposed as structured
`WAITING_FOR_CONNECTION` data. After the new connection is READY, CL may send
`execution.resume`; SE validates owner, same-client policy, current checkpoint,
and capability availability before performing reconnect/merge, rebuilding the
durable context, rebinding it to the new connection, and restarting
`AgentRuntime`.

## Verification gates

- Canonical CI: Linux full suite and Windows CL contract suite.
- Local verification on 2026-09-18: `418 passed` using Python 3.12.10.
- Coverage includes real TCP WebSocket execution, affinity checks, durable
  request reconstruction, dispatcher HITL/duplicate replay, auth generation
  rotation, and legacy architecture exit gates.
