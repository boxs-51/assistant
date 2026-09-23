# R8-G — FINAL R8 EXIT-GATE CONTRACT FREEZE

**Repository:** boxs-51/assistant  
**Work branch:** work/r8-g-0c519287  
**R8-F completion baseline:** 0c519287769377fd9b5ce24acf66daf6919342b4  
**Tested production code baseline:** 006c70167059a520c42f9a40718d15a8cc253fe1  
**Status:** **G0 FROZEN / NO PRODUCTION CODE**

---

## 1. Purpose

R8-G is the final proof/hardening workstream for Phase R8.

R8-G does not introduce a new Task, Branch, Execution, Resume, Fork,
cancellation, or result-resolution authority model.

Its only purpose is to prove that the already-frozen R8-A through R8-F
components compose into the Phase-R8 exit gate:

~~~text
Fork is safe, isolated and durable.
~~~

The preferred R8-G production diff is:

~~~text
NONE
~~~

If an R8-G test exposes a real production correctness defect, implementation
must stop, post a new P0/P1 finding to Issue #5, re-audit the exact boundary,
and only then claim a narrowly-scoped production fix.

---

## 2. Frozen baseline

R8-F is closed/green.

Tested production code:

~~~text
006c70167059a520c42f9a40718d15a8cc253fe1
~~~

R8-F completion-only documentation HEAD:

~~~text
0c519287769377fd9b5ce24acf66daf6919342b4
~~~

Exact final CI on the tested code baseline:

~~~text
Architecture Baseline 35819929664
    linux-full-suite
        934 passed, 1 skipped, 43 warnings

    windows-client-contracts
        68 passed

Phase 5 Exit Gates 35819929697
    40 passed
~~~

The R8-G branch must start from the R8-F completion HEAD, not the older
r8-taskbranch-fork ref while that ref remains behind the completed R8-F work.

---

## 3. Authority already owned by R8-A through R8-F

R8-G must treat the following as frozen inputs, not redesign targets.

### R8-A / R8-B

~~~text
Task 1:N Branch
Branch 1:N Execution
root/delegated branch authority
TaskBudget branch/execution accounting
R7 RESUME stays on the same branch
~~~

### R8-C

~~~text
read-only safe fork planning
side-effect safety checks
pending/unknown remote-outcome rejection
semantic fork fingerprint
overlay validation
budget/capacity preflight
~~~

### R8-D

~~~text
atomic fork consume
ForkAdmission
new Branch + new Execution
accounting in one transaction
idempotent same fork_request_id replay
fresh FORK Task activity epoch
~~~

### R8-E

~~~text
immutable BranchContext/runtime seed
restart-safe fork bootstrap
base transcript freeze
branch-local overlay isolation
transport-free fork seed
~~~

### R8-F

~~~text
public fork control plane
replay-first durable identity
RUNNING@1 -> RUNNING@2 activation authority
execution-scoped supervisor ownership
sibling branch runtimes under one Task
durable branch list/get
aggregate nonterminal Task activity
Task activity epoch
live branch-head CAS
exact WAITING reason-set CAS
Task cancellation fan-out
R7 ResumeClaim compatibility
same-request reconnect claim migration
atomic task-scoped WAIT timeout/activity
~~~

R8-G must prove composition of these contracts.

---

## 4. Canonical R8-G boundary

R8-G is:

~~~text
FINAL EXIT-GATE PROOF / HARDENING
~~~

R8-G is not:

~~~text
a new runtime authority phase
a new branch-resolution phase
a retry phase
a crash-recovery phase
a general fault-injection phase
~~~

The default implementation shape is:

~~~text
tests + documentation only
~~~

---

## 5. R9 boundary — MUST NOT move backward

R8-G must not implement any of the following:

~~~text
retry-as-new-execution
ADOPT
SUPERSEDE
DISCARD
AGGREGATE
accepted branch winner
final branch result authority
Task final result winner
Task completion due to branch result
active_branches release due to branch resolution
~~~

Until R9 exists:

~~~text
all current OPEN branch heads may become terminal
AND
Task still remains nonterminal
AND
TaskBudget remains open unless explicitly cancelled
AND
no branch result is implicitly adopted
AND
no Task output winner is minted
~~~

This is a required R8-G regression.

Any R8-G patch that writes final Task output/result authority is a STOP
condition.

---

## 6. R12 boundary — MUST NOT move backward

R8-G must not introduce:

~~~text
owner_instance_id
execution lease
lease expiry
stale RUNNING scanner
recovery coordinator
cross-worker immediate cancellation channel
distributed stale-runner takeover
~~~

R8-G may prove restart-safe durable replay for:

