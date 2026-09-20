# Phase 5.9 Task Checklist

## Objective

Close Phase 5.9 by adding durable execution-plane persistence and resumable loop state for agent iterations and tool execution, without mixing persistence concerns into the runtime orchestration layer.

## Canonical scope

Phase 5.9 covers durable persistence and resume semantics for the execution plane:
- `agent_executions`
- `agent_iterations`
- `agent_tool_calls`
- `agent_tool_results`

It must preserve the separation between:
- control-plane persistence (sessions/tasks/executions)
- execution-plane persistence (iteration/tool state)
- runtime orchestration logic (loop execution, retries, cancellation)

## Checklist

### A. Execution-plane persistence model
- [ ] Add model/table for `agent_iterations`
- [ ] Add model/table for `agent_tool_calls`
- [ ] Add model/table for `agent_tool_results`
- [ ] Link each iteration to an execution record
- [ ] Link each tool call/result to the owning iteration and execution
- [ ] Persist stable IDs: execution_id, iteration_id, invocation_id, tool_call_id
- [ ] Persist timestamps and terminal status fields

### B. Durable execution state
- [ ] Persist execution lifecycle state (`CREATED`, `RUNNING`, `WAITING_TOOL`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`)
- [ ] Persist current iteration number and loop state
- [ ] Persist final inference request/response summary when applicable
- [ ] Persist tool call inputs and outputs with metadata
- [ ] Persist retry/cancellation state necessary for resume

### C. Resume capability
- [ ] Implement `load_execution()`
- [ ] Implement `load_iteration()`
- [ ] Implement `resume_execution()`
- [ ] Resume from last durable iteration instead of starting over blindly
- [ ] Reconstruct pending tool calls from persisted state
- [ ] Rehydrate execution context from persisted state without losing correlation metadata
- [ ] Ensure resume does not duplicate already-committed tool results

### D. Runtime integration
- [ ] Agent runtime persists iteration before/after inference
- [ ] Agent runtime persists tool call before dispatch and result after completion
- [ ] Runtime records success/failure state atomically
- [ ] Runtime persists cancellation/timeouts with explicit status
- [ ] Runtime recovers from persisted execution on restart or resume path

### E. Correctness and idempotency
- [ ] Repeated resume of the same execution does not create duplicate rows
- [ ] Same `invocation_id` reused after resume remains idempotent
- [ ] Persisted tool results are not re-executed if already committed
- [ ] Resume respects original execution_id, iteration, and correlation data
- [ ] Terminal states prevent re-entry or double completion

### F. Testability and release gate
- [ ] A canonical Phase 5.9 exit-gate doc exists
- [ ] A dedicated gate test file exists for 5.9
- [ ] CI workflow runs full suite + Phase 5.9 gate
- [ ] Legacy docs point to the canonical Phase 5.9 gate

## Test plan

### P0: persistence schema and record linkage
1. `test_agent_execution_model_persists_expected_fields`
   - Assert: `agent_executions` stores core execution metadata and state

2. `test_agent_iteration_record_has_execution_foreign_key`
   - Assert: each iteration references a parent execution

3. `test_agent_tool_call_record_has_iteration_and_execution_link`
   - Assert: invocation/tool metadata is attached to correct execution and iteration

4. `test_agent_tool_result_record_matches_tool_call_context`
   - Assert: result references correct tool call, invocation, execution

### P0: resume and idempotency
5. `test_resume_execution_rehydrates_latest_iteration`
   - Assert: resumed execution loads the correct iteration and loop state

6. `test_resume_execution_does_not_duplicate_committed_tool_results`
   - Assert: repeated resume does not create duplicate committed rows

7. `test_resume_after_timeout_restarts_from_persisted_checkpoint`
   - Assert: execution resumes from durable checkpoint without losing correlation state

8. `test_resume_after_tool_failure_persists_failure_and_retry_state`
   - Assert: failed tool calls and retry markers are stored durably

### P1: runtime persistence integration
9. `test_agent_runtime_persists_iteration_before_and_after_inference`
   - Assert: iteration record exists before and after inference lifecycle

10. `test_agent_runtime_persists_tool_calls_and_results`
   - Assert: tool invocation and result rows are created atomically

11. `test_agent_runtime_persists_cancellation_or_timeout_state`
   - Assert: terminal state is stored durably

### P1: gate readiness
12. `test_E5_ci_declares_full_suite_and_phase_5_9_gate_as_blocking_checks`
   - Assert: workflow includes both full suite and 5.9 gate

13. `test_E7_phase_5_9_status_document_is_canonical_and_legacy_doc_points_to_it`
   - Assert: canonical gate doc is the source of truth and legacy docs point to it

## Exit criteria for Phase 5.9

Phase 5.9 is ready to close when all items above are checked, the dedicated gate test file passes, and CI confirms the full suite + Phase 5.9 gate still pass together.

## Implementation order

1. Add execution-plane persistence models/tables
2. Add durable iteration and tool-call/result records
3. Add `load_execution()`, `load_iteration()`, `resume_execution()` APIs
4. Integrate runtime save points into execution loop and tool execution
5. Validate idempotency and resume semantics with failing tests
6. Add canonical exit gate doc + CI workflow
7. Run full suite and gate tests together
8. Close Phase 5.9 and open Phase 5.10
