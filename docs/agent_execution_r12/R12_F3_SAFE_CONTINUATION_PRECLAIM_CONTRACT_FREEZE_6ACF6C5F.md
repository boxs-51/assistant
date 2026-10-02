# R12-F3 Safe Continuation PRE-CLAIM Contract Freeze

Status: **PRE-CLAIM / CONTRACT-EVIDENCE ONLY / PRODUCTION CLAIM CLOSED**

Canonical baseline:

```text
main = 6acf6c5ffc288c2b747f3ca92e86cca805ee43c7
source = merged AE-R12-F2 PR #196
Architecture #1810 / 36986054522 = GREEN/GREEN
R12-F2 = CANONICAL / COMPLETE
```

This freeze addresses the independent R12-F3 PRE-CLAIM findings:

```text
P1-R12-F3-ACTIVE-LEASE-FENCE-1
P1-R12-F3-RECOVERY-EXECUTION-SEAM-2
P1-R12-F3-POSTACTIVATION-FAILURE-AUTHORITY-3
```

No production code, schema, migration, provider dispatch, tool dispatch, or durable
state mutation is authorized by this document.

## 1. Inherited authorities

R12-F3 reuses, and does not redefine:

- R12-C execution lease owner/generation/expiry authority;
- R12-E recovery safe-point provenance;
- R12-F1 RecoveryPlan read-only reconciliation/action evidence;
- R12-F2 RecoveryActivationResult and atomic activation handoff;
- R7 ordered invocation continuation semantics;
- R6 invocation reconciliation/idempotency;
- UBQ-3 existing logical TOOL quota continuation authority;
- TaskBudget structural execution-capacity authority.

R12-F3 MUST NOT create a second continuation engine, a second ResumeClaim authority,
a second tool admission path, or a second quota lifecycle.

## 2. F3 input authority

R12-F3 consumes the exact pair:

```text
RecoveryPlan
RecoveryActivationResult
```

It MUST NOT manufacture a fake ResumePlan or ResumeClaimConsumeResult merely to enter
the CLIENT_RECONNECT execution path.

A recovery-active authority is derived only when all of the following are exact:

```text
RecoveryActivationResult.execution_id == RecoveryPlan.execution_id
RecoveryActivationResult.checkpoint_id == RecoveryPlan.checkpoint_id
RecoveryActivationResult.source_execution_revision
    == RecoveryPlan.expected_execution_revision
RecoveryActivationResult.consumed_execution_revision
    == RecoveryPlan.expected_execution_revision + 1
RecoveryActivationResult.activation_owner_instance_id is non-empty
RecoveryActivationResult.lease_generation
    == RecoveryPlan.expected_unowned_lease_generation + 1
RecoveryActivationResult.lease_expires_at is aware UTC
RecoveryActivationResult.lease_expires_at > validation_now_utc
RecoveryPlan.plan_fingerprint is the exact claim-bound F1 plan fingerprint
```

The derived recovery-active authority MUST retain at least:

```text
execution_id
checkpoint_id
consumed_execution_revision
activation_owner_instance_id
lease_generation
lease_expires_at
recovery_fingerprint
recovery_plan_fingerprint
```

This object is execution authority, not metadata decoration.

## 3. Exact active lease fence

Before every externally visible continuation dispatch and before every durable
active-owner commit, F3 MUST validate the exact active lease fence:

```text
state == RUNNING
owner_instance_id == activation_owner_instance_id
lease_generation == RecoveryActivationResult.lease_generation
lease_expires_at == RecoveryActivationResult.lease_expires_at
lease_expires_at > validation_now_utc
```

The existing DurableAgentStore.has_active_execution_lease_fence() proves active
RUNNING + owner + generation + unexpired lease. F3 implementation MUST additionally
preserve exact activation lease-expiry equality. It MAY do so by a dedicated exact
read-only predicate; it MUST NOT weaken exact-expiry authority to merely "some active
lease with the same owner/generation".

AgentExecution semantic revision equality is not a substitute for this fence.

A fence check performed once for an entire parallel batch is insufficient.

