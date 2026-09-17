# Assistant Runtime — Implementation Execution Report

This report is the execution companion to `Assistant Runtime — Master Implementation Roadmap.md`.

## Delivery plan and status

| Phase | Delivery slice | Status | Primary evidence |
|---|---|---:|---|
| 1 | Conversation `turn_id`, atomic `sequence`, lifecycle timestamps, stream correlation | Implemented | SQL model/repository, migration `5b_conversation_temporal_contract`, architecture tests |
| 1.5 | DIRECT/AGENT selection, read-only effect policy, context skills, fresh temporal context | Implemented | `DirectChatRuntime`, capability access policy, temporal provider, HTTP E2E tests |
| 2 | One immutable invocation ID from caller through driver/result | Implemented | `CapabilityRuntime.execute_capability(invocation_id=...)`, adapter propagation tests |
| 3 | Driver binding and execution by `implementation_id` | Implemented | `CapabilityDriverRegistry`, deterministic multi-implementation test |
| 4 | TOOL/SKILL/AGENT on the unified capability path | Implemented | Python/MCP/remote, executable skill, and agent capability drivers; API E2E tests |
| 5 | Canonical invocation state machine | Implemented | immutable terminal states, canonical WAITING reason, transition tests |
| 6 | Invocation attempts, SQL persistence, revision CAS, events, retry/fallback | Implemented | migration `6a_capability_invocations`, SQL store test, disconnect fallback tests |
| 7 | Production-shaped HTTP and real TCP/WebSocket E2E | Implemented | DIRECT, skill, Agent, remote client, and real disconnect/fallback E2E tests |
| 8 | Legacy authority cleanup | Implemented | compatibility endpoints delegate to the capability control plane; inventory below |

## Phase 8 legacy inventory

| Surface | Classification | Result |
|---|---|---|
| `CapabilityRuntime` / `CapabilityCatalog` / invocation lifecycle | KEEP | Canonical execution and lifecycle authority |
| `AgentRuntime` | KEEP | Canonical Agent loop; physical capability location remains outside it |
| `AgentRegistry` | KEEP | Definition lookup only; Agent execution is exposed through `AgentCapabilityDriver` |
| `ToolRegistry` | COMPATIBILITY | Metadata projection for old consumers; it is not invocation authority |
| `CapabilityRegistry` | MIGRATE | Temporary legacy server-driver projection; concrete drivers are now additionally bound by `implementation_id` |
| `CapabilityRuntime.execute_tool` | COMPATIBILITY | Thin wrapper that delegates to `execute_capability` |
| `/v1/tools` and `/v1/agents` | COMPATIBILITY | Preserve response shapes while delegating registration to `/v1/capabilities` logic |
| `/v1/capabilities/{id}/execute` | KEEP | Canonical HTTP execution endpoint |
| Event-bus capability command handler | COMPATIBILITY | Transport adapter only; delegates to `CapabilityRuntime` |
| Agent tool call/result persistence | KEEP | Agent audit/checkpoint data only; global authority is `capability_invocations` |
| Duplicate capability status models | MIGRATE | Catalog implementation state is routing authority; legacy registry state remains only for compatibility health projection |

## Verified architecture paths

1. HTTP Gateway → DIRECT → Provider Runtime → READ tool → Capability Runtime → second inference.
2. HTTP Gateway → DIRECT → context-only Skill → Provider Runtime.
3. Capability API → executable Skill driver → Provider Runtime → Capability result.
4. Capability API → Agent capability driver → Agent Runtime → server tool → second inference.
5. Agent Runtime → Capability Runtime → real TCP/WebSocket client → client tool → result.
6. Real TCP/WebSocket disconnect → attempt 1 fails → server implementation attempt 2 → one completed invocation with the same ID.

## Operational requirement

Deployments must run Alembic through revision `6a_capability_invocations` before starting the updated server. Client-orchestrated runtime remains outside this implementation, as required by the roadmap.

## Final verification

- `python -m pytest cl/tests se/tests -q`: **322 passed**.
- `python -m compileall -q cl/src se/src`: passed.
- `node --check cl/src/ui/web/js/components/gatewayPanel.js`: passed.
- Empty SQLite database upgraded through Alembic head (`6a_capability_invocations`): passed.
- `git diff --check`: passed (line-ending notices only).
