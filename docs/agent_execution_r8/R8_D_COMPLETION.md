# R8-D — COMPLETION / EXIT GATE

**Repository:** `boxs-51/assistant`  
**Branch:** `r8-taskbranch-fork`  
**R8-D code baseline:** `1fa71f1001972565c2bc07efeb6e846ccbdf3ae7`  
**R8-E freeze docs-only HEAD before this completion:** `95b070173ee0b92f5be38c165edf71e1ad02bbf9`  
**Status:** **R8-D CLOSED / GREEN**

---

## 1. Scope completed

R8-D implements the frozen atomic FORK consume contract:

- revalidate Task / Branch / Execution / Checkpoint / side effects / transcript / budget inside one UoW;
- atomically create one new `TaskBranch`;
- atomically create one new `TaskBranchContext`;
- atomically create one new `AgentExecution RUNNING@1`;
- charge `BRANCH` and `NEW_EXECUTION` exactly once;
- persist immutable `ForkAdmission` idempotency authority;
- preserve source Branch / source Execution immutability;
- preserve delegation lineage across the FORK boundary;
- reject stale/unsafe checkpoint authority;
- replay the same `fork_request_id` without a second budget charge;
- perform no runner start and expose no public FORK endpoint.

The last R8-D hardening at `1fa71f10` also performs consume revalidation under the Task row lock when the repository supports `get_task_for_update`.

---

## 2. Exact CI evidence

The production code under test was exactly the current branch code at:

```
95b070173ee0b92f5be38c165edf71e1ad02bbf9
```

Relative to the R8-D code baseline `1fa71f10`, this commit adds only the canonical R8-E contract-freeze document. No production code changed.

Because no historical workflow run existed for `95b07017`, a disposable CI probe branch was created:

```
ci/r8-d7-95b07017
```

from exactly `95b07017`. Its only additional commit:

```
37d1ebc125095409f1836e6bea29d93a0f1d1a5f
ci(r8): probe R8-D7 targeted and full suite
```

adds only `.github/workflows/r8-d7-probe.yml`; product and test code remain identical to `95b07017`.

### 2.1 Targeted R8-D regression

Command:

```bash
python -m pytest -q   se/tests/integration/test_r8_d_atomic_fork_consume.py   se/tests/integration/test_r8_d_fork_admission_migration.py
```

Evidence:

- GitHub Actions run: `35808790191` — **R8-D7 CI Probe**
- job: `targeted-r8-d`
- job id: `107015410680`
- result: **SUCCESS**
- exact pytest result:

```
25 passed, 4 warnings in 6.27s
```

### 2.2 Full Linux suite

Command:

```bash
python -m pytest -q
```

Evidence:

- GitHub Actions run: `35808790191`
- job: `linux-full-suite`
- job id: `107015410591`
- result: **SUCCESS**
- exact pytest result:

```
879 passed, 1 skipped, 40 warnings in 80.70s
```

The repository's existing independent **Architecture Baseline** push workflow also completed successfully:

- run: `35808790088`
- conclusion: **SUCCESS**

### 2.3 Windows client contracts

Command:

```powershell
python -m pytest -q cl/tests
```

Evidence:

- GitHub Actions run: `35808790191`
- job: `windows-client-contracts`
- job id: `107015410396`
- result: **SUCCESS**
- exact pytest result:

```
68 passed in 8.19s
```

### 2.4 Phase 5 exit gates

The existing push workflow also ran from the same CI probe commit:

- workflow: **Phase 5 Exit Gates**
- run: `35808790315`
- conclusion: **SUCCESS**

---

## 3. R8-D invariants proven

The R8-D targeted matrix covers the frozen consume semantics, including:

1. fresh FORK creates exactly one Branch + BranchContext + Execution;
2. new execution is `RUNNING@1`;
3. source execution remains at the source WAITING checkpoint;
4. source Branch remains unchanged;
5. fork Branch points to the exact source execution/checkpoint;
6. fork execution has no inherited client/connection affinity;
7. TaskBudget branch/execution accounting increments exactly once;
8. duplicate `fork_request_id` replays the committed result;
9. duplicate replay does not recharge TaskBudget;
10. changed semantics under the same request identity fail closed;
11. delegated FORK preserves the existing parent-execution lineage instead of making the source execution its parent;
12. second-hop FORK keeps the same delegation ancestry contract;
13. checkpoint branch mismatch fails atomically;
14. migration `14c_r8_fork_admission` is reversible and remains the single migration head for the R8-D baseline.

---

## 4. Boundary to R8-E

R8-D deliberately does **not**:

- reconstruct the fork execution runtime context;
- copy the base transcript into BranchContext;
- use R7 `resume_transcript` for FORK;
- start `AgentRuntime`;
- reserve supervisor ownership;
- expose a public FORK endpoint;
- implement branch-result resolution.

The canonical R8-E boundary is frozen in:

```
docs/agent_execution_r8/
R8_E_BRANCH_CONTEXT_RUNTIME_HANDOFF_CONTRACT_FREEZE_1FA71F10.md
```

Canonical freeze commit:

```
95b070173ee0b92f5be38c165edf71e1ad02bbf9
```

R8-E owns branch-aware context reconstruction and read-only runtime bootstrap only.

R8-F remains responsible for the later distributed execution activation fence and runner/API wiring.

---

## 5. Final status

```
R8-A        CLOSED / GREEN
R8-B        CLOSED / GREEN
R8-C        CLOSED / GREEN
R8-D0→D7    CLOSED / GREEN

R8-E        CONTRACT FROZEN / READY TO IMPLEMENT
R8-F        NOT IMPLEMENTED
R8-G        NOT IMPLEMENTED
```

**R8-D exit gate is satisfied.**