## 4. F3-A bounded production slice — recovered active TOOL batch only

The first production CLAIM MAY be limited to **F3-A**.

F3-A begins after successful F2 activation and ends after every frozen active-batch
tool slot is resolved to one canonical committed result.

F3-A MAY:

- accept exact RecoveryPlan + RecoveryActivationResult;
- load REUSE_COMMITTED slots read-only from durable canonical AgentToolResult;
- map executable RecoveryInvocationAction evidence to the existing R7 continuation
  executor boundary without creating a new logical invocation;
- call AgentToolExecutionCoordinator / CapabilityToolExecutionAdapter /
  CapabilityRuntime continuation for the same durable invocation;
- preserve plan order in returned results;
- persist a continuation transport result only through the existing canonical
  AgentToolResult projection path when no committed projection already exists;
- return an ordered recovery-batch result to the caller.

F3-A MUST NOT:

- call ordinary execute_capability() for a planned continuation;
- reserve a new logical tool quota charge;
- create a new CapabilityInvocation to replace the existing invocation;
- execute provider/model inference;
- enter the normal Agent iteration loop after the recovered active batch;
- publish a new Agent checkpoint merely to hand off to provider inference;
- renew/release/remint the R12 lease;
- park/recover/fail the RUNNING execution through generic R7 post-claim helpers;
- mutate R6 reconciliation authority;
- perform UBQ reserve/recharge/settle/release outside canonical continuation.

### 4.1 Per-action dispatch fence

For executable continuation actions, the active fence MUST be checked **inside each
per-action semaphore slot**, immediately before the adapter/CapabilityRuntime
continuation call.

Conceptual order:

```text
acquire continuation semaphore slot
-> exact active-fence check
-> CapabilityToolExecutionAdapter.continue_invocation(...)
-> CapabilityRuntime.continue_invocation(...)
```

If the fence fails in one slot:

- that slot MUST NOT dispatch;
- no later not-yet-dispatched slot may dispatch;
- sibling local tasks SHOULD be cancelled where safe;
- already externally dispatched work is not retroactively "un-dispatched";
- no stale-owner durable commit may follow.

REUSE_COMMITTED is a read-only durable reuse path and does not itself create an
external dispatch. It still remains subject to the initial recovery-active authority
validation and all later commit fences.

### 4.2 Durable tool-result commit fence

If a continuation result is not yet represented by a canonical COMMITTED
AgentToolResult and the existing projection path would persist it, F3 MUST revalidate
the exact active fence immediately before that durable commit.

After the commit, F3 reloads the canonical committed result. Provisional or missing
results MUST NOT be exposed as model-consumable recovery output.

## 5. F3-B boundary — provider/model progression is explicitly CLOSED

F3-A ends **before provider/model inference**.

The following remains CLOSED until a separate F3-B PRE-CLAIM refresh:

- entering AgentRuntime normal subsequent iteration execution;
- ProviderInferenceAdapter.complete();
- ProviderRuntime / ChatExecutionHandler dispatch;
- new logical inference admission/accounting;
- post-recovery model continuation;
- durable iteration/checkpoint/execution commits that belong to the subsequent
  normal Agent loop.

This boundary is intentional because current UBQ-4 PR #190 modifies AgentRuntime,
Agent inference adapter/provider runtime/quota seams.

F3-B may open only after exact-main dependency refresh. If #190 lands first, F3-B
MUST re-anchor/re-audit on the new canonical main before production CLAIM.

## 6. Post-activation failure authority — fail closed without parking mutation

This freeze selects **Option A / current-safe minimum**.

On lease fence loss, expiry, owner mismatch, generation mismatch, or other stale
recovery-active authority:

```text
STOP new external dispatch
STOP stale-owner durable commit
CANCEL local not-yet-completed continuation work where safe
RETURN/RAISE fail-closed recovery execution error
DO NOT publish WAITING/RECOVERY
DO NOT publish FAILED
DO NOT release TaskBudget active capacity
DO NOT clear/remint/renew the lease
```

The durable RUNNING lease is allowed to expire naturally. Canonical R12-D/E later
observes the stale owned RUNNING execution and publishes a new recovery safe point.

