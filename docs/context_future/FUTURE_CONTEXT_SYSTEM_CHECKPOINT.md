# FUTURE CONTEXT SYSTEM — PARKED CHECKPOINT

**Branch:** `feature/context-memory-personalization-future`  
**Base:** `main @ 28757e9c46355083ed16ee7bfda9c98fd7883a9b`  
**Status:** PARKED / FUTURE  
**Roadmap:** `docs/context_future/UNIFIED_CONTEXT_MEMORY_PERSONALIZATION_ROADMAP.md`

## Current intent

This branch records future architecture only. It must not be treated as an implementation branch.

The work includes:

- pull-first Context APIs;
- Session/Task/Branch entity discovery;
- TaskAdmission and TaskFinalization;
- ToolResponsePayload storage;
- long-term Memory;
- User/Agent/UserAgent personalization;
- Context pins, scoring and deduplication;
- ExecutionContinuityState;
- Working Set + immutable ContextSnapshot;
- CompactContext focused on continuity preservation;
- future `DIRECT -> DefaultChatAgent -> AgentRuntime`.

## Dependency gate

At branch creation, `main` already contains the AE-R9 merge.

This work remains parked behind:

```text
R10
R11
R12
R13
R14
```

unless the user explicitly re-freezes the order.

Central Asset F5-F8 must also be re-audited on the eventual unified post-R14 baseline.

## Do not implement early

Do not wire any of these into production before the future gate:

```text
Memory -> ContextBuilder
CompactContext -> transcript rewrite
ContextSnapshot -> checkpoint schema
automatic profile inference writes
DirectChatRuntime-specific memory
branch memory promotion
global asset/tool semantic retrieval without owner fencing
```

## Resume procedure

When this work is resumed:

1. inspect latest `main`;
2. verify R10-R14 completion;
3. inspect current Central Asset integration state;
4. audit exact Task/Branch/Execution context authority;
5. freeze final namespace;
6. write the exact CF0/CTX-F0 contract;
7. only then start implementation.

This checkpoint exists so future sessions/agents do not reconstruct these architectural decisions from chat history.
