# R8-G — FINAL R8 EXIT-GATE COMPLETION

**Repository:** boxs-51/assistant  
**Work branch:** `work/r8-g-0c519287`  
**R8-F completion baseline:** `0c519287769377fd9b5ce24acf66daf6919342b4`  
**R8-G tested code/test HEAD:** `275f38b17bcf9f388c74998c916552e846a5b01e`  
**Status:** **IMPLEMENTATION / EXIT-GATE EVIDENCE GREEN — FINAL CROSS-AUDIT PENDING**

---

## 1. Scope delivered

R8-G closes Phase R8 as a final composition-proof/hardening workstream.

R8-G did not add branch result resolution, retry-as-new-execution, distributed
execution leases, stale-RUNNING takeover, provider behavior, CapabilityRuntime
authority, or client protocol changes.

The final R8-G scope is:

~~~text
G0      final R8 exit-gate contract freeze
G0.1    two cross-audited control-plane P1 hardening fixes
G1      durable fork happy-path + replay proof
G2      sibling execution/context isolation + root compatibility coexistence
G3      rejection / zero-partial-write proof
G4      restart / replay / cancellation proof
G5      explicit R9 non-resolution boundary proof
CI      dedicated R8 A-G exit gate
~~~

---

## 2. G0 contract freeze

Initial G0 freeze:

~~~text
5f47734920e6cf9b5d69ff68ad62077596d7ef8f
docs(r8): freeze R8-G final exit-gate contract
~~~

Document:

~~~text
docs/agent_execution_r8/R8_G_EXIT_GATE_CONTRACT_FREEZE_0C519287.md
~~~

The freeze keeps R8-G inside final proof/hardening and preserves the explicit
R9, R12 and R14 boundaries.

---

## 3. G0.1 cross-audit P1 hardening

Cross-audit of the frozen R8-F baseline found two P1 control-plane composition
gaps. Both were re-frozen before final G1-G5 work.

### P1-1 — public FORK error taxonomy

Problem:

~~~text
ForkPlanError / ForkConsumeError
    could fall through the public router as generic string-only HTTP 422

stable FORK code + deferred/retry semantics
    were therefore not preserved end-to-end
~~~

Fix:

~~~text
efb232064ba67334b4f74bd9242eeb70fa4349cc
fix(r8-g): preserve public fork error semantics
~~~

The public FORK boundary now preserves:

~~~text
detail.code
detail.message
detail.retryable
~~~

with deferred planner/consume outcomes distinguishable from permanent
rejection/conflict.

### P1-2 — restart-safe durable Task cancellation facade

Problem:

~~~text
fork / branch reads
    used durable Task ownership after restart

Task cancellation facade
    still required coordinator._tasks / _sessions process-local state
~~~

Fix:

~~~text
3e3370b564415ba140fc731f359ffe737899d381
fix(r8-g): make durable task cancellation restart-safe
~~~

Durable-mode cancellation now performs:

~~~text
durable Task load + owner check
-> materialize/sync AgentTask response view
-> TaskBudgetService.cancel_task()
-> durable Task/TaskBudget commit
-> local root runner drain if present
-> AgentExecutionSupervisor.cancel_task(task_id)
~~~

It does not introduce R12 owner/lease/recovery semantics.

Targeted regression commit:

~~~text
1fb677b22631c9e25c56c3a456491a7f3322572c
test(r8-g): cover fork errors and restart-safe cancel
~~~

G0.1 freeze update:

~~~text
778ad431334e242c1a0022759c794c4ff37b1e9f
docs(r8-g): freeze cross-audit P1 hardening
~~~

The independent Issue #5 cross-audit closed both P1s before G1-G5 was opened.

---

## 4. G1 — durable vertical FORK happy path

Final vertical regression proves, using real SQLite/UoW durable components:

~~~text
owned Task + WAITING source checkpoint
-> build/consume FORK
-> ForkAdmission
-> B2 + E2 RUNNING@1
-> supervisor reservation
-> durable activation RUNNING@2
-> active budget restore
-> supervisor-owned runtime start
~~~

Same-request replay proves:

~~~text
same fork_request_id
-> same branch_id
-> same execution_id
-> no second runtime start
-> no second branch/execution accounting charge
~~~

The frozen base transcript and branch overlay are reconstructed from durable
R8-E evidence.

---

## 5. G2 — sibling isolation and ownership coexistence

The exit gate proves two independent fork executions under one Task can be
owned concurrently by `AgentExecutionSupervisor`.

Branch context isolation is explicit:

~~~text
B2 transcript = frozen base + overlay-alpha
B3 transcript = frozen base + overlay-beta

alpha does not leak into B3
beta does not leak into B2
source checkpoint transcript remains unchanged
~~~

A final cross-audit coverage gap required explicit proof that legacy/root
compatibility ownership does not become fork exclusivity.

Closed by:

~~~text
275f38b17bcf9f388c74998c916552e846a5b01e
test(r8-g): prove root compatibility runner coexists with fork
~~~

The regression proves:

~~~text
coordinator._running_tasks[T1] root wrapper is live
+
fork B2/E2 activates by execution_id
->
root runner remains independently owned
fork E2 appears in AgentExecutionSupervisor
fork E2 can drain without draining the root wrapper
no Task result authority is minted
~~~

---

## 6. G3 — rejection and zero-partial-write proof

The dedicated R8 gate reuses the lower-layer R8-A-F rejection matrix and adds
vertical capacity rejection proof.

