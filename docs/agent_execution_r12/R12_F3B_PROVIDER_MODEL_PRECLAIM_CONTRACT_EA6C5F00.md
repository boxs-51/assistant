# AE-R12-F3-B — Provider/Model Progression PRE-CLAIM Contract Freeze

**Repository:** `boxs-51/assistant`  
**Primary workspace:** Issue #107  
**Policy:** Issue #85 v2.5  
**Stable development baseline:** `main@ea6c5f00a9432a89275d9cc4b9b52b8f992d0f12`  
**Current canonical main at replacement audit:** `ab20305b02ac0d2ac6af93e7c53de9464962f8a3`  
**Inbound main movement:** landed CTX-F5-3I-B3 PR #207, contract/evidence-only, independently classified NON_MATERIAL to F3-B  
**Class:** CONTRACT / EVIDENCE / ARCHITECTURE-TEST ONLY  
**Production delta:** ZERO

## Status

```text
R12-F3-A = LANDED / CANONICAL / HEALTHY
PR #202 = MERGED / SQUASH
IW-2026-10-02-06 = COMPLETE
post-merge Architecture #1882 / 37021044586 = GREEN/GREEN

R12-F3-B PRE-CLAIM = REPLACEMENT CONTRACT CANDIDATE
R12-F3-B production CLAIM = CLOSED
production branch/PR = NONE
merge authority = NONE
```

This replacement incorporates the independent PRE-CLAIM decisions recorded on
Issue #107 / PR #208 after exact source inspection. It releases no production
implementation.

## 1. Bounded F3-B stage

F3-B is a **dormant/service-level safe-continuation slice**.

It owns the safe handoff from the already activated recovery owner through the
next model iteration. It does **not** activate automatic crash-recovery
scheduling.

```text
F3-B owns
  RecoveryPlan + exact RecoveryActivationResult
  -> F3-A exact ordered COMMITTED TOOL-result batch
  -> durable recovery transcript handoff exactly once
  -> fresh next iteration
  -> provider physical-attempt recovery fence
  -> post-provider stale-owner fence

F3-B does not own
  stale-lease scheduling
  background recovery workers
  scanner -> planner -> activation orchestration
  deployment/startup wiring
  multi-worker recovery-vs-resume arbitration
```

Those control-plane activation/race concerns remain for later R12 stages,
including R12-G.

No `se/src/main.py`, `ApplicationContainer`, stale-lease scanner or
scheduler production change belongs to F3-B.

## 2. Frozen old-inference disposition

The recovered active TOOL batch belongs to an inference that has already
happened.

R12-F1 already freezes:

```text
active TOOL recovery cut
+ durable old inference_request_id
=> RecoveryInferenceDisposition.NO_INFERENCE
```

Therefore F3-B MUST NOT replay or resume the old inference request.

After F3-A has a complete ordered COMMITTED batch, F3-B may advance only to a
**fresh next iteration** and a **new inference_request_id**.

The current fail-closed rule also remains:

```text
non-null recovery iteration
+ no proven active TOOL batch
=> RECOVERY_INFERENCE_CUT_UNPROVEN
=> DEFER
```

## 3. Durable F3-A -> F3-B handoff ordering

The exact handoff is frozen as follows:

1. consume the same exact `RecoveryPlan` and `RecoveryActivationResult`
   already proven by F2/F3-A;
2. require `plan.inference_disposition == NO_INFERENCE`;
3. require one exact canonical COMMITTED F3-A result for every
   `plan.ordered_tool_call_ids` entry, in exact order;
4. require the base transcript to be exactly `plan.transcript_snapshot`;
5. under a fresh exact R12 fence, build
   `plan.transcript_snapshot + recovered tool-result messages` exactly once;
6. durably commit that recovery handoff/checkpoint authority **before** minting
   the next inference request identity;
7. set the next Agent iteration to exactly `plan.iteration + 1`;
8. mint a new inference request identity; never reuse
   `plan.inference_request_id`;
9. continue the active execution budget; never reset/remint/release it by
   implication.

Crash semantics:

```text
crash before durable handoff commit
=> reconstruct from canonical F3-A COMMITTED results
=> append recovered tool messages once

crash after durable handoff commit
=> durable handoff is authoritative
=> do not append the same recovered tool messages again
```

Passing arbitrary `initial_tool_results` to generic `AgentRuntime.execute()`
is not sufficient by itself. Current runtime initializes
`latest_tool_results` from that parameter but does not automatically append
those results to transcript unless the dedicated resumed-tool path runs.

### 3.1 Atomicity requirement

