# AE-R14-E P1A — Physical server hard-restart evidence (candidate)

Scope: issue #359, test/evidence only. This is **not** a production exit certificate.

## What the executable test actually attempts

Node: `se/tests/e2e/test_r14_e_sql_restart_agent_hitl_k1_k2.py::test_r14_e_decided_high_risk_k1_terminal_recovers_after_real_s1_kill`

1. Launch OS process S1 with a real Uvicorn TCP WebSocket gateway, AgentRuntime, durable AgentStore, SQL capability invocation store, and either file-backed SQLite or a genuine PostgreSQL 17 database through asyncpg.
2. Connect a surviving client K1 over real TCP. Require a real HIGH-risk HITL **APPROVE** callback before executing a `NON_IDEMPOTENT` callable. Persist its external side effect into a **separate SQLite database**, not a process-local list or server invocation ledger.
3. Hold `capability.result` only **after the client SQLite ledger has committed TERMINAL**. Disconnect K1, require S1 to durably record `WAITING` and a checkpoint. The K1 terminal frame must not reach S1.
4. Call actual `kill()` on OS process S1 (SIGKILL / TerminateProcess), prove nonzero termination and record its PID. No graceful `uvicorn.should_exit`, `AgentExecutionSupervisor.shutdown`, or SQL engine dispose is allowed in S1 before this cut.
5. Launch new OS process S2 with a different PID and fresh Uvicorn, AgentRuntime, capability/connection services, and the **same** durable server database (SQLite or PostgreSQL 17). Connect K2 with the original installation/principal and original client ledger. K2 must reconcile the committed K1 result without a new approval or target execution.
6. Read SQL from an independent parent connection. Require Agent `COMPLETED`, matching original invocation identity, one canonical `COMMITTED` tool result, one K1 dispatch attempt with a K2 resume binding, a `CONSUMED` ResumeClaim, external effect count exactly **one**, approval count exactly **one**, no second approval, and no pending resume tickets.

The test is designed to fail, not silently skip or xfail, if a real child cannot launch, S1 does not reach `WAITING`, K2 cannot recover, the result was not committed, an unsafe replay occurs, or the OS kill does not happen.

## Important limits

This witnesses **the already-DECIDED terminal HITL** case, not PENDING consent across server death. HITL callback evidence in the separate test database is test instrumentation, **not** a product-level authenticated or durable consent record. #359 E-P1B remains P0 HOLD.

Two real server SQL backends are now exercised independently:
- **SQLite** remains the ordinary portable Architecture/full-suite node `se/tests/e2e/test_r14_e_sql_restart_agent_hitl_k1_k2.py::test_r14_e_decided_high_risk_k1_terminal_recovers_after_real_s1_kill`.
- **PostgreSQL 17.11** runs only in the dedicated native Linux and Windows workflow through explicitly-selected `se/tests/e2e/r14_e_postgres17_hard_restart_evidence.py::test_postgres17_real_s1_kill_and_k2_rediscovery`. The non-`test_*.py` filename deliberately avoids requiring PostgreSQL in the baseline full suite. The dedicated runner must provide a real `postgresql+asyncpg://` DSN and read back an actual server version in the `170000..179999` range, against the isolated `r14e_p1a` database; **no silent skip, xfail, fallback or mock** is permitted. Linux runs `postgres:17.11` as a PostgreSQL service container. Native Windows Server 2025 explicitly enables and starts the preinstalled `postgresql-x64-17` service before test execution.
- The same S1 OS hard kill, S2 new PID, Agent/ResumeClaim/server SQL/CL terminal K1→K2 checks and independent client effect sink are required in BOTH PostgreSQL jobs. PostgreSQL fixture users are flushed before dependent chat sessions in one transaction to preserve real FK enforcement.

The original #409 PostgreSQL races are separate evidence, not borrowed for this scenario. **The R7-J-based server harness covers Agent/Checkpoint/ResumeClaim and R6/CL durable reconciliation but is still NOT a full UBQ TaskBudget/multiworker quota settlement proof.** No production runtime, schema, migration, client HITL handler or sandbox native behavior is changed by this candidate.

## Failure-cut matrix

| Cut / property | Candidate status |
|---|---|
| HIGH-risk APPROVED locally before effect | Real client callback; externally observed |
| After effect and CL terminal commit / before K1 result frame | Deterministic held send boundary |
| S1 durable WAITING/checkpoint | Required before kill |
| Actual S1 OS hard kill / S2 new PID | Executable |
| Same server durable SQLite across two OS PIDs | Executable |
| Original K1 invocation, one dispatch attempt, K2 resumed connection and claim binding | Actual SQL/claims/attempts assertions; terminal reconciliation does not mint an extra attempt |
| No second external NON_IDEMPOTENT effect | Separate SQLite sink assertion |
| No duplicate HIGH-risk approval | Count and callback assertions |
| ResumeClaim consumed | Actual SQL assertion |
| PostgreSQL 17 hard-kill/restart + K1/K2, Ubuntu | **PASS** on real PostgreSQL 17.11, targeted CI #38221413008, 1 passed |
| PostgreSQL 17 hard-kill/restart + K1/K2, native Windows | **PASS** on real PostgreSQL 17, targeted CI #38221413008, 1 passed |
| PostgreSQL general isolation/race/multiworker parity | **NOT_PROVEN** by this single composed scenario |
| Pending HITL approval crossing crash | **HOLD_P0** |
| UBQ/TaskBudget settlement under multiworker | **NOT_PROVEN** |
| Native Windows SQLite and PostgreSQL E-P1A | **PASS** (two distinct targeted CI jobs; PostgreSQL run #38221413008) |
| Native process/FS/network NONE / VERIFIED_EMPTY | Out of scope; #164/#156 |
| Full AE-R14 global production exit | **HOLD** |

## CI and handling of RED

The Linux Architecture baseline uses `python -m pytest -q` and continues to select the SQLite node; the existing targeted SQLite workflow runs it on both native OSes. The expanded dedicated workflow has two further **explicit** PostgreSQL 17 jobs (Linux container and native Windows service), each installing an evidence-only pinned `asyncpg==0.32.0`. The PostgreSQL jobs use `--basetemp`, JUnit XML and `actions/upload-artifact@v4` to retain real S1/S2 child logs, server SQL proof, and a separate SQLite side-effect sink in their private runner artifacts. Do not treat service readiness/collection-only success as passed physical-kill evidence.

Historical transparent REDs: first #38220694644 was a missing pytest `--basetemp` parent directory in workflow; #38221022969 then reached real PG schema and revealed `sessions.user_id` FK parent flush ordering. Both were fixed without loosening FK, hard-kill, effect or no-replay assertions. Targeted PostgreSQL Linux and Windows were each **1 passed** in #38221413008. Neither RED is recast as product defect or hidden. The full Architecture run for the final documentation HEAD must be separately consumed; no implicit production PASS is granted by this test-only document.

No merge is requested.


## 2026-10-12 — E-P1A extended negative fault-cuts and accounting candidate

**New candidate test cases (implementation pending exact-HEAD CI evidence):**

- `approve_terminal`: HIGH-risk approve, durable K1 TERMINAL and independent external effect before the K1 result wire is withheld, real S1 OS kill, new S2, K2 reconciles one result without replay.
- `deny_terminal`: HIGH-risk deny becomes durable `TERMINAL/error/HITL_DENIED` before the K1 error wire is withheld; external effect count stays **zero**; S2/K2 preserves terminal denial, and capability invocation is expected to be `FAILED` rather than `COMPLETED`.
- `effect_before_terminal`: K1 runs NON_IDEMPOTENT callable, commits one effect to independent SQLite, then blocks BEFORE returning and committing CL TERMINAL. S1 dies after K1 disconnect and durable WAITING checkpoint. K2 must reconcile authentic `UNKNOWN` over real WebSocket with CL ledger `RUNNING`; fail-closed planning is permitted to defer **without creating** a ResumeClaim. No second attempt, no committed success projection and no duplicate effect are allowed.

Each scenario uses its own fresh `r14e_p1a_*` PostgreSQL 17 database, while the original SQLite tests each use isolated pytest directories. A single database reused across scenarios would be invalid evidence because the R7-J fixture carries fixed Task/Execution IDs.

**Accounting witness (test-only; not a production configuration change):**
The test harness now binds a real `TaskBudgetService` to S1 and S2, creates an authenticated owner-associated Task and TaskBudget, and examines durable `agent_tasks`, `agent_task_budgets` and `agent_task_budget_reservations` from independent SQL. Required invariants include exactly one NEW_EXECUTION and TOOL_CALL reservation, `used_executions=1`, `used_tool_calls=1`, `active_executions=0` at stable evidence readback and no duplicate reservation key. UBQ2 dual-accounting enrollment is enabled in the test's UoW; one owner binding, one mirrored TOOL_CALL bridge and the corresponding `SETTLED` user-budget reservation of one unit are asserted.

**Hard evidence boundary:** this covers UBQ2 shadow dual-accounting for an admitted TaskBudget tool call. It is NOT a claim that UBQ3 finite user-tool quota or UBQ4 inference quota under multitenant contention, multiworker simultaneous admissions, PostgreSQL database-server failover, E-P1B pending HITL, or global AE-R14-E exit are certified. These remain separate gates.

**CI declaration:** the targeted SQLite jobs select the whole test module and PostgreSQL 17 jobs select the three parametrized fault cuts; no skip/xfail and no SQLite substitution for PostgreSQL. Earlier 4/4 APPROVE-only PASS is historical positive-cut evidence. Do not call this extension FINAL until the exact-head native Ubuntu/Windows PostgreSQL 17 + SQLite jobs and Architecture jobs report terminal success, with raw artifacts inspected. Failures must remain RED until repaired. No merge.