~~~text
crash/restart after fork consume
before activation authority is acquired
~~~

R8-G must not solve:

~~~text
stale post-activation RUNNING ownership
~~~

That remains R12.

---

## 7. R14 boundary

R8-G should add only R8-specific exit-gate races and vertical proofs.

Do not turn R8-G into the general R14 fault-injection program.

R8-G may inject narrowly-scoped failures around:

~~~text
fork planning rejection
fork consume rollback
consume-before-activation restart
activation winner/loser
handoff failure
Task cancel vs activation
activity reconciliation
~~~

R8-G must not attempt the complete production fault matrix owned by R14.

---

## 8. Lock and authority invariants

R8-G tests must preserve the final R8-F authority order:

~~~text
Task authority
-> TaskBudget / AgentExecution authority
-> current OPEN branch-head snapshot
-> aggregate Task activity
-> Task activity CAS
~~~

Cross-dialect activity protection remains:

~~~text
Task activity epoch
+ live branch-head predicate
+ exact live WAITING reason-set proof
~~~

R8-G must not replace this with a new locking model.

---

## 9. Runtime ownership invariants

Fork execution ownership remains execution-scoped.

~~~text
AgentExecutionSupervisor
    owns by execution_id

MultiAgentCoordinator._running_tasks
    remains root/legacy Task compatibility ownership
~~~

Forbidden:

~~~text
routing fork E2 through MultiAgentCoordinator.execute_task()
using _running_tasks[task_id] as fork exclusivity
allowing one sibling branch to terminalize Task
~~~

R8-G should prove these invariants, not refactor them.

---

## 10. Replay invariants

Same fork_request_id is durable replay identity.

Canonical replay states remain:

~~~text
RUNNING@1
    preactivation
    -> same E2 may continue activation

RUNNING@2+
WAITING@3+
COMPLETED@2+
FAILED@2+
CANCELLED@2+
TIMEOUT@2+
    identity replay only
    -> no new Branch
    -> no new Execution
    -> no second runtime start
~~~

Unexpected lifecycle shapes remain fail-closed.

R8-G must not add a second replay authority model.

---

## 11. Cancellation invariants

Task cancellation remains durable-first:

~~~text
Task -> CANCELLED
TaskBudget -> CLOSED
settle exact dormant fork RUNNING@1 executions
commit
then drain local root/sibling runners
~~~

Activation and preactivation cancellation continue to race on the same
durable E2 revision.

R8-G must prove cancellation composition but must not add distributed
cross-worker cancellation.

---

## 12. G0 — contract freeze

G0 changes documentation only.

Required G0 outputs:

~~~text
docs/agent_execution_r8/R8_G_EXIT_GATE_CONTRACT_FREEZE_0C519287.md
Issue #5 [CLAIM]
Issue #5 [DONE]/[HANDOFF]
~~~

No tests or production files are changed in G0.

---

## 13. G1 — full durable fork happy-path proof

Add a vertical integration test using real persistence components:

~~~text
owned Task/source checkpoint
-> fork request/control-plane
-> durable ForkAdmission
-> B2
-> E2 RUNNING@1
-> supervisor reservation
-> activation RUNNING@2
-> active budget restore
-> supervisor-owned runtime begins
-> durable branch/execution visible
~~~

Prefer real:

~~~text
SQLite
UnitOfWork
DurableAgentStore
TaskBudgetService
AgentExecutionSupervisor
~~~

A deterministic AgentRuntime seam is acceptable to avoid provider/network
dependence.

Required assertions:

~~~text
same fork_request_id retry
    -> same branch_id
    -> same execution_id
    -> no second ForkAdmission
    -> no second runtime start

Task remains nonterminal.

No Task result winner exists.
~~~

---

## 14. G2 — sibling execution independence / context isolation

Build one Task with source/root B1 and fork B2.

Prove:

~~~text
B1 and B2 can both be locally owned concurrently
different execution_ids
same task_id
different branch_ids

B2 context
    = frozen base transcript + B2 overlay

B1 transcript/context
    remains unchanged

B2 overlay
    never appears in B1

root compatibility _running_tasks[task_id]
    does not block fork execution ownership

B2 terminal/waiting transition
    cannot terminalize Task
~~~

No coordinator ownership refactor is allowed in G2.

---

## 15. G3 — vertical rejection / zero-partial-write proof

Through the highest practical control-plane layer, prove:

~~~text
unsafe checkpoint rejected
pending/unknown remote side effect rejected
foreign principal rejected
budget closed/capacity exhausted rejected/deferred correctly
same fork_request_id + semantic mismatch rejected
~~~

