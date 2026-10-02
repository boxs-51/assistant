# AE-R12-F3-B — Provider/Model Progression PRE-CLAIM Contract Freeze

**Repository:** `boxs-51/assistant`  
**Primary workspace:** Issue #107  
**Policy:** Issue #85 v2.5  
**Exact audit baseline:** `main@ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12`  
**Class:** CONTRACT / EVIDENCE / ARCHITECTURE-TEST ONLY  
**Production delta:** ZERO

## Status

```text
R12-F3-A = LANDED / CANONICAL / HEALTHY
PR #202 = MERGED / SQUASH
IW-2026-10-02-06 = COMPLETE
post-merge Architecture #1882 / 37021044586 = GREEN/GREEN

R12-F3-B PRE-CLAIM = CONTRACT CANDIDATE
R12-F3-B production CLAIM = CLOSED
production branch/PR = NONE
merge authority = NONE
```

This candidate does not release provider/model execution changes. It freezes the
current exact-main facts and the minimum authority boundary that an independent
audit must accept before any F3-B production CLAIM.

## 1. Exact current topology

The landed F3-A executor is intentionally bounded:

```text
RecoveryPlan + RecoveryActivationResult
    -> AgentRecoveryExecutionService.execute_active_tool_batch(...)
    -> complete ordered COMMITTED ToolExecutionResult batch
    -> STOP before provider/model inference and normal Agent-loop progression
```

There is currently no production caller of
`AgentRecoveryExecutionService.execute_active_tool_batch(...)` outside tests.
R12-F1/F2/F3-A therefore exist as production services/contracts but are not yet
joined by one canonical recovery orchestration entrypoint.

Normal Agent progression is:

```text
AgentRuntime._execute_loop(...)
    -> create fresh inference_request_id
    -> persist AgentIteration.inference_request_id
    -> publish INFERENCE_REQUESTED
    -> reserve existing TaskBudget inference authority where applicable
    -> ProviderInferenceAdapter.complete(...)
    -> ChatExecutionHandler.execute_with_fallback(...)
    -> ProviderExecutor.execute(...)
    -> RetryPolicy may call the same provider attempt more than once
    -> fallback handler may move to another provider
    -> UBQ-4 inference settlement
    -> Agent transcript/checkpoint/iteration progression
```

F3-B MUST reuse this path. It MUST NOT create a second provider executor,
retry/fallback budget, inference quota lifecycle, TaskBudget lifecycle or
provider outcome vocabulary.

## 2. Frozen inference disposition

R12-F1 already freezes `RecoveryInferenceDisposition`.

For the active TOOL-batch cut used by F3-A, the exact planner requires an
existing durable `inference_request_id` for the iteration that produced the
tool calls and emits:

```text
inference_disposition = NO_INFERENCE
```

That means the inference which produced the recovered TOOL batch is already
past the inference boundary and MUST NOT be replayed.

After F3-A has produced complete committed tool results, F3-B may only prepare
those results as model-visible continuation material and then enter the normal
Agent loop at the **next iteration**, which creates a **new logical inference**.

The existing planner also deliberately fails closed for a non-null recovery
iteration whose frozen active-tool batch is empty:

```text
RECOVERY_INFERENCE_CUT_UNPROVEN -> DEFER
```

F3-B MUST preserve that rule. It does not gain authority to resume/replay an
in-flight provider request whose external outcome is ambiguous.

## 3. Recovered transcript handoff

F3-A returns ordered `ToolExecutionResult` objects but intentionally does not
append them to Agent transcript or enter `AgentRuntime`.

The F3-B handoff must prove all of the following before model progression:

1. the input `RecoveryPlan` and `RecoveryActivationResult` are the exact
   authority already consumed by F2/F3-A;
2. `plan.inference_disposition == NO_INFERENCE`;
3. every `plan.ordered_tool_call_ids` slot has one canonical committed result
   from F3-A, in exact order;
4. the starting transcript is exactly `plan.transcript_snapshot`;
5. recovered tool-result messages are appended exactly once;
6. the next Agent iteration is `plan.iteration + 1`;
7. no old `inference_request_id` is reused;
8. no synthetic ResumePlan/CLIENT_RECONNECT authority is introduced;
9. active budget is continued from the F2/F3-A authority and is not reset.

This handoff requires an explicit recovery-specific runtime entrypoint or an
equally strong typed boundary. Passing arbitrary `initial_tool_results` into
the generic Agent execution entrypoint is insufficient authority by itself.

## 4. Provider dispatch fence

R12-F0 requires exact active recovery authority immediately before **every**
externally visible provider/model/tool dispatch and before every durable
active-owner commit.

The exact active fence remains:

```text
execution.state == RUNNING
execution.owner_instance_id == activation.activation_owner_instance_id
execution.lease_generation == activation.lease_generation
execution.lease_expires_at == activation.lease_expires_at
validation_now_utc < execution.lease_expires_at
```

The current provider path has no R12 recovery guard at the physical provider
attempt seam.

An outer check in `AgentRuntime` or `ProviderInferenceAdapter` is not
sufficient. AE-R10 permits multiple retry attempts inside
`ProviderExecutor.execute(...)`, and `ChatExecutionHandler` may fall back to
another provider. Therefore the future guard must be threaded to the canonical
per-attempt seam and run immediately before each `provider.chat.chat(...)`
physical attempt. The same guard instance must remain bound to the exact F2
owner/generation/expiry tuple.

Ordinary DIRECT/Agent calls must preserve current behavior when no recovery
guard is supplied.

## 5. UBQ-4 and AE-R10 authority

UBQ-4 PR #190 is already merged/canonical/healthy before this baseline.
F3-B therefore inherits, without redefining:

