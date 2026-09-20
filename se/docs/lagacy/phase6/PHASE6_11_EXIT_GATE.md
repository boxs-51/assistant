# Phase 6.11 Exit Gate

Status: **GREEN** on 2026-09-16.

## Canonical Agent context

- [x] `AgentDefinition` resolves through `AgentRegistry`.
- [x] Constitution, identity, goal, instruction, and execution constraints are composed by `DefaultAgentSystemPromptProvider`.
- [x] Every inference snapshot starts with exactly one canonical system message.
- [x] Existing/stale system messages are removed before the canonical prompt is rebuilt.
- [x] Conversation order is preserved and the initial user prompt is not duplicated after a tool result.
- [x] Tool result is present at the end of the second-inference context.

## Capability view

- [x] `RegistryAgentCapabilityResolver` is the central capability projection.
- [x] Server registry and remote client catalog implementations are supported.
- [x] Missing, non-executable, unauthorized, and foreign-owner capabilities are absent.
- [x] `InferenceRequest.tools` is derived from the resolved capability view.
- [x] Production `RegistryAgentToolPolicy` recognizes executable remote catalog capabilities.

## Boundaries and wiring

- [x] `DefaultAgentContextAssembler` implements the Phase 6.11 assembly contract.
- [x] `ContextBuilderAdapter` delegates semantic assembly instead of duplicating it.
- [x] Production bootstrap wires prompt provider, resolver, assembler, and adapter.
- [x] AgentRuntime remains transport/location agnostic and has no CLIENT/SERVER branch.
- [x] No Skill Runtime code was added.

## E2E and regression

- [x] Real TCP/WebSocket E2E checks system content and visible tools on inference 1.
- [x] A real client dispatcher executes the remote capability.
- [x] Inference 2 sees the same single system context and the returned tool result.
- [x] Final Agent execution completes successfully.
- [x] Combined Phase 6.9-6.11 and context/coordinator regressions: `61 passed`.
- [x] Repository-wide non-live run: `273 passed`.
