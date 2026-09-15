# Exit Gate Criteria

Phase 6.9 is considered complete only when:
- [ ] Bootstrap injects `CapabilityRoutingPolicy`.
- [ ] Client connection registry is the availability provider.
- [ ] Server tool registration does not create definition-only executable claims.
- [ ] Connection ID is explicit in execution contracts.
- [ ] Foreign client implementations cannot be selected.
- [ ] Remote driver rejects mismatched context/implementation connection.
- [ ] Client result correlation resolves the correct invocation.
- [ ] Agent loop continues to a second inference after remote tool result.
- [ ] All new Phase 6.9 tests pass.
- [ ] Existing Phase 6.6/6.8 compatibility tests remain green.

---

## Known Follow-Up

Phase 6.9 intentionally does not migrate:
- `/v1/tools` legacy endpoint
- `/v1/agents` legacy endpoint
- `/v1/sessions/{id}/regenerate` direct `ProviderRuntime` path
- Agent event -> `assistant.delta`/`completed`/`error` realtime mapping
- Provider streaming DTO normalization

Those belong to later migration phases.

---
---

# Document 2: Phase 6.9 Exit Gate Checklist

## P0 — Bootstrap
- [ ] `CapabilityRoutingPolicy` constructed during application bootstrap.
- [ ] `ConnectionRegistry` supplied as connection availability provider.
- [ ] `CapabilityRuntime.routing_policy` is non-null in the production container.

## P0 — Server Tool
- [ ] `/v1/capabilities/tools` requires an executable server driver.
- [ ] Successful server registration creates `server:<capability_id>`.
- [ ] Server implementation is `SERVER` / `SYSTEM` / `ENABLED`.
- [ ] Definition-only remote tools are rejected by this server endpoint.
- [ ] Client tools still register through `/v1/events/ws`.

## P0 — Connection Invariant
- [ ] `AgentExecutionContext.connection_id` is explicit.
- [ ] `CapabilityExecutionContext.connection_id` is explicit.
- [ ] `ToolExecutionRequest.connection_id` is explicit.
- [ ] Tool request/context IDs must match.
- [ ] Explicit argument and metadata connection IDs must not conflict.
- [ ] `RemoteClientDriver` refuses a mismatched connection.

## Routing
- [ ] Current-session `CLIENT` implementation wins over foreign `CLIENT` implementations.
- [ ] Foreign `CLIENT` implementations are never selected.
- [ ] `SERVER` fallback remains possible when no current `CLIENT` implementation is available.
- [ ] Stale/disconnected connections remain unroutable.

## E2E
- [ ] Agent emits a tool call.
- [ ] Tool invocation uses the active session connection.
- [ ] Client receives `capability.invoke`.
- [ ] Client returns `capability.result`.
- [ ] Result resolves the same `invocation_id`.
- [ ] Agent continues to next iteration.
- [ ] Final answer is returned.

## Regression
- [ ] Phase 6.6 tests remain green.
- [ ] Phase 6.8 resilience tests remain green.
- [ ] Legacy `/v1/tools` and `/v1/agents` compatibility tests remain green.