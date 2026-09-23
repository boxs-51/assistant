# R8-E — COMPLETION / EXIT GATE

**Repository:** `boxs-51/assistant`  
**Canonical branch:** `r8-taskbranch-fork`  
**Work branch:** `work/r8-e-73a47b2c`  
**R8-D closed baseline:** `73a47b2c9f89a0bc86143a44a8218c1355acc645`  
**R8-E tested code HEAD:** `362c0561b9a0c8c7f83807e3a30269be0e4e901c`  
**Contract freeze:** `95b070173ee0b92f5be38c165edf71e1ad02bbf9`  
**Status:** **R8-E0→E7 CLOSED / GREEN**

---

## 1. Scope completed

R8-E implements the frozen branch-context reconstruction and read-only runtime handoff contract only.

Delivered:

- immutable/restart-safe `ForkRuntimeSeed v1`;
- migration `14d_r8_fork_runtime_seed`;
- runtime seed bound into `ForkPlan` semantics and plan fingerprint;
- same-UoW runtime-seed revalidation during R8-D consume;
- atomic persistence of `runtime_seed_json` and `runtime_seed_fingerprint` on `ForkAdmission`;
- user-only initial branch overlay policy;
- lookup of immutable ForkAdmission by execution id;
- read-only `ForkExecutionBootstrap`;
- exact receipt / Task / Branch / BranchContext / Execution / base-checkpoint lineage validation;
- checkpoint-directed strict COMMITTED-only branch transcript reconstruction;
- `AgentExecutionContext.branch_base_transcript`;
- `branch_runtime_seed_fingerprint`;
- explicit mutual exclusion between R7 resume seed and R8 FORK seed;
- `AgentContextHistoryMode.AUTO | EXPLICIT`;
- explicit empty FORK history never falls back to mutable Session history;
- runtime transcript seed selection:
  - FORK -> `branch_base_transcript`;
  - RESUME -> `resume_transcript`;
  - ordinary root -> Session/context history;
- reset execution-local counters for fork bootstrap;
- no inherited client/connection affinity.

R8-E does not start E2 and does not acquire runner ownership.

---

## 2. Implementation commits

The R8-E implementation is six commits ahead of the R8-D completion baseline:

```text
0bd92f54  R8-E0 immutable ForkRuntimeSeed + migration + atomic receipt persistence
5f122050  R8-E1→E5 read-only bootstrap + branch transcript + explicit history
9092b692  R8-E6 branch isolation / restart / tamper regression matrix
e692d518  align R8-C contract fixture with runtime seed semantics
25d1cb69  fix SQLAlchemy checkpoint metadata attribute collision
362c0561  advance historical migration-head expectation to 14d
```

The final scope diff from `73a47b2c` to `362c0561` changes only R8-E production/test blast-radius files.

It does **not** modify:

```text
se/src/runtimes/agent/coordinator.py
se/src/runtimes/agent/supervisor.py
se/src/main.py
se/src/transport/gateway/api/v1/multi_agent_router.py
se/src/transport/gateway/api/v1/events_router.py
se/src/runtimes/capability/drivers/agent_driver.py
cl/
```

Therefore no R8-F execution wiring is present.

---

## 3. Last-mile CI hardening

The first R8-E candidate exposed two real integration defects before closure.

### 3.1 SQLAlchemy metadata collision

`_build_runtime_seed()` originally attempted:

```python
checkpoint.metadata
```

before `checkpoint.metadata_json`.

For the SQLAlchemy checkpoint record, `metadata` resolves to SQLAlchemy's declarative `MetaData` object rather than the persisted checkpoint JSON payload. This caused:

```text
TypeError: 'MetaData' object is not iterable
```

and broke R8-D/E consume/bootstrap paths.

Fix:

- prefer the durable `metadata_json` field on SQL records;
- use the contract-level `metadata` mapping only when appropriate;
- reject non-mapping durable checkpoint metadata.

Commit:

```text
25d1cb69c24f53a9bb7e1dce5f5c4c0556dbf28c
fix(r8): avoid SQLAlchemy checkpoint metadata collision
```

### 3.2 Historical migration-head fixture

R8-E adds migration head:

```text
14d_r8_fork_runtime_seed
```

with:

```text
14d -> 14c -> 14b
```

The R8-B migration regression still expected `14c` as repository head.

The fixture was updated to prove the full linear chain instead of freezing an obsolete repository head.

Commit:

```text
362c0561b9a0c8c7f83807e3a30269be0e4e901c
test(r8): advance migration head expectation to 14d
```

---

## 4. Exact targeted CI evidence

