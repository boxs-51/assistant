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


## Central Asset cross-roadmap checkpoint

Detailed audit/matrix:

```text
docs/context_future/CENTRAL_ASSET_F1_F8_CTX_F_DEPENDENCY_AUDIT.md
```

Durable conclusions from that audit:

```text
- Central Asset F1-F4 remain the canonical file/media architecture,
  but the parked branch must be replayed/re-audited on post-R14 main.
- F5-0 must be re-frozen after R10/R11/R12 and before provider hydration.
- CTX must reuse one canonical asset read-lease/GC fence rather than inventing
  a parallel Context lease authority.
- FileAsset.revision is lifecycle/CAS authority, not content-version authority;
  Context uses canonical content evidence such as blob_id + sha256.
- F6/F7/F8 require dedicated contract freezes before code.
- Full F5-F8 completion is not automatically required before CTX-F0/F1;
  the detailed HARD/SOFT/EXIT matrix controls sequencing.
```

Central Asset still has no dedicated issue at this checkpoint. Creating/activating
that issue remains a required action at the post-R14 Central Asset resumption gate.