For each rejected request, prove:

~~~text
no new Branch
no new Execution
no new ForkAdmission
no TaskBudget accounting leak
no active execution leak
no supervisor reservation/handle leak
no Task terminalization
~~~

Existing lower-layer tests may satisfy individual checks, but G3 should add
vertical evidence where composition is currently unproven.

---

## 16. G4 — replay / restart / cancellation vertical proof

Required scenarios:

### G4.1 consume-before-activation restart

~~~text
ForkAdmission + B2 + E2 RUNNING@1 committed
process-local ownership lost
restart/rebuild coordinator/runtime
same fork request
-> same B2/E2
-> one activation winner
-> one runtime start
~~~

### G4.2 post-activation replay

~~~text
E2 RUNNING@2+
same fork request
-> identity-only
-> no second runtime start
~~~

### G4.3 durable branch reads

With empty coordinator caches:

~~~text
list branches
get branch
~~~

must reconstruct from durable state and preserve owner authorization.

### G4.4 Task cancellation

With independent local sibling runners:

~~~text
durable Task cancellation first
-> durable budget closed
-> exact dormant RUNNING@1 cleanup
-> all process-local sibling handles cancelled/drained
-> no active execution double-release
~~~

No R12 post-activation stale RUNNING takeover is allowed.

---

## 17. G5 — R9 boundary regression + final exit gate

Add an explicit regression:

~~~text
Task T1
B1 current head terminal
B2 current head terminal
both branches still OPEN
no R9 resolution
~~~

Required final R8 behavior:

~~~text
Task is nonterminal
TaskBudget is not closed merely because both executions are terminal
Task.output is not selected as a branch winner
Task.error is not selected as a final winner
active_branches remains charged
branch resolution_state remains OPEN
~~~

This test is the explicit guard against pulling R9 result authority backward.

---

## 18. Expected R8-G file blast radius

Preferred:

~~~text
ADD docs/agent_execution_r8/R8_G_EXIT_GATE_CONTRACT_FREEZE_0C519287.md
ADD se/tests/integration/test_r8_g_exit_gate.py
OPTIONAL ADD se/tests/e2e/test_r8_g_fork_exit_gate.py
LATER ADD docs/agent_execution_r8/R8_G_COMPLETION.md
~~~

Production diff should remain:

~~~text
NONE
~~~

Existing production files may be read/imported by tests but should not be
edited during the proof-only path.

---

## 19. STOP / re-audit conditions

Stop G1→G5 immediately if a test appears to require any of:

~~~text
schema migration
new persistent authority field
new public API semantics
cl/ changes
provider runtime changes
CapabilityRuntime authority changes
events_router resume-protocol changes
retry-as-new-execution
Branch resolution state transition
Task final result/completion authority
owner_instance_id
lease/recovery ownership
general R14 fault-injection framework
~~~

Required response:

~~~text
1. post [AUDIT] P0/P1 to Issue #5;
2. do not patch production code yet;
3. freeze exact defect/contract;
4. re-audit blast radius;
5. only then claim a narrowly-scoped fix if authorized.
~~~

---

## 20. Test reuse rule

R8-G should not duplicate every A→F test.

For each Phase-R8 exit-gate requirement:

~~~text
either
    cite an existing lower-layer regression
or
    add one vertical R8-G regression
~~~

The purpose is composition evidence, not test-count inflation.

---

## 21. Required final R8-G gates

Before R8-G completion:

~~~text
targeted R8-A→G tests
Architecture Baseline
Phase 5 Exit Gates
~~~

All must pass on the exact tested code/docs HEAD.

If G1→G5 add tests only, those tests must be included in the final targeted
evidence.

Write:

~~~text
docs/agent_execution_r8/R8_G_COMPLETION.md
~~~

only after all exact-head gates are green.

---

## 22. Completion criteria

R8-G is complete only when the repository has explicit proof that:

~~~text
fork creation is durable and atomic
fork replay is idempotent
branch base/context is immutable and isolated
sibling executions can coexist under one Task
fork execution ownership is execution-scoped
Task activity remains aggregate/nonterminal
R7 resume remains compatible
Task cancellation drains local branch runners
unsafe fork paths produce zero partial durable ownership
R9 result authority has not been pulled backward
restart-before-activation converges on the same branch/execution
~~~

No new R8 production semantics should be required.

---

## 23. Frozen handoff

After this G0 document is committed:

~~~text
NEXT:
    G1 -> G5 tests/docs only

DO NOT:
    edit production code unless a new audited P0/P1 is first posted
~~~

R8-G starts from the closed R8-F boundary and ends with evidence, not a new
authority model.