Current `DurableAgentStore.update_checkpoint()` performs an ordinary
load/update/commit and is not bound atomically to exact recovery
owner/generation/lease-expiry authority.

A separate
`has_active_execution_lease_fence(...) -> update_checkpoint(...)`
sequence is forbidden because lease ownership can move between the read and
the durable write.

F3-B therefore requires one existing-schema atomic store operation that, in one
UoW:

1. locks/loads the AgentExecution row;
2. proves:
   - state == RUNNING;
   - owner == F2 activation owner;
   - lease_generation == F2 activation generation;
   - lease_expires_at == exact F2 activation expiry;
   - current wall clock is still before that exact expiry;
3. proves the expected recovery checkpoint/handoff identity has not already
   advanced incompatibly;
4. writes the exact recovered transcript handoff;
5. commits once.

Existing R12-F3-A persistence already contains a transaction-scoped
`_lock_recovery_projection_fence_in_uow(...)` /
`_require_recovery_projection_fence_now(...)` pattern. F3-B should reuse that
authority style rather than creating a weaker pre-check.

No schema/Alembic migration is authorized or currently required.

## 4. Physical provider-attempt fence

Independent audit accepts this canonical send seam:

```text
ChatExecutionHandler.execute_with_fallback(...)
  -> ProviderExecutor.execute(...)
  -> RetryPolicy.apply(execution_func, ...)
  -> execution_func()
       -> recovery pre-attempt guard
       -> provider.chat.chat(...)
```

The exact recovery guard remains bound to:

```text
execution.state == RUNNING
execution.owner_instance_id == activation.activation_owner_instance_id
execution.lease_generation == activation.lease_generation
execution.lease_expires_at == activation.lease_expires_at
validation_now_utc < execution.lease_expires_at
```

The guard MUST execute before every physical send, therefore again for every
AE-R10 retry attempt and every fallback provider executor invocation.

Ordinary non-recovery inference must preserve existing behavior when no guard is
present.

## 5. Dedicated authority-loss exception semantics

Recovery guard rejection is control-plane authority loss, **not** provider
failure.

The production implementation must use one dedicated non-`ProviderError`
exception classification with these properties:

- bypass provider circuit-breaker failure accounting;
- bypass `wrap_provider_exception`;
- consume no retry token;
- trigger no provider fallback;
- preserve the original authority-loss classification to the recovery owner;
- leave `provider_attempted == false` when guard loss occurs before physical
  send.

For budgeted calls, `await_with_provider_deadline(...)` must not overwrite a
completed authority-loss exception with `ProviderDeadlineExceededError` if
the logical deadline crosses while/after the guard completes.

The accepted implementation shape is:

```text
run_attempt(...)
  -> await recovery_pre_attempt_guard()
  -> mark provider_attempted
  -> provider.chat.chat(...)

await_with_provider_deadline(...)
  -> if child completed with dedicated authority-loss error:
       re-raise authority-loss before post-completion deadline rewriting

ProviderExecutor.execute(...)
  -> dedicated except authority-loss:
       raise unchanged
       no breaker.on_failure()
```

UBQ-4 admission happens before physical provider dispatch. Pre-send R12
authority loss MUST NOT invent a quota refund/release lifecycle. Existing UBQ
reservation remains under canonical UBQ semantics.

Required red-first production evidence:

- first-attempt guard loss -> zero provider send, zero breaker failure, zero
  retry/fallback;
- retry-attempt guard loss -> prior real attempt truth preserved, no next send;
- fallback-attempt guard loss -> prior provider truth preserved, no fallback
  send;
- guard-loss classification survives logical-deadline wrapping unchanged.

## 6. Ambiguous provider outcome — final F3-B decision

The independent PRE-CLAIM decision is frozen:

**F3-B does not add a durable provider-attempt/outcome ledger.**

Safe bounded behavior is:

```text
physical provider send may have happened
+ Agent inference_response is not durably committed
=> external outcome is not replay authority
=> RECOVERY_INFERENCE_CUT_UNPROVEN
=> DEFER
=> NO BLIND REPLAY
```

F3-B deliberately sacrifices liveness for this ambiguous cut. A future
separately audited stage may add durable provider-outcome reconciliation.

F3-B therefore does not claim automatic recovery of an in-flight inference. It
owns only safe next-iteration progression while exact recovery ownership
remains live.

## 7. Post-provider truth versus stale Agent progression

Provider/AE-R10/UBQ-4 truth inside `ChatExecutionHandler` may complete after a
physical provider attempt has started. F3-B must not roll that truth back.

CAS-F7-S also owns generated-media canonicalization after provider success.
Canonical order remains:

