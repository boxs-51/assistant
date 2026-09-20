# Phase 6.9 Exit Gate

Status: **GREEN** on 2026-09-16.

Phase 6.9 is complete only while every P0 item below remains green.

## Bootstrap and registration

- [x] Production bootstrap constructs `CapabilityRoutingPolicy`.
- [x] `ConnectionRegistry` is the connection-availability provider.
- [x] `CapabilityRuntime.routing_policy` is non-null in production.
- [x] `/v1/capabilities/tools` requires an executable server driver.
- [x] Successful server registration creates an enabled `SERVER` / `SYSTEM` implementation.
- [x] Definition-only server claims are rejected.
- [x] Client tools register through `/v1/events/ws` and remain executable without a server driver.

## Identity and routing

- [x] Connection identity is explicit in Agent, tool, and capability execution contracts.
- [x] Tool request and Agent context connection IDs must match.
- [x] Explicit and metadata connection IDs cannot conflict.
- [x] `RemoteClientDriver` rejects context/implementation mismatch.
- [x] The exact active client is preferred and foreign clients are excluded.
- [x] An issued invocation remains bound to its original connection.
- [x] A new routing decision may use a server implementation after client loss.

## Real WebSocket E2E

- [x] Canonical E2E uses real uvicorn and a real TCP WebSocket.
- [x] It uses `ClientRuntime`, its receiver loop, and `CapabilityDispatcher`.
- [x] It does not manually call `handle_inbound()` or the dispatcher.
- [x] The local Python capability executes on the client.
- [x] `capability.result` returns over the real socket with the same invocation identity.
- [x] AgentRuntime performs a second inference and completes.

## Disconnect and continuation

- [x] Disconnect marks the connection inactive and fails pending invocations.
- [x] Each failure is `RemoteConnectionLost(connection_id, invocation_id)`.
- [x] Late and foreign results cannot resurrect an invocation.
- [x] Agent creates an immutable disconnect checkpoint with transcript and pending identity.
- [x] Remote-only work enters `WAITING_FOR_CONNECTION` without unsafe retry.
- [x] Server-side continuation can proceed automatically as a new routing decision.
- [x] Reconnect requires a new connection identity and creates an isolated branch.
- [x] Branch merge requires the owning user and matching base checkpoint.
- [x] Stale merge is rejected and successful merge is idempotent.
- [x] Continuation state is persisted in the existing execution `context_state` JSON.

## Verification

- [x] Phase 6.9 plus Phase 6.6/6.8 and coordinator regression selection: `51 passed`.
- [x] Focused Phase 6.9 plus compatibility selection: `29 passed`.
- [x] Repository-wide non-live run: `273 passed`.

## Deferred migrations

Phase 6.9 intentionally does not migrate legacy `/v1/tools`, `/v1/agents`,
`/v1/sessions/{id}/regenerate`, Agent-event realtime mapping, or provider
streaming DTO normalization. Those remain later-phase work.
