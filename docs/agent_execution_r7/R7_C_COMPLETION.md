# R7-C Completion — Tool Result Commitment + Checkpoint-Directed Reconstruction

## Closure

- Repository: `boxs-51/assistant`
- Baseline: `24f3c5fc808729cbe714e13650c37262bccfef48`
- Verified implementation HEAD: `bbf4e7fd1a2a73a19cae8e3b9441bdc0a834763d`
- Branch: `r7-c-tool-result-commitment`
- Depends on: R7-B CLOSED
- Status: **CLOSED / VERIFIED**
- Scope boundary: R7-D / R7-E / R7-F remain deferred

## What R7-C closes

R7-C closes the model-context commitment boundary for Agent tool results and the deterministic reconstruction boundary for checkpoint resume.

The phase establishes these hard guarantees:

1. `PROVISIONAL` AgentToolResult rows never become model `role=tool` messages.
2. Remote results become model-consumable only after R6 terminal authority exists.
3. Promotion from `PROVISIONAL -> COMMITTED` uses the exact R6 terminal output/error projection.
4. A `COMMITTED` result is immutable against conflicting later projections.
5. Checkpoint reconstruction uses the checkpoint-safe transcript prefix.
6. Parallel tool batches reconstruct in canonical `AgentIteration.tool_call_ids` order, never SQL insert/completion order.
7. Current-batch tool messages are materialized exactly once.
8. Legacy transcript tool messages are reconstructed from the durable committed result, not trusted as arbitrary serialized content.
9. Reused `tool_call_id` with conflicting invocation/iteration/capability/arguments is rejected instead of silently aliasing another logical call.
10. Missing R6 invocation authority fails closed in production unless the result is explicitly marked as a safe Agent pre-dispatch outcome.
11. With commitment-aware persistence, WAITING classification uses commitment state rather than remote-looking error strings.

## Final hardening findings resolved

### P0 — semantic collision / committed-result aliasing

Before final hardening, persistence deduplicated by `(execution_id, tool_call_id)` and could silently reuse an earlier committed result if the same tool-call ID reappeared with a different invocation or iteration.

The final implementation now validates durable identity for tool calls and tool results:

```text
execution_id
iteration_id
invocation_id
tool_call_id
capability_id
arguments  # tool calls
```

Conflicts raise `ExecutionConflictError`.

Runtime committed-result reuse also validates the loaded record against the exact `ToolExecutionRequest` before conversion to `ToolExecutionResult`.

### P1 — stale legacy transcript payload

A historical `role=tool` message is no longer retained merely because a matching result row is COMMITTED.

Resume sanitization now materializes the tool message from the durable committed projection:

```text
success/output/error_code/error_message/retryable
```

This removes stale `REMOTE_OUTCOME_UNKNOWN` or other poisoned serialized content after a result has subsequently been reconciled.

### P1 — dual WAITING authority

For stores implementing `load_committed_tool_result()`, the fresh execution path now treats:

```text
uncommitted_tool_call_ids
```

as the WAITING authority.

Legacy error-code heuristics remain only for compatibility stores without the R7-C commitment interface. A terminal committed remote error is therefore allowed to enter model context as an authoritative tool error even if its application-level error code resembles a connection error.

### P1 — missing R6 authority fail-open

Production UoW exposes `capability_invocations`. If that repository exists but a claimed capability-backed result has no matching invocation, the result remains `PROVISIONAL`.

Safe results generated before CapabilityRuntime dispatch are marked explicitly:

```text
r7_commit_authority = AGENT_PRE_DISPATCH
```

Examples include policy denial, capability-not-found, validation failure, pre-start timeout, and Agent tool-budget rejection.

Compatibility-only stores that do not expose R6 authority preserve legacy immediate-commit behavior so older isolated tests/adapters remain supported.

## Exact implementation surfaces

### `se/src/runtimes/agent/adapters/tool.py`

- tags safe pre-dispatch outcomes with `AGENT_PRE_DISPATCH`;
- does not apply the marker to arbitrary post-dispatch exceptions.

### `se/src/runtimes/agent/persistence.py`

- validates existing tool-call identity;
- validates existing tool-result identity;
- rejects conflicting COMMITTED content;
- distinguishes absent R6 repository from missing invocation in an available R6 repository;
- promotes exact R6 terminal projections;
- canonicalizes legacy committed tool messages during resume;
- preserves checkpoint-directed active-batch ordering.

### `se/src/runtimes/agent/runtime.py`

- validates committed record identity against the current request;
- blocks provisional results from both fresh and resumed model context;
- uses commitment state as WAITING authority when available;
- preserves canonical parallel result order.

### `se/src/infrastructure/storage/repositories/agent.py`

- provides transactional result promotion/update support used by DurableAgentStore.

## Regression proof

`se/tests/architecture/test_r7_c_tool_result_commitment.py` now proves:

1. PROVISIONAL rows cannot be loaded for model use.
2. R6 TERMINAL_COMMITTED promotes exact terminal output once.
3. Checkpoint reconstruction strips provisional messages.
4. Parallel ordering follows `tool_call_ids`.
5. Active-batch messages are materialized exactly once.
6. Legacy transcript provisional messages are removed.
7. Mixed reused/new resumed results preserve original parallel ordering.
8. Reused tool_call_id with a different invocation/iteration is rejected.
9. Conflicting COMMITTED payload is rejected.
10. Runtime committed loader rejects request identity mismatch.
11. Legacy transcript content is re-materialized from exact committed payload.
12. Missing expected R6 authority remains PROVISIONAL.
13. Explicit Agent pre-dispatch outcomes can commit without an R6 invocation.
14. A terminal COMMITTED error whose code resembles a connection error does not incorrectly enter WAITING.

The Phase 5.9 compatibility fixture was also strengthened to model a complete committed durable identity.

## CI evidence

Verified on implementation HEAD `bbf4e7fd1a2a73a19cae8e3b9441bdc0a834763d`.

### Architecture Baseline

Linux:

```text
python -m pytest -q
678 passed, 1 skipped, 14 warnings in 122.55s
```

Windows client contracts:

```text
python -m pytest -q cl/tests
38 passed in 5.51s
```

### Workflow gates

All succeeded:

```text
Architecture Baseline
Phase 5.6 Exit Gate
Phase 5.7 Exit Gate
Phase 5.8 Exit Gate
Phase 5.9 Exit Gate
Phase 5.10 Exit Gate
Phase 5.11 Exit Gate
```

## Remaining boundary — intentionally not R7-C

R7-C does not implement ResumePlan or existing-invocation continuation.

The following remain frozen for later phases:

### R7-D

- normalized checkpoint load for planning;
- R6 reconciliation matrix;
- per-invocation action classification;
- invocation revision/state snapshots;
- plan fingerprint;
- capability/client readiness validation.

### R7-E

- continue or replay the same existing `CapabilityInvocation.invocation_id`;
- never recreate one logical invocation through `execute_capability(existing_id)`.

### R7-F

- durable ResumeClaim consume;
- atomic WAITING -> RUNNING;
- TaskBudget coupling;
- reconciliation-to-claim TOCTOU revalidation;
- request-level resume idempotency.

Until R7-D/E/F land, unresolved remote continuation must remain fail-safe and must not be interpreted as safely claimable merely because R7-C can reconstruct its context.

## Exit decision

R7-C satisfies its frozen exit gate:

```text
OUTCOME_UNKNOWN never enters model transcript as committed
reconciled terminal promotes exact result once
parallel reconstruction preserves original call order
semantic collisions fail closed
legacy tool payloads are canonicalized from committed durable authority
```

**R7-C is CLOSED / VERIFIED.**