Covered rejection classes include:

~~~text
unsafe checkpoint
pending/unknown side effect
foreign principal
budget/capacity exhaustion
same-request semantic replay mismatch
activation/replay conflicts
~~~

The vertical capacity case proves no rejected second fork creates:

~~~text
new TaskBranch
new AgentExecution
new ForkAdmission
TaskBudget accounting delta
supervisor reservation/handle leak
Task terminalization
~~~

---

## 7. G4 — restart / replay / cancellation proof

### Consume-before-activation restart

A committed:

~~~text
ForkAdmission + B2 + E2 RUNNING@1
~~~

is replayed after process-local ownership loss and converges on the same B2/E2
with one activation/runtime start.

### Post-activation replay

~~~text
E2 RUNNING@2+
same request
-> identity-only replay
-> started=false
-> no second runtime start
~~~

### Restart-safe durable cancellation

A fresh coordinator with empty process-local Task/session caches reaches the
same durable cancellation authority.

The real-store vertical gate proves:

~~~text
Task -> CANCELLED
TaskBudget -> CLOSED
dormant ForkAdmission-backed E2 RUNNING@1 -> CANCELLED@2
active execution capacity released exactly once
active branch capacity remains charged
branch resolution_state remains OPEN
~~~

No stale RUNNING takeover or distributed cancellation mechanism is added.

---

## 8. G5 — explicit R9 boundary proof

The final regression forces all current OPEN branch heads terminal while no R9
resolution exists.

Required and observed R8 behavior:

~~~text
Task remains nonterminal
TaskBudget remains OPEN
Task.output remains unset
Task.error is not promoted to final Task authority
active_branches remains charged
all branch resolution_state values remain OPEN
~~~

R8-G therefore does not pull any of the following backward from R9:

~~~text
RETRY
ADOPT
SUPERSEDE
DISCARD
AGGREGATE
Task final result winner
branch resolution accounting
~~~

---

## 9. Final production blast radius

Compared with closed R8-F completion HEAD `0c519287`, final R8-G production
changes are limited to two already cross-audited hardening seams:

~~~text
se/src/transport/gateway/api/v1/multi_agent_router.py
se/src/runtimes/agent/coordinator.py
~~~

Final R8-G proof/support files:

~~~text
docs/agent_execution_r8/R8_G_EXIT_GATE_CONTRACT_FREEZE_0C519287.md
se/tests/transport/test_r8_g_control_plane.py
se/tests/integration/test_r8_g_exit_gate.py
.github/workflows/r8-exit-gates.yml
~~~

No R8-G changes exist in:

~~~text
cl/
provider runtime
CapabilityRuntime
events_router.py
database schema / migrations
R9 resolution models
R12 lease/recovery ownership
~~~

---

## 10. Sequencing correction

During G0.1 work, G1/workflow artifacts were briefly committed before the
independent P1 audit gate closed.

They were immediately reverted:

~~~text
b582ea8b680b1e282a97c20e6aee83f0d4786cd3
revert(r8-g): hold G1-G5 until P1 audit closes

f6d3fee8ef697ad95f0eab87fd7d8331c21a8632
revert(r8-g): defer final gate workflow until P1 closure
~~~

Exact `f6d3fee8` then passed Architecture + Phase 5 and was checkpointed as
G0.1 CLOSED/GREEN before G1-G5 was re-claimed.

The final branch state therefore preserves the agreed implementation order.

---

## 11. Exact final CI evidence

All final gates below ran against the same tested code/test HEAD:

~~~text
275f38b17bcf9f388c74998c916552e846a5b01e
~~~

### R8 Exit Gates

~~~text
workflow run: 35824193248
job:          107062266684
result:       SUCCESS
tests:        141 passed, 31 warnings in 36.56s
~~~

This gate runs the R8 architecture/integration suites plus the R8-G transport
regressions.

### Architecture Baseline

~~~text
workflow run: 35824193158

linux-full-suite
job:          107062266222
result:       SUCCESS
tests:        945 passed, 1 skipped, 43 warnings in 103.01s

windows-client-contracts
job:          107062266360
result:       SUCCESS
tests:        68 passed in 8.64s
~~~

### Phase 5 Exit Gates

~~~text
workflow run: 35824193209
job:          107062266410
result:       SUCCESS
tests:        40 passed in 1.35s
~~~

---

## 12. Final R8-G invariant summary

At the tested R8-G code HEAD:

~~~text
safe FORK source
+ atomic durable ForkAdmission
+ immutable branch base/context
+ replay-first identity
+ RUNNING@1 -> RUNNING@2 sole activation authority
+ execution-scoped supervisor ownership
+ sibling branch isolation
+ root compatibility coexistence
+ durable branch reads
+ restart-safe durable Task cancellation
+ structured public FORK errors
+ aggregate nonterminal Task activity
+ R7 resume compatibility
+ R9 non-resolution boundary
+ no R12 recovery leakage
~~~

are covered by the dedicated R8 gate and the full compatibility suites.

---

## 13. Exit boundary

R8-G implementation/evidence is green at `275f38b1`.

Final closure still requires the independent Issue #5 cross-auditor to review
this exact tested head and confirm:

~~~text
P0 = 0
P1 = 0
no unresolved exit-gate coverage gap
~~~

After that audit:

~~~text
R8 = CLOSED / GREEN
R9 = READY FOR BOUNDARY AUDIT
~~~

R9 implementation must not begin before its own contract audit/freeze.