F3 MUST NOT call generic R7 recover_claimed_resume() or fail_claimed_resume() for
SERVER_RECOVERY activation because those helpers do not carry exact R12 activated
owner/generation/expiry authority and own different R7 parking semantics.

Any immediate parking/relinquish design requires a separately frozen owner+generation
fenced transition contract and is outside this PRE-CLAIM.

## 7. Concurrency and cancellation

Parallel recovered continuations remain bounded by the existing
AgentToolExecutionCoordinator semaphore.

The lease fence is evaluated independently inside each dispatch slot.

Once any task reports stale active authority:

- the recovery batch enters fail-closed state;
- no new slot may externally dispatch;
- caller cancellation remains propagated;
- cancellation MUST NOT synthesize a durable parking/failure transition under stale
  authority.

## 8. Quota and invocation identity

For every non-REUSE TOOL action:

- invocation_id remains the existing logical CapabilityInvocation identity;
- capability_id/version/kind/execution mode/idempotency remain frozen by RecoveryPlan;
- canonical CapabilityRuntime.continue_invocation() owns fresh continuation target
  re-proof and UBQ-3 recover_tool_call() authority;
- F3 performs no second admission;
- F3 performs no second logical TOOL charge;
- canonical continuation finalization remains exactly-once authority.

REUSE_COMMITTED never mints quota authority.

## 9. Integration order / cross-track guard

At this freeze:

```text
main = 6acf6c5ffc288c2b747f3ca92e86cca805ee43c7
UBQ-4 PR #190 = OPEN / DRAFT / unmerged / material-potential to F3-B
CTX-F5-3I-B1 PR #198 = OPEN / DRAFT / unmerged / current non-material
```

F3-A contract/evidence work may proceed because its bounded tool-continuation lease
fence does not require provider/inference seams.

F3-A production CLAIM is still CLOSED until independent PRE-CLAIM audit closes the
three P1 findings against this exact contract.

F3-B production CLAIM is additionally CLOSED until #190 ordering is settled and a
fresh exact-main dependency audit is complete.

Any material movement in ResumeClaim, RecoveryPlan, RecoveryActivationResult,
CapabilityRuntime continuation, R12 lease predicates, AgentToolResult commitment,
or TaskBudget recovery-capacity authority triggers STOP/re-audit.

## 10. Required implementation evidence for F3-A

A future F3-A implementation audit MUST include at least:

1. exact RecoveryPlan + RecoveryActivationResult authority match;
2. foreign owner rejection;
3. wrong lease generation rejection;
4. exact lease-expiry mismatch rejection;
5. expired lease rejection;
6. fence loss before first action => zero external dispatch;
7. fence loss while waiting for a semaphore slot => that slot never dispatches;
8. parallel batch fence loss => no later new dispatch;
9. fence loss before durable tool-result projection => zero stale-owner commit;
10. REUSE_COMMITTED remains read-only/no dispatch;
11. continuation uses same invocation_id and bypasses ordinary execute_capability();
12. ordered results match RecoveryPlan.ordered_tool_call_ids;
13. missing/non-COMMITTED projection fails closed;
14. no R7 generic recover_claimed_resume()/fail_claimed_resume() call;
15. no provider/model inference call in F3-A;
16. no UBQ admission/refund lifecycle duplicated;
17. no lease renew/release/remint;
18. CLIENT_RECONNECT R7 behavior remains unchanged.

## 11. PRE-CLAIM disposition

```text
P1-R12-F3-ACTIVE-LEASE-FENCE-1
= OWNER CONTRACT REPAIR CANDIDATE

P1-R12-F3-RECOVERY-EXECUTION-SEAM-2
= OWNER CONTRACT REPAIR CANDIDATE

P1-R12-F3-POSTACTIVATION-FAILURE-AUTHORITY-3
= OWNER CONTRACT REPAIR CANDIDATE

F3-A production CLAIM = HOLD pending independent PRE-CLAIM PASS
F3-B production CLAIM = HOLD pending #190 order + fresh exact-main audit
merge authority = NONE
```