```text
provider success
-> UBQ-4 settlement
-> CAS generated-media canonicalization/finalization
-> return response to ProviderInferenceAdapter / AgentRuntime
```

Once `ProviderInferenceAdapter.complete()` returns to recovery-owned
`AgentRuntime`, one fresh exact R12 fence is mandatory **before the first
recovery-owner semantic/accounting mutation**, including at least:

- `TaskBudgetService.account_usage`;
- `INFERENCE_COMPLETED` publication;
- transcript append;
- `context.usage` progression;
- durable inference request+response checkpoint;
- iteration progression / terminalization;
- new tool-call construction;
- TOOL_REQUESTED / TOOL_STARTED publication;
- tool-call persistence;
- TaskBudget tool reservation/admission;
- external tool dispatch.

`TaskBudgetService.account_usage()` is compatibility/read-model state after
UBQ-4 cutover. It receives **no stale-owner cleanup exemption**.

If exact R12 authority is lost after provider/UBQ/CAS finalization but before
AgentRuntime progression:

```text
preserve provider / AE-R10 / UBQ-4 / CAS truth
skip stale-owner TaskBudget and Agent progression
fail closed
do not release/remint/reset quota, TaskBudget or lease authority
```

## 7.1 CAS-F5-D pre-send side-effect boundary — Option A

The CAS bilateral owner/auditor decision for the first F3-B slice is **Option A**.

Current canonical non-stream ordering includes an asset projection step before
the provider executor:

```text
capability eligibility
-> _asset_attempt_requires_projection(body)
-> _project_asset_attempt(...)
   -> CanonicalAssetHydrationService.hydrate(...)
   -> durable provider-binding PROCESSING claim
   -> object-store read
   -> provider.files.upload_file_outcome(...)
   -> provider-binding finalization
-> ProviderExecutor.execute(...)
-> recovery pre-attempt guard
-> provider.chat.chat(...)
```

Therefore the later `ProviderExecutor.execution_func()` guard cannot authorize
asset-bearing recovered inference: CAS provider-file upload may already have
happened.

For bounded F3-B the normative rule is:

```text
recovery guard present
+ request requires canonical-asset projection
=> FAIL CLOSED / DEFER BEFORE _project_asset_attempt()
=> ZERO CanonicalAssetHydrationService.hydrate()
=> ZERO provider.files.upload_file_outcome()
=> ZERO new/updated provider binding from this recovered inference
```

Ordinary non-recovery F5-D projection/hydration remains unchanged.

F3-B receives no authority to thread R12 lease fencing into CAS hydration or
provider-file upload. Any future recovered asset-bearing inference is a
separate MATERIAL CAS expansion requiring a new bilateral re-freeze.

Required production evidence:

- recovery authority/path plus canonical assets => DEFER before projection;
- zero `_project_asset_attempt()` call;
- zero `upload_file_outcome()` side effect;
- zero new/updated provider binding from this recovered inference;
- ordinary non-recovery canonical-asset projection remains unchanged.

## 7.2 Streaming scope — explicitly CLOSED

F3-B recovery provider progression is **NON-STREAM ONLY**.

```text
ChatExecutionHandler.execute_with_fallback = bounded F3-B overlap
ChatExecutionHandler.stream_with_fallback = OUT OF SCOPE / UNCHANGED
ProviderExecutor.execute_stream = OUT OF SCOPE / UNCHANGED
GeneratedAssetStreamAssembler.observe = UNCHANGED
GeneratedAssetStreamAssembler.finalize = UNCHANGED
GeneratedAssetStreamAssembler.media_seen = UNCHANGED
stream generated-media withholding/fallback-terminal semantics = UNCHANGED
```

`ProviderInferenceAdapter.complete()` is the only F3-B provider entrypoint.
F3-B does not add a streaming inference path or a recovery guard to streaming.

Any future recovery support for streaming is a separate MATERIAL CAS overlap
and requires a new bilateral #107 <-> #74 PRE-CLAIM freeze.

## 7.3 CAS bilateral repair status

```text
P1-CAS-R12-F3B-F5D-PRESEND-SIDE-EFFECT-1
  CAS decision = OPTION A / NO ASSET-BEARING RECOVERED INFERENCE
  owner contract repair = APPLIED
  independent CAS closure = PENDING

P1-CAS-R12-F3B-STREAM-SCOPE-2
  CAS decision = NON-STREAM ONLY
  owner contract repair = APPLIED
  independent CAS closure = PENDING
```

No CAS authority transfers to R12.

## 8. Cross-track authority

### UBQ-5 / Issue #147