A disposable CI branch was created from exact tested code HEAD `362c0561`:

```text
ci/r8-e-e7-362c0561
```

Its only additional commit is:

```text
83b055c100fdac8e6632c35a2d9a78f304b3ec22
ci(r8): probe R8-E7 hardening
```

That commit adds only:

```text
.github/workflows/r8-e-targeted-probe.yml
```

Production and test code are therefore identical to `362c0561`.

Targeted command:

```bash
python -m pytest -q \
  se/tests/architecture/test_r8_c_fork_contracts.py \
  se/tests/architecture/test_r8_c_fork_planning.py \
  se/tests/integration/test_r8_b_root_branch_accounting_migration.py \
  se/tests/integration/test_r8_d_atomic_fork_consume.py \
  se/tests/integration/test_r8_d_fork_admission_migration.py \
  se/tests/integration/test_r8_e_runtime_seed_migration.py \
  se/tests/integration/test_r8_e_branch_context_runtime_seed.py
```

Evidence:

- workflow: **R8-E Targeted Probe**
- run: `35811823214`
- job: `targeted-r8-e`
- job id: `107024866895`
- conclusion: **SUCCESS**

Exact pytest result:

```text
73 passed, 17 warnings in 15.96s
```

---

## 5. Exact full-suite evidence

The repository's existing Architecture Baseline ran directly on the exact work HEAD:

```text
362c0561b9a0c8c7f83807e3a30269be0e4e901c
```

### Linux full suite

- workflow: **Architecture Baseline**
- run: `35811794718`
- job: `linux-full-suite`
- job id: `107024779887`
- conclusion: **SUCCESS**

Exact result:

```text
891 passed, 1 skipped, 43 warnings in 94.09s
```

### Windows client contracts

- same run: `35811794718`
- job: `windows-client-contracts`
- job id: `107024780010`
- conclusion: **SUCCESS**

Exact result:

```text
68 passed in 8.09s
```

### Phase 5 exit gates

The existing Phase 5 workflow also ran on exact work HEAD `362c0561`:

- workflow: **Phase 5 Exit Gates**
- run: `35811794785`
- conclusion: **SUCCESS**

---

## 6. Frozen R8-E invariants proven

The regression matrix proves:

1. explicit empty branch history does not read Session history;
2. source prompt is not silently re-added under EXPLICIT mode;
3. Session messages appended after FORK do not change branch transcript;
4. sibling overlays remain isolated;
5. source execution may advance after FORK while the branch remains pinned to the admitted base checkpoint;
6. restart reconstruction produces the same runtime-seed fingerprint and branch transcript;
7. runtime seed tampering fails closed;
8. fork execution request tampering fails closed;
9. remaining active-budget tampering fails closed;
10. BranchContext revision/overlay tampering fails closed;
11. base checkpoint transcript tampering fails closed;
12. overlay roles `system`, `assistant`, and `tool` are rejected;
13. only `user` overlays are accepted in initial R8;
14. FORK and R7 RESUME transcript authority cannot coexist;
15. fork counters start at iteration/tool/retry/usage zero;
16. transport affinity is stripped from the runtime seed and bootstrap context;
17. migration `14d` is reversible and remains on one linear chain;
18. R8-D consume remains compatible with the new immutable runtime seed.

---

## 7. Exact R8-E -> R8-F boundary

The read-only handoff produced by R8-E is:

```text
ForkExecutionBootstrap
  execution_id = E2
  expected_execution_revision = 1
  immutable admission/seed evidence
  reconstructed AgentExecutionContext
  branch_base_transcript
  active budget still frozen
```

R8-E deliberately does **not**:

- call `AgentRuntime._begin_durable_execution(E2)`;
- call `AgentRuntime.execute()`;
- create a supervisor task;
- reserve or start a runner;
- expose public FORK HTTP/API routes;
- activate E2;
- terminalize the Task from one branch;
- implement ADOPT/SUPERSEDE/DISCARD/AGGREGATE.

R8-F must own a separate distributed activation fence:

```text
RUNNING@1 -> RUNNING@2
```

Only the durable CAS winner may dispatch inference/tools.

The next phase must perform a fresh E->F boundary audit before writing R8-F code.

---

## 8. Final status

```text
R8-A        CLOSED / GREEN
R8-B        CLOSED / GREEN
R8-C        CLOSED / GREEN
R8-D0→D7    CLOSED / GREEN
R8-E0→E7    CLOSED / GREEN

R8-F        NOT IMPLEMENTED
R8-G        NOT IMPLEMENTED
F5          PAUSED UNTIL R14+
```

**R8-E exit gate is satisfied.**
