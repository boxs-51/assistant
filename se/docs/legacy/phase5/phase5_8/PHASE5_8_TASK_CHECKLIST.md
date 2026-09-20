# Phase 5.8 Task Checklist

## Objective

Close Phase 5.8 by enforcing bounded parallel tool execution, stable ordered results, retry policy compliance, and cancellation-safe batch execution without introducing a second concurrency authority.

## Canonical scope

Phase 5.8 is the bounded-parallel execution layer for the agent tool loop. It must preserve the canonical ToolExecutionPort boundary and keep orchestration ownership inside `AgentToolExecutionCoordinator`.

## Checklist

### A. Execution ownership and boundary
- [ ] Only `AgentToolExecutionCoordinator` owns batch orchestration
- [ ] `CapabilityToolExecutionAdapter.execute_many()` remains compatibility-only
- [ ] `AgentRuntime` does not contain parallel tool orchestration logic
- [ ] Batch scheduling must be single-authority and deterministic

### B. Bounded concurrency
- [ ] `execute_many()` validates `max_parallel >= 1`
- [ ] `max_parallel` is bounded by `context.limits.max_parallel_tools`
- [ ] A semaphore or equivalent bound is used for all batch tool executions
- [ ] Unbounded `asyncio.gather` is not used for the production batch path
- [ ] Peak concurrency is observable and testable

### C. Stable ordering
- [ ] Results are returned in the same order as input `ToolExecutionRequest`s
- [ ] Ordering is independent of task completion timing
- [ ] Duplicate request IDs are rejected before dispatch
- [ ] Duplicate downstream results are rejected
- [ ] Missing downstream result triggers a fail-closed error
- [ ] Mismatched execution_id / iteration / capability_id is rejected

### D. Retry policy compliance
- [ ] Validation error is never retried
- [ ] Authorization/visibility/tool-not-found errors are never retried
- [ ] Timeout, network, unavailable MCP, and rate-limit errors are retried only when policy says so
- [ ] `CapabilityError.retryable` remains the source of truth for the retry gate
- [ ] `max_retry_attempts` is enforced consistently across the whole execution
- [ ] Retry attempts are counted only for additional attempts, not the first execution

### E. Cancellation and timeout propagation
- [ ] Caller cancellation does not cancel a shared downstream invocation while another waiter is attached
- [ ] Execution-level cancellation cancels the shared task exactly once
- [ ] Completed shared execution is committed before waiter cleanup resolves
- [ ] Late callers reuse completed result instead of redispatching the side effect
- [ ] `context.ensure_active()` is enforced before and after retry boundaries
- [ ] Tool timeout is derived from remaining execution budget

### F. Batch validation and failure semantics
- [ ] Duplicate `tool_call_id` in the same batch is rejected
- [ ] Malformed downstream results fail closed
- [ ] Non-success results are normalized to the canonical tool error contract
- [ ] `CAPABILITY_INVALID_ARGUMENT` is never marked retryable
- [ ] Unknown exception wrapping preserves original cause metadata without reinterpreting it as a different canonical error

### G. Observability and traceability
- [ ] Batch tasks expose stable names / correlation hint
- [ ] `execution_id`, `iteration`, `invocation_id`, `tool_call_id`, `capability_id` remain attached to every result
- [ ] Completion ledger retains bounded retention at runtime
- [ ] Completed results remain traceable for idempotent reuse

### H. Gate readiness
- [ ] A canonical Phase 5.8 exit-gate doc exists
- [ ] A test file for the gate exists
- [ ] CI workflow runs full suite + exit-gate suite
- [ ] Legacy docs explicitly point to the canonical gate

## Test plan

### P1: Core bounded concurrency and ordering
1. `test_coordinator_restores_original_tool_call_order`
   - Input: 3 requests with varied delay
   - Assert: output order matches input order

2. `test_coordinator_bounds_parallel_dispatch`
   - Input: 6 requests, `max_parallel=2`
   - Assert: peak concurrent executions == 2

3. `test_coordinator_rejects_duplicate_tool_call_ids_before_dispatch`
   - Input: same `tool_call_id` with different capability
   - Assert: `ValueError` and zero dispatches

4. `test_coordinator_rejects_mismatched_downstream_result`
   - Assert: mismatched `execution_id` fails before returning

5. `test_coordinator_rejects_mismatched_result_iteration`
   - Assert: mismatched `iteration` fails before returning

6. `test_coordinator_rejects_mismatched_capability`
   - Assert: mismatched `capability_id` fails before returning

7. `test_coordinator_rejects_duplicate_downstream_results`
   - Assert: duplicate result for same request fails

8. `test_coordinator_rejects_missing_downstream_result`
   - Assert: missing result raises fail-closed error

### P1: Retry budget and policy behavior
9. `test_coordinator_capped_by_max_attempts`
   - Input: retryable result with `max_attempts=2`
   - Assert: total attempts == 2 and metadata marks attempt count

10. `test_retry_rejects_non_retryable_failure`
   - Input: `success=False`, `retryable=False`
   - Assert: no additional attempt

### P1: Cancellation and shared execution semantics
11. `test_E1_caller_cancellation_detaches_waiter_but_shared_execution_continues`
   - Assert: one waiter cancellation does not cancel shared execution while another is attached

12. `test_E2_execution_cancellation_cancels_shared_task_once_for_all_waiters`
   - Assert: one shared execution cancels once even with multiple waiters

13. `test_E3_completed_shared_execution_is_committed_even_when_last_waiter_cancels`
   - Assert: result committed before cleanup loses it

14. `test_E4_completion_or_cancellation_race_never_redispatches_same_invocation`
   - Assert: same `execution_id + invocation_id` reuses result, no duplicate side effect

15. `test_E6_completed_ledger_has_bounded_retention`
   - Assert: completed ledger count is bounded

### P2: Release gate checks
16. `test_E5_ci_declares_full_suite_and_exit_gate_as_blocking_checks`
   - Assert: workflow includes full suite and explicit gate

17. `test_E7_phase_5_6_status_document_is_canonical_and_legacy_status_declares_it`
   - Assert: canonical gate doc is referenced and stale claims are not current truth

## Exit criteria for Phase 5.8

Phase 5.8 is ready to close when all items above are checked, the dedicated gate test file passes, and CI confirms the full suite + Phase 5.8 gate still pass together.

## Implementation order

1. Write/confirm failing tests for bounded ordering and cancellation semantics
2. Enforce bounded concurrency and stable ordering in coordinator
3. Validate retry policy and budget accounting
4. Verify cancellation and idempotency invariants
5. Add canonical exit gate doc + CI workflow
6. Run full suite and gate file together
7. Close Phase 5.8 and open Phase 5.9