Current UBQ-5A/T-0 evidence work is NON_MATERIAL to this contract.

Future F3-B provider/model production overlaps materially with UBQ-5B and may
overlap UBQ-5C. Fresh bilateral PRE-CLAIM is mandatory before overlapping
production mutation.

### CAS / Issue #74

CAS-F7-S is LANDED / CANONICAL / HEALTHY.

F3-B's exact production set includes
`se/src/provider/handlers/chat_handler.py`, which is a direct canonical
CAS-F7-S seam. Therefore fresh bilateral #107 <-> #74 PRE-CLAIM confirmation
is mandatory before F3-B production CLAIM.

F3-B may thread recovery authorization only. It may not reorder or redefine
UBQ settlement, generated-media canonicalization, F7-T/F8/#166, destructive
lifecycle, GC or provider-cleanup authority.

### CTX / Issue #15

CTX-F5-3I-B3 PR #207 is now LANDED at current main
`ab20305b02ac0d2ac6af93e7c53de9464962f8a3`.

Its delta is contract/evidence-only and independently classified NON_MATERIAL
inbound to this F3-B contract. No R12 authority transferred.

### AE-R10

AE-R10 remains canonical for one logical call budget, deadline, retry, fallback
and provider error truth. F3-B adds authorization fencing only.

## 9. Exact production file set proposed for PRE-CLAIM release

The replacement contract freezes this exact maximum production set:

```text
se/src/runtimes/agent/recovery_execution.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/contracts/inference.py
se/src/runtimes/agent/adapters/inference.py
se/src/provider/handlers/chat_handler.py
se/src/provider/executor.py
se/src/provider/exceptions.py
```

Rationale:

- `recovery_execution.py`: exact F3-A authority/result handoff;
- `runtime.py`: recovery continuation, next iteration, post-provider fence;
- `persistence.py`: atomic exact-lease durable handoff/checkpoint;
- `contracts/inference.py`: non-persisted typed pre-attempt guard carrier;
- `adapters/inference.py`: thread guard into canonical provider handler;
- `chat_handler.py`: preserve UBQ/CAS order while threading guard to executor;
- `executor.py`: per-physical-attempt guard and deadline/breaker bypass;
- `provider/exceptions.py`: dedicated non-ProviderError authority-loss class.

Explicitly excluded:

```text
se/src/main.py
se/src/application/container.py
stale-lease scanner/scheduler
schema/Alembic migrations
new provider-outcome ledger
new quota/refund lifecycle
```

The addition of `persistence.py` relative to the auditor's prior likely
maximum is intentional and must receive fresh independent approval: ordinary
`update_checkpoint()` is not atomic with the R12 exact lease fence.

Any production path outside the eight files above invalidates this PRE-CLAIM
scope and requires a new audit before mutation.

## 10. P1 replacement disposition

```text
P1-R12-F3B-ORCHESTRATION-HANDOFF-1
  owner contract repair = APPLIED
  atomic persistence scope addition = NEEDS INDEPENDENT APPROVAL
  independent closure = PENDING

P1-R12-F3B-PER-ATTEMPT-LEASE-FENCE-2
  owner contract repair = APPLIED
  independent closure = PENDING

P1-R12-F3B-INFERENCE-AMBIGUOUS-OUTCOME-3
  auditor decision = BOUNDED DEFER / NO-REPLAY ACCEPTED
  owner contract repair = APPLIED
  independent closure = PENDING

P1-R12-F3B-POSTPROVIDER-PROGRESSION-FENCE-4
  owner contract repair = APPLIED
  independent closure = PENDING
```

No owner self-closes an independent gate.

## 11. PRE-CLAIM release gate

F3-B production CLAIM remains CLOSED until all are true:

1. this exact two-file replacement contract/evidence candidate is GREEN/GREEN;
2. independent auditor accepts the durable handoff ordering and the
   `persistence.py` atomic-fence scope;
3. all four P1 findings receive independent closure;
4. fresh bilateral #107 <-> #74 closure confirming Option A asset-bearing
   DEFER plus NON-STREAM-only scope for the exact `chat_handler.py` boundary;
5. fresh bilateral #107 <-> #147 classification against the then-current
   UBQ-5B/5C state;
6. exact eight-file maximum production scope is accepted;
7. red-first race/evidence matrix is frozen;
8. no material canonical-main or authority drift invalidates the release.

```text
THIS PR = CONTRACT/EVIDENCE ONLY
PRODUCTION/RUNTIME/SCHEMA/MIGRATION DELTA = ZERO
F3-B production CLAIM = CLOSED
merge authority = NONE
```
