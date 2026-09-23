# AE-R9-A Implementation — Representation and Durable Admission

## Scope

R9-A freezes RETRY representation only. It adds the immutable `RetryPlan` and
`RetryAdmission` contracts, the `agent_task_retry_admissions` receipt, the
linear `15a_r9_retry_admission` migration, repository lookup/save primitives,
and the task-scoped CREATED ResumeClaim lock query.

## Storage contract

- Logical identity: `(task_id, retry_request_id)`.
- `execution_id` is unique.
- The receipt is immutable after commit.
- `retry_request_id` is excluded from the semantic plan fingerprint so the
  same identity with changed semantics can be rejected.
- Migration ancestry remains `14d_r8_fork_runtime_seed -> 15a`.

## Verification plan

- Contract immutability and deterministic fingerprint tests.
- SQL constraints/index representation tests.
- Repository round-trip and task-scoped claim-lock tests.
- Empty upgrade/downgrade migration test.

R9-A deliberately exposes no runtime RETRY behavior.
