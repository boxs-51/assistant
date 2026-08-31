# Phase 5.0 — Agent Runtime Contract Freeze

## Purpose

Freeze the boundaries required to implement a real Agent Runtime after Phase 4.

This phase deliberately contains contracts and contract tests only. It does not implement the Agent Runtime.

## Existing authorities

```text
MultiAgentCoordinator = task/control plane
AgentRuntime           = execution plane (future)
ContextRuntime         = context implementation
ProviderRuntime        = inference implementation
CapabilityRuntime      = capability/tool execution implementation
EventBus               = asynchronous lifecycle/observability transport
```

The future AgentRuntime must depend on ports, not on ProviderRuntime internals, ToolRegistry, CapabilityRegistry, MCP drivers, or ContextEngine directly.

## Contract package

```text
src/runtimes/agent/contracts/
├── context.py
├── loop.py
├── inference.py
├── tool.py
├── context_builder.py
├── policy.py
├── events.py
└── __init__.py
```

## Boundaries

### AgentExecutionContext

Carries execution identity, budgets, deadline, cancellation and correlation information.

### AgentLoopState

Describes the internal inference/tool loop. It is deliberately separate from persisted `AgentExecutionState`.

### InferencePort

Provider-neutral request/response boundary. Provider-specific DTOs and Gateway HTTP response models stay outside this boundary.

### ToolExecutionPort

Provider-neutral tool invocation boundary. A later adapter can delegate to CapabilityRuntime.

### ContextBuilderPort

Builds one context snapshot for one inference turn. A later implementation can delegate to ContextRuntime/ContextEngine.

### AgentToolPolicy / AgentExecutionPolicy

Separate tool visibility/authorization from execution-budget admission decisions.

### Correlation / Event contracts

Agent events carry stable execution and causation identifiers:

```text
request_id
  ↓
execution_id
  ↓
iteration_id
  ↓
tool_call_id
  ↓
invocation_id
```

`correlation_id`, `parent_execution_id`, `causation_id` and optional `trace_id` support parent/child execution and distributed tracing.

## Design rules

1. No AgentRuntime implementation belongs in Phase 5.0.
2. No changes to ProviderRuntime are required by this freeze.
3. No changes to ContextRuntime are required by this freeze.
4. No changes to CapabilityRuntime are required by this freeze.
5. EventBus is not used as synchronous RPC between runtimes.
6. `ToolRegistry` remains a compatibility surface; it is not a new AgentRuntime authority.
7. `GatewayResponse` remains a gateway/provider DTO; it is not the AgentRuntime inference contract.
8. `AgentExecutionState` remains the persisted execution lifecycle; `AgentLoopState` is runtime-only.

## Acceptance

The contract test suite must establish:

- execution context carries limits and cancellation;
- loop transitions are explicit and terminal states cannot restart;
- inference is provider-neutral;
- tool results have stable execution/tool/invocation identifiers;
- context snapshots are immutable at the model boundary;
- tool and execution policies can be substituted;
- event correlation can represent parent/child and causation chains.

## Non-goals

This phase does not implement:

- AgentRuntime;
- inference adapters;
- tool execution coordinator;
- JSON Schema validator;
- retry engine;
- scheduler;
- persistence adapters;
- MCP changes;
- provider cutover;
- HTTP API changes.
