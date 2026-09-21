# R7-C — Tool Result Commitment + Checkpoint-Directed Reconstruction

## Baseline

- Repository: `boxs-51/assistant`
- Audited baseline: `24f3c5fc808729cbe714e13650c37262bccfef48`
- Implementation branch: `r7-c-tool-result-commitment`
- Depends on: R7-B CLOSED
- Status: **IMPLEMENTED / VERIFICATION PENDING**

## Goal

R7-C establishes one hard model-context rule:

> A tool result is model-consumable only when its durable `AgentToolResult.commit_state` is `COMMITTED`.

It also changes resume reconstruction so the normalized checkpoint is the safe transcript prefix and the active parallel tool batch is re-materialized exactly once in the original `AgentIteration.tool_call_ids` order.

## Exact call-site audit

### 1. `AgentRuntime._persist_tool_result()`

Baseline behavior persisted every `ToolExecutionResult` before the runtime classified remote connection ambiguity. Because R7-A intentionally defaulted new rows to `PROVISIONAL`, this writer needed explicit commitment classification rather than relying on the SQL default.

R7-C leaves the runtime adapter thin and moves commitment authority into `DurableAgentStore.save_tool_result()`.

### 2. `DurableAgentStore.save_tool_result()`

Baseline behavior was insert-if-absent and did not inspect R6 `CapabilityInvocation`.

R7-C now loads the linked invocation in the same UnitOfWork when available and applies:

```text
no linked remote authority / local outcome
    -> COMMITTED

linked remote invocation
  remote_outcome_state != TERMINAL_COMMITTED
    -> PROVISIONAL

linked remote invocation
  remote_outcome_state == TERMINAL_COMMITTED
    -> exact R6 terminal output/error projection
    -> COMMITTED
```

Semantic identity is validated across:

- invocation_id
- capability_id
- execution_id
- tool_call_id

A conflicting existing `COMMITTED` row is never overwritten.

### 3. `AgentRuntime._load_committed_tool_result()`

Baseline behavior loaded any row by execution/tool-call identity and converted it directly to `ToolExecutionResult`.

This was the primary P0 path by which `PROVISIONAL` could be reused after restart.

R7-C now:

- prefers `DurableAgentStore.load_committed_tool_result()`;
- fails closed when a fallback store returns a row without explicit `COMMITTED`;
- never converts `PROVISIONAL` to a runtime result.

### 4. `DurableAgentStore.load_committed_tool_result()`

This new read boundary is the only persistence-level model-consumable loader.

If the row is `PROVISIONAL`:

1. load the linked R6 invocation;
2. revalidate semantic identity;
3. require `remote_outcome_state == TERMINAL_COMMITTED`;
4. replace the transport projection with R6's exact terminal output/error;
5. persist `PROVISIONAL -> COMMITTED`;
6. return the committed row.

If terminal R6 authority is absent, return `None`.

### 5. Fresh tool batch in `AgentRuntime._execute_loop()`

A second P0 existed beyond resume: baseline code persisted a result and then continued using the original in-memory `ToolExecutionResult`. That meant persistence could correctly classify a projection as `PROVISIONAL` while the in-memory object still reached the transcript.

R7-C changes the boundary:

```text
tool execution
    -> persist projection
    -> reload through committed-only loader
    -> only committed projection can enter committed_batch
    -> transcript append
```

Any uncommitted result is treated as remote-waiting evidence. If there is no durable continuation checkpoint, execution fails closed before transcript/model inference.

### 6. `DurableAgentStore.resume_execution()`

Baseline reconstruction used:

- `execution.transcript` / latest iteration transcript;
- `list_tool_calls()` ordered by row `created_at`;
- only calls whose persisted status was not `COMPLETED`.

Those are not sufficient R7 reconstruction authorities.

R7-C now prefers:

```text
AgentExecution.current_checkpoint_id
    -> AgentExecutionCheckpoint.transcript_snapshot
    -> checkpoint.iteration
    -> AgentIteration.tool_call_ids
```

It validates:

- checkpoint exists;
- checkpoint belongs to the same execution;
- checkpoint revision equals execution revision;
- checkpoint iteration exists;
- every canonical tool_call_id has a durable tool-call record.

### 7. Legacy transcript sanitization

Every reconstructed transcript is fail-closed:

- non-tool messages remain;
- tool messages without a tool_call_id are removed;
- tool messages without a durable result are removed;
- tool messages whose durable result is not `COMMITTED` are removed.