- one inference admission/reservation per logical inference;
- one settlement/refund authority for that admission;
- no second quota reservation because recovery occurred;
- one AE-R10 logical provider deadline;
- one global retry-token budget across fallback;
- provider transition is not a retry;
- no retry/fallback after a terminal streaming visibility boundary where
  applicable;
- provider/deadline/cancellation failure truth remains owned by AE-R10.

A recovery attempt guard is an **authorization fence**, not a new retry policy,
quota decision or provider failure. Failure of the guard must stop a new
provider attempt without being rewritten as an ordinary provider failure that
triggers retry/fallback.

## 6. Post-send crash / ambiguous inference outcome

Current durable Agent iteration storage can hold
`inference_request_id`, `inference_request` and `inference_response`.
However the normal Agent path persists the request id before provider execution
and writes request+response checkpoint material only after
`ProviderInferenceAdapter.complete(...)` returns.

Therefore this window exists today:

```text
durable inference_request_id exists
-> physical provider attempt starts
-> process/owner dies
-> durable Agent inference_response is not yet committed
```

R12 currently has no provider-side analogue of the R6 CapabilityInvocation
remote-outcome state machine proving `NOT_DISPATCHED / IN_FLIGHT /
OUTCOME_UNKNOWN / TERMINAL` for that inference.

F3-B MUST NOT convert that ambiguity into permission to replay the provider
request. Until separately proven, the existing F1
`RECOVERY_INFERENCE_CUT_UNPROVEN` fail-closed DEFER behavior remains the
authority for an inference-only recovery cut.

This PRE-CLAIM candidate does not decide that a durable provider-outcome ledger
must be added in F3-B. It freezes the decision gate: an independent audit must
either:

- accept a bounded F3-B slice whose post-send crash behavior is explicitly
  DEFER/no-replay and leave broader liveness to a later audited stage; or
- require durable provider-attempt/outcome authority before F3-B production
  CLAIM.

No owner implementation may silently choose between those alternatives.

## 7. Post-provider accounting vs stale Agent progression

Lease loss after a provider attempt has begun must not erase real provider/UBQ
truth. At the same time, a stale recovery owner must not advance Agent state.

A production design must therefore distinguish:

```text
provider/UBQ/R10 attempt truth + required settlement
    !=
permission for stale owner to append transcript,
persist Agent checkpoint/iteration progression,
create tool calls,
or terminalize the AgentExecution
```

The audit must identify the exact mutation seams that require a fresh R12 fence
after provider completion and before Agent-visible durable progression.

## 8. PRE-CLAIM blocking findings

### P1-R12-F3B-ORCHESTRATION-HANDOFF-1 — OPEN

There is no canonical production orchestration joining F2 activation, F3-A
active-tool execution and F3-B next-iteration progression. The exact transcript,
ordered tool-result and supervisor ownership handoff must be frozen.

### P1-R12-F3B-PER-ATTEMPT-LEASE-FENCE-2 — OPEN

The provider path has no exact R12 active-fence callback at each physical
provider attempt. Outer-only checking is insufficient under AE-R10 retry and
fallback.

### P1-R12-F3B-INFERENCE-AMBIGUOUS-OUTCOME-3 — OPEN / DECISION REQUIRED

A crash after provider send but before durable Agent response commitment has no
R6-equivalent provider outcome ledger. No blind replay is allowed. Auditor must
decide whether bounded DEFER/no-replay is sufficient for F3-B or whether this
blocks production CLAIM.

### P1-R12-F3B-POSTPROVIDER-PROGRESSION-FENCE-4 — OPEN

Provider/UBQ truth after a started attempt must be preserved while stale-owner
Agent transcript/checkpoint/tool-admission/terminal progression is forbidden.
Exact post-provider mutation fences are not yet implemented.

## 9. Cross-track classification

### UBQ-5 / Issue #147

Current T-0 PR #203 is contract/evidence-only and has zero production delta.
Future UBQ-5 provider/tool timeout production remains MATERIAL to the provider
attempt/settlement seams above. Bilateral PRE-CLAIM is required before either
track edits overlapping production seams.

### CTX / Issue #15

Current CTX-F5-3I-B3 PR #207 is contract/evidence-only with zero production
runtime/schema/migration delta. No CTX authority transfers to R12-F3-B.

### AE-R10

AE-R10 remains canonical. F3-B may add authorization fencing but must not
redefine retry/fallback/deadline/error semantics.

## 10. Candidate production surface for auditor review

This is a **candidate blast radius**, not a CLAIM:

```text
se/src/runtimes/agent/recovery_execution.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/contracts/inference.py          [only if typed guard carrier is required]
se/src/runtimes/agent/adapters/inference.py
se/src/provider/handlers/chat_handler.py
se/src/provider/executor.py
composition/control-plane wiring                      [exact file must be audited]
targeted unit/integration/architecture tests
```

The auditor may narrow or reject this surface. No schema/Alembic migration is
currently justified.

## 11. Release gate

Production F3-B remains CLOSED until all of the following are true:

1. independent PRE-CLAIM audit against exact current main;
2. disposition for all four P1 findings above;
3. fresh bilateral classification with UBQ-5 if provider seams overlap;
4. exact bounded production file set;
5. red-first race tests for per-attempt lease loss, retry/fallback, post-send
   ambiguity and post-provider stale-owner progression;
6. exact-head Linux + Windows Architecture GREEN/GREEN on the future production
   candidate;
7. no new blocking P0/P1, authority conflict or canonical-main material drift.

```text
THIS PR = CONTRACT/EVIDENCE ONLY
F3-B production CLAIM = CLOSED
merge authority = NONE
```
