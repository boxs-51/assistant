# AE-R14-E P1A — Physical server hard-restart evidence (candidate)

Scope: issue #359, test/evidence only. This is **not** a production exit certificate.

## What the executable test actually attempts

Node: `se/tests/e2e/test_r14_e_sql_restart_agent_hitl_k1_k2.py::test_r14_e_decided_high_risk_k1_terminal_recovers_after_real_s1_kill`

1. Launch OS process S1 with a real Uvicorn TCP WebSocket gateway, AgentRuntime, durable AgentStore, SQL capability invocation store, and file-backed SQLite.
2. Connect a surviving client K1 over real TCP. Require a real HIGH-risk HITL **APPROVE** callback before executing a `NON_IDEMPOTENT` callable. Persist its external side effect into a **separate SQLite database**, not a process-local list or server invocation ledger.
3. Hold `capability.result` only **after the client SQLite ledger has committed TERMINAL**. Disconnect K1, require S1 to durably record `WAITING` and a checkpoint. The K1 terminal frame must not reach S1.
4. Call actual `kill()` on OS process S1 (SIGKILL / TerminateProcess), prove nonzero termination and record its PID. No graceful `uvicorn.should_exit`, `AgentExecutionSupervisor.shutdown`, or SQL engine dispose is allowed in S1 before this cut.
5. Launch new OS process S2 with a different PID and fresh Uvicorn, AgentRuntime, capability/connection services, and the **same** server SQLite. Connect K2 with the original installation/principal and original client ledger. K2 must reconcile the committed K1 result without a new approval or target execution.
6. Read SQL from an independent parent connection. Require Agent `COMPLETED`, matching original invocation identity, one canonical `COMMITTED` tool result, K1/K2 attempt sequence, a `CONSUMED` ResumeClaim, external effect count exactly **one**, approval count exactly **one**, no second approval, and no pending resume tickets.

The test is designed to fail, not silently skip or xfail, if a real child cannot launch, S1 does not reach `WAITING`, K2 cannot recover, the result was not committed, an unsafe replay occurs, or the OS kill does not happen.

## Important limits

This witnesses **the already-DECIDED terminal HITL** case, not PENDING consent across server death. HITL callback evidence in the separate test database is test instrumentation, **not** a product-level authenticated or durable consent record. #359 E-P1B remains P0 HOLD.

The database in this first implementation is server **SQLite**, not PostgreSQL. The existing #409 PostgreSQL race tests are NOT composed R14-E P1A evidence. Native Windows selection and a real PostgreSQL composed execution lane must be separately demonstrated; no workflow has been widened implicitly. The R7-J-based server harness covers Agent/Checkpoint/ResumeClaim and R6/CL durable reconciliation but is **not** a full UBQ TaskBudget/multiworker quota settlement proof. No production runtime, schema, migration, client HITL handler or sandbox native behavior is changed by this candidate.

## Failure-cut matrix

| Cut / property | Candidate status |
|---|---|
| HIGH-risk APPROVED locally before effect | Real client callback; externally observed |
| After effect and CL terminal commit / before K1 result frame | Deterministic held send boundary |
| S1 durable WAITING/checkpoint | Required before kill |
| Actual S1 OS hard kill / S2 new PID | Executable |
| Same server durable SQLite across two OS PIDs | Executable |
| Original K1→K2 principal, invocation, fingerprint | R7-J/R6 invariants; actual DB and attempts assertions |
| No second external NON_IDEMPOTENT effect | Separate SQLite sink assertion |
| No duplicate HIGH-risk approval | Count and callback assertions |
| ResumeClaim consumed | Actual SQL assertion |
| PostgreSQL SQL parity | **NOT_RUN** |
| Pending HITL approval crossing crash | **HOLD_P0** |
| UBQ/TaskBudget settlement under multiworker | **NOT_PROVEN** |
| Windows native execution | **NOT_RUN until actual CI reports** |
| Native process/FS/network NONE / VERIFIED_EMPTY | Out of scope; #164/#156 |
| Full AE-R14 global production exit | **HOLD** |

## CI and handling of RED

The repository Linux Architecture baseline uses `python -m pytest -q`, which selects this test; a dedicated native Windows selector is a separate scope decision. Run the focused node explicitly on Linux and Windows before treating either platform as covered. Retain real child logs and SQL/effect evidence. Never interpret collection-only success as a passed process witness, and never turn an environmental or semantic RED into a skip.

No merge is requested.
