# AE-R9-G Implementation — Public Control Plane

## Scope

R9-G exposes only explicit commands after the storage invariants are green:

- `POST /v1/multi-agent/tasks/{task_id}/retry`
- `POST /v1/multi-agent/tasks/{task_id}/branches/discard`
- `POST /v1/multi-agent/tasks/{task_id}/branches/adopt`
- `POST /v1/multi-agent/tasks/{task_id}/aggregate`

Pydantic request/response contracts are defined in `multi_agent.py`. The
coordinator verifies Task ownership before dispatching to the durable command.

## RETRY runtime command

The retry command first probes the immutable receipt. A committed lifecycle is
identity-only replay. `RUNNING@1` is reconstructed, locally reserved, activated
by durable CAS, then handed to `AgentRuntime`; failed post-activation handoff is
settled as CANCELLED so precharged capacity is not leaked.

## Error envelope

R9 planning, admission, activation and resolution exceptions retain their
stable `code`. The HTTP detail is `{code, message, retryable}` with ownership
mapped to 403, authority/race conflicts to 409, and invalid input to 422.

AGGREGATE is deliberately returned as an admitted durable execution and does
not implicitly run ADOPT.

## Verification plan

- Route registration and schema validation.
- Stable error-code preservation.
- Committed RETRY identity replay does not replan or reactivate.
- Coordinator dispatch for all four explicit commands.
- Existing R8 FORK control-plane regression suite.
