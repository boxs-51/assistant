# R7-A Completion — Representation + Migration

## Baseline

- Repository: `boxs-51/assistant`
- R7-A commit audited: `5fe15ee5c7490d005f58e482ba5919645995dfd0`
- Phase: `R7-A Representation + Migration`
- Status: **CLOSED / EXIT GATE PASSED**

## Applied scope

R7-A introduced the normalized durable representation required by R7 without changing resume authority:

- `AgentExecution.current_checkpoint_id`
- `AgentExecution.bound_client_id`
- `AgentExecution.bound_connection_id`
- `agent_execution_checkpoints`
- `agent_checkpoint_pending_invocations`
- `agent_resume_claims`
- `AgentToolResult.commit_state`
- immutable R7 checkpoint / ResumeClaim contracts
- repository CRUD/CAS primitives
- Alembic revision `13a_r7_durable_resume`

R7-A intentionally did **not** switch the runtime to normalized checkpoint writing and did not replace legacy branch/merge resume behavior.

## User-verified exit evidence

```text
patch_applier --check: PASS
patch apply:             12/12 files PASS
R7-A focused gate:       5 passed
R6/R5 regression gate:  26 passed
full repository suite:   665 passed
warnings:                11 deprecation warnings only
```

The warnings are unrelated to R7 authority or persistence correctness (Alembic path separator, Starlette/AnyIO, Argon2 metadata, HTTP status alias deprecations).

## Closure assessment

R7-A is closed as a representation/migration phase. One contract-completeness gap was discovered while entering R7-B: the pending-invocation child representation lacks the complete R6 semantic watermark frozen in R7-0 v3 (`capability_version`, `request_fingerprint`, `idempotency`, `observed_remote_outcome_state`, `origin_client_id`, `origin_connection_id`). Because `13a` has already been applied, R7-B must repair this forward with revision `13b`; `13a` must not be rewritten.

## Authority after R7-A

R7-A is representation-only. These remain true until later phases:

- legacy `context_state["continuation"]` is still readable/writable;
- `ContinuationBranch/reconnect/confirm_merge` are still active compatibility behavior;
- normalized checkpoints are not yet the canonical WAITING writer;
- ResumeClaim is representation-only and not yet the resume authority;
- R6 `CapabilityInvocation` remains the remote-side-effect authority.

## Next phase gate

R7-B is allowed to begin only with the following invariant:

> A canonical RUNNING -> WAITING transition must atomically commit the normalized checkpoint, every unresolved pending-invocation snapshot, AgentExecution revision/state/current_checkpoint_id, and TaskBudget active-slot release in one SQL transaction. Any failure rolls back all of them.
