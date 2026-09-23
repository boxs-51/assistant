# R8-F — COMPLETION / EXIT GATE

**Repository:** `boxs-51/assistant`  
**Canonical branch:** `r8-taskbranch-fork`  
**Work branch:** `work/r8-f-a723337b`  
**R8-E -> R8-F baseline:** `a723337b7b3104beae4728c1b0f1f534c3de807f`  
**R8-F tested code HEAD:** `006c70167059a520c42f9a40718d15a8cc253fe1`  
**Contract freeze document:** `R8_F_EXECUTION_ACTIVATION_CONTROL_PLANE_CONTRACT_FREEZE_79A1CB3C.md`  
**Final freeze update:** `06e88394480fdf238adaf205abe620a83aa4193d`  
**Status:** **R8-F0→F7 CLOSED / GREEN**

---

## 1. Scope completed

R8-F turns the read-only R8-E fork bootstrap into a durable execution/control plane without pulling R9 branch-result resolution backward.

Delivered:

- public fork request/response contracts;
- replay-first durable fork lookup by `task_id + fork_request_id`;
- exact replay identity after source execution/checkpoint progress;
- durable E2 activation fence:
  - `RUNNING@1` = preactivation only;
  - only `RUNNING@1 -> RUNNING@2` CAS grants execution authority;
- Task/TaskBudget/Branch/BranchContext/runtime-seed validation before activation;
- process-local supervisor reservation separated from durable authority;
- active execution budget restore before inference/tool work;
- execution-scoped fork runtime handoff that bypasses one-root-runner-per-Task compatibility wrappers;
- concurrent sibling branch runners under the same Task;
- durable branch list/get control-plane reads;
- owner-only fork/read authorization;
- public fork/branch HTTP surfaces;
- durable multi-branch Task activity reconciliation;
- legacy/root Task write fences after a Task becomes multi-branch;
- exact deterministic WAITING reason union;
- cross-dialect activity safety:
  - Task-first lock order where row locks exist;
  - Task activity revision epoch;
  - live branch-head CAS backstop;
  - exact live WAITING reason-set predicate for SQLite/weak row-locking dialects;
- fresh FORK admissions advance Task activity epoch even if visible Task state stays RUNNING;
- R7 task-scoped resume advances a semantic no-op Task activity epoch before execution authority;
- task-scoped WAIT expiry / timeout and aggregate activity commit atomically;
- transient R7 activity conflicts retry the same CREATED ResumeClaim;
- retryable wire conflict preserves the same `claim_id + resume_request_id`;
- same retryable CREATED claim may migrate K2 -> K3 for the same authenticated user + stable client after exact semantic fingerprint proof;
- Task cancellation durably closes Task/TaskBudget before local runner fan-out;
- dormant preactivation fork cancellation/accounting;
- sibling branch-runner cancellation;
- activation/start failure accounting and fail-closed handoff behavior;
- restart-safe branch reads and replay behavior.

R8-F does **not** implement branch result resolution, ADOPT/SUPERSEDE/DISCARD/AGGREGATE, stale RUNNING lease recovery, provider retry/fallback, or R9+ semantics.

---

## 2. Implementation history

R8-F is 84 commits ahead of the frozen R8-E -> R8-F baseline.

Major milestones:

```text
16e7c750  R8-F0 contracts
819dc74e  R8-F1/F2 durable replay + activation
c7c55a37  R8-F3/F4 execution-scoped activation handoff
fe5b2978  R8-F6 public fork/branch HTTP surfaces
2acc6fe7  R8-F5 multi-branch Task activity reconciliation
e15685c0  legacy Task write fence after FORK
c76db614  Task-first activity serialization
302d6b02  live branch-head activity CAS backstop
37abb5c0  exact live WAITING reason-set CAS
b3546a5e  FORK Task activity epoch
dc2c8e90  R7 resume semantic no-op Task epoch
6ddf5ded  atomic task-scoped WAIT timeout
6230bc29  bounded retry for transient resume activity conflicts
9accc49a  retryable resume claim identity on wire
e084f166  CREATED ResumeClaim reconnect-generation rebind
eb172d2f  transport migration of same retryable claim/request
006c7016  final timeout CAS-loser compatibility regression
```

The implementation was repeatedly hardened by CI and read-only cross-audit rather than accepting the first green-looking design.

---

## 3. Final blast radius

Diff from `a723337b` to tested code HEAD `006c7016` changes 21 files.

Production scope:

```text
docs/agent_execution_r8/R8_F_EXECUTION_ACTIVATION_CONTROL_PLANE_CONTRACT_FREEZE_79A1CB3C.md
se/src/application/container.py
se/src/domain/schemas/multi_agent.py
se/src/infrastructure/storage/repositories/agent.py
se/src/main.py
se/src/runtimes/agent/contracts/fork.py
se/src/runtimes/agent/coordinator.py
se/src/runtimes/agent/persistence.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/task_budget.py
se/src/transport/gateway/api/v1/multi_agent_router.py
se/src/transport/gateway/api/v1/events_router.py
```

`events_router.py` is the frozen narrow R7 compatibility exception only:

- preserve a retryable CREATED ResumeClaim identity;
- preserve same-request retry;
- migrate that CREATED claim across connection generations after semantic proof;
- no new ResumeClaim logical authority model;
- no new ACK/handoff authority.

No changes were required in:

```text
cl/
se/src/runtimes/capability/drivers/agent_driver.py
provider runtime
R9 resolution models
```

Test changes are limited to architecture/integration/transport regression coverage.

---

## 4. Critical audit findings closed

### 4.1 P0 — stale aggregate Task activity / SQLite split-brain

A lock-only implementation allowed this committed state on SQLite:

```text
current OPEN branch head = RUNNING
Task = WAITING
```

Final cross-dialect model:

```text
row-locking DB:
    Task FOR UPDATE
    + Task activity revision epoch

SQLite / weak row-locking:
    Task activity revision epoch
    + live branch-head conditional Task CAS
    + exact live WAITING reason-set CAS
```

FORK and R7 resume both advance the Task activity epoch when they change the live branch-activity graph.

**Status: CLOSED.**

### 4.2 P1 — stale WAITING reason projection

The first live activity CAS proved only WAITING category existence and could still commit a stale wait-reason union.

The final WAITING CAS proves:

- no live active current OPEN head;
- at least one WAITING current OPEN head;
- no normalized live reason outside the expected set;
- every expected reason still exists.

**Status: CLOSED.**

### 4.3 P1 — retryable R7 activity conflict surfaced as non-retryable

A rolled-back activity-epoch conflict originally left the durable ResumeClaim CREATED but surfaced `RESUME_CONFLICT retryable=false`.

Final behavior:

```text
activity conflict
-> rollback execution/budget/Task/claim authority
-> ResumeClaimDeferred(retryable=true)
-> bounded internal retry of SAME claim/request
-> if still deferred, wire preserves SAME claim_id + resume_request_id
```

AgentExecution claim CAS loss remains non-blind-retry because another execution authority may have won.

**Status: CLOSED.**

### 4.4 P1 — direct task-scoped WAIT timeout was two transactions

The direct runtime timeout path originally committed execution TIMEOUT first and reconciled Task activity in a later transaction.

Final path:

```text
Task authority
-> validate WAITING execution@revision
-> WAITING -> TIMEOUT CAS
-> aggregate multi-branch activity rederive
-> one commit
```

A lost activity CAS rolls the staged TIMEOUT back before retry. A timeout CAS loser remains `ExecutionConflictError`, preserving pre-R8 behavior.

**Status: CLOSED.**

### 4.5 P1 — same retryable ResumeClaim failed after reconnect generation change

The client correctly retried the same `resume_request_id` after K2 -> K3, but the server originally pinned a CREATED claim to K2.

Final model:

- fast replay permits same user/client CREATED claim to reach fresh planning;
- K3 must produce a valid fresh ResumePlan;
- durable store reconstructs the K3 plan with old claim connection K2;
- old reconstructed fingerprint must equal the durable K2 fingerprint;
- only then CAS updates:
  - `connection_id`;
  - `plan_fingerprint`;
- `claim_id`, `resume_request_id`, TTL and logical authority stay unchanged;
- foreign user/client or any non-connection semantic drift is rejected.

**Status: CLOSED.**

---

## 5. Frozen authority / lock order

Canonical R8-F authority order is:

```text
Task authority
-> TaskBudget / AgentExecution authority
-> branch snapshot / aggregate activity
-> Task activity CAS
```

Important consequences:

- FORK consume cannot commit a new RUNNING branch behind a stale Task WAITING snapshot;
- R7 resume cannot leave Task WAITING after its current branch becomes RUNNING;
- Task cancellation closes durable Task/Budget before local runner drain;
- activation and cancellation race on durable revision authority, not supervisor state;
- supervisor ownership remains process-local only;
- no branch-local terminal result may terminalize the Task in R8-F.

---

## 6. Final regression coverage

