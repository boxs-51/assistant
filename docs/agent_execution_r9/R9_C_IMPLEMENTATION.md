# AE-R9-C Implementation — RETRY Activation and Runtime Handoff

## Scope

R9-C reconstructs a retry only from committed durable state and separates
context preparation from activation authority. `RUNNING@1` is admitted but not
yet process-owned; a specialized CAS grants one `RUNNING@2` activation winner.

## Flow

1. Load/replay the immutable retry receipt.
2. Rebuild context from E2's committed source-derived seed and transcript.
3. Validate owner, terminal source, OPEN branch head, lineage and active budget.
4. Reserve process-local ownership (wired by the R9-G control plane).
5. Lock Task -> TaskBudget -> Branch -> source execution -> receipt.
6. CAS E2 `RUNNING@1 -> RUNNING@2` and hand it to `AgentRuntime`.

Task cancellation races the same revision-1 fence. If cancellation wins it
marks E2 CANCELLED and releases its precharged capacity exactly once.

## Verification plan

- Restart reconstruction returns the committed execution ID.
- The source terminal execution is never resurrected.
- Simultaneous activation has one CAS winner.
- Cancellation before activation settles counters once.