For the active batch, even already-committed tool messages are removed from the prefix and re-materialized later. This guarantees exact-once insertion and canonical ordering.

### 8. Parallel ordering authority

SQL insertion time is not an ordering authority.

The canonical order is:

```text
AgentIteration.tool_call_ids[i]
    <=> reconstructed ToolExecutionRequest[i]
    <=> reconstructed ToolExecutionResult[i]
    <=> model tool message[i]
```

`_execute_resumed_tool_calls()` combines reused committed rows and newly committed results by `tool_call_id`, then returns them in the original request order.

## Invariants implemented

### R7-C-I1 — PROVISIONAL isolation

```text
AgentToolResult.commit_state != COMMITTED
=> MUST NOT become an InferenceMessage(role="tool")
```

This applies to both restart reconstruction and the live fresh-tool path.

### R7-C-I2 — R6 terminal authority

For linked remote invocations:

```text
commit_state == COMMITTED
=> CapabilityInvocation.remote_outcome_state == TERMINAL_COMMITTED
```

The committed projection is derived from the terminal R6 payload rather than from a stale transport error projection.

### R7-C-I3 — committed immutability

```text
COMMITTED -> terminal
```

A later conflicting projection does not overwrite an existing committed row.

### R7-C-I4 — checkpoint-directed prefix

When `current_checkpoint_id` exists, reconstruction must use that checkpoint's transcript snapshot and iteration. Legacy execution transcript is not allowed to supersede it.

### R7-C-I5 — exact parallel order

Active-batch reconstruction uses `AgentIteration.tool_call_ids`, never SQL creation/completion order.

### R7-C-I6 — exact-once current-batch materialization

Current-batch tool messages are stripped from the checkpoint/legacy prefix and re-added once after committed reconstruction.

## Files changed

1. `docs/agent_execution_r7/R7_B_COMPLETION.md`
2. `se/src/infrastructure/storage/repositories/agent.py`
3. `se/src/runtimes/agent/persistence.py`
4. `se/src/runtimes/agent/runtime.py`
5. `se/tests/architecture/test_phase5_9_exit_gate.py`
6. `se/tests/architecture/test_r7_c_tool_result_commitment.py`
7. this document

## Regression coverage added

`test_r7_c_tool_result_commitment.py` proves:

1. OUTCOME_UNKNOWN persists as PROVISIONAL and cannot be loaded for model consumption.
2. A later R6 TERMINAL_COMMITTED state promotes the exact authoritative payload once.
3. Checkpoint reconstruction sanitizes provisional tool messages.
4. Checkpoint reconstruction ignores SQL insertion order and follows `tool_call_ids`.
5. Active-batch messages are removed from the prefix for exact-once materialization.
6. Legacy transcripts also remove uncommitted tool messages.
7. Mixed reused/new resumed results are returned in the original parallel call order.

Existing Phase 5.9 fake committed-result fixtures were updated to declare `commit_state="COMMITTED"` explicitly so they continue to represent their intended semantics.

## Deliberately deferred to R7-D / R7-E / R7-F

R7-C does **not** make ordinary resumed dispatch safe for an unresolved remote invocation. It only guarantees that such a provisional outcome cannot reach model context.

The next phases still own:

- R7-D: R6 reconciliation matrix and immutable ResumePlan;
- R7-E: continue/replay the same `CapabilityInvocation.invocation_id` without recreating logical invocation identity;
- R7-F: atomic ResumeClaim consume + WAITING -> RUNNING + TOCTOU revalidation.

Until those phases land, unresolved remote work must not be considered claim-safe merely because R7-C can reconstruct it.

## Verification gate

Run:

```powershell
py -m pytest -q `
  se/tests/architecture/test_r7_c_tool_result_commitment.py `
  se/tests/architecture/test_r7_b_atomic_waiting.py `
  se/tests/architecture/test_phase5_9_exit_gate.py

py -m pytest -q `
  se/tests/architecture/test_phase5_runtime.py `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  se/tests/architecture/test_capability_invocation_lifecycle.py

py -m pytest -q se/tests tools cl/tests
```

This branch has no GitHub workflow/status result at the time of this implementation review, and the current tool environment cannot execute the repository locally. R7-C must therefore remain **IMPLEMENTED / VERIFICATION PENDING** until these commands are green.