The final matrix includes:

- fresh activation `RUNNING@1 -> RUNNING@2`;
- two-worker activation race;
- activation loser zero durable cleanup;
- active budget restore before runtime work;
- activation/start failure accounting;
- outer cancellation around activation commit;
- invalid replay lifecycle fail-closed;
- replay after source progress;
- restart/retry after consume-before-activation;
- replay after activation;
- concurrent sibling branch runners;
- Task cancellation of independent sibling runners;
- dormant preactivation fork cancellation;
- cancel-vs-activation race;
- branch completion/failure does not terminalize Task;
- legacy root WAITING/terminal writes cannot override multi-branch aggregate state;
- FORK consume vs legacy terminalization;
- FORK consume vs standalone activity reconciliation;
- exact WAITING reason-set race in both directions;
- R7 resume vs standalone activity reconciliation;
- pre-resume Task epoch rollback;
- task-scoped WAIT expiry reason cleanup;
- atomic direct WAIT timeout rollback with activity-CAS loss;
- timeout CAS loser remains conflict;
- transient activity conflict retries same CREATED claim;
- retryable wire result preserves claim/request identity;
- K2 -> K3 same-request CREATED claim migration;
- foreign client, foreign principal and semantic-drift rebind rejection;
- restart-safe durable branch reads;
- R7 compatibility regressions;
- R8-C/D/E compatibility;
- full repository CI.

---

## 7. Exact final CI evidence

All final gates ran on exact tested code HEAD:

```text
006c70167059a520c42f9a40718d15a8cc253fe1
```

### Architecture Baseline

Workflow run:

```text
35819929664
```

Linux full suite:

- job: `linux-full-suite`
- job id: `107049417133`
- conclusion: **SUCCESS**

Exact pytest result:

```text
934 passed, 1 skipped, 43 warnings in 104.98s
```

Windows client contracts:

- job: `windows-client-contracts`
- job id: `107049416924`
- conclusion: **SUCCESS**

Exact result:

```text
68 passed in 8.12s
```

### Phase 5 Exit Gates

Workflow run:

```text
35819929697
```

Job:

```text
Phase 5.6-5.11 exit gates
job id 107049417140
```

Conclusion: **SUCCESS**

Exact result:

```text
40 passed in 1.27s
```

---

## 8. Independent Issue #5 cross-audit status

Issue #5 cross-audit found and drove closure of:

- stale Task activity / lock-order risks;
- SQLite live-activity split;
- stale WAITING reason union;
- R7 activity-epoch retryability gap;
- direct WAIT-timeout atomicity gap;
- cross-connection same-request ResumeClaim gap.

Latest read-only cross-audit through `89688dd1` reports the reconnect P1 closed, the direct timeout atomicity issue closed, and no new P0/P1 in those changes.

The only commits after that reviewed code snapshot are:

```text
877005f6  preserve timeout CAS conflict semantics
006c7016  regression proving timeout CAS loser stays conflict
```

They restore the pre-existing CAS-loser behavior and introduce no new authority surface. Exact HEAD CI is fully green as recorded above.

---

## 9. R8-F -> R8-G boundary

R8-F now leaves:

```text
safe durable FORK
+ immutable/restart-safe branch bootstrap
+ replay-first idempotency
+ exactly-one durable activation authority
+ execution-scoped supervisor ownership
+ independent sibling branch runners
+ durable branch read/control plane
+ cross-dialect nonterminal Task activity
+ Task cancellation fan-out
+ R7 resume compatibility under multi-branch activity
```

R8-G may now own only final R8 exit-gate proof / hardening.

R8-G must **not** pull in R9 result-resolution semantics.

Still deferred:

```text
retry-as-new-execution            R9
ADOPT / SUPERSEDE / DISCARD       R9
AGGREGATE                         R9
Task final result authority       R9
provider retry/fallback           R10
checkpoint storage optimization   R11
owner_instance_id / lease         R12
stale RUNNING recovery scanner    R12
legacy cleanup                    R13
full fault-injection matrix       R14
Central Asset Storage F5          paused until R14+
```

---

## 10. Final status

```text
R8-A        CLOSED / GREEN
R8-B        CLOSED / GREEN
R8-C        CLOSED / GREEN
R8-D0→D7    CLOSED / GREEN
R8-E0→E7    CLOSED / GREEN
R8-F0→F7    CLOSED / GREEN

R8-G        NOT IMPLEMENTED
R9+         NOT IMPLEMENTED
```

**R8-F exit gate is satisfied at tested code HEAD `006c7016`.**
