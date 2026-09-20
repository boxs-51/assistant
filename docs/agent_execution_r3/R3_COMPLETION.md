# R3 — Execution Lineage Completion

Trạng thái: **COMPLETE**  
Ngày hoàn thành: **2026-09-20**  
Validated implementation SHA: `782a3fe64ad740875a4489d2676d29b01cb6178c`

## Kết quả

R3 đóng băng và triển khai đầy đủ execution lineage cho root Agent,
nested Agent delegation và durable reconstruction.

Canonical nested graph:

```text
Parent AgentExecution E1
    |
    +-- CapabilityInvocation I1
    |       execution_id = E1
    |
    +-- Child AgentExecution E2
            execution_id != E1
            parent_execution_id = E1
            causation_id = I1
```

Các invariant đã được chứng minh:

```text
1 AgentRuntime.execute()
=
1 execution_id
=
1 durable AgentExecution

I1.execution_id = E1
E2.parent_execution_id = E1
E2.causation_id = I1
```

Child E2 giữ cùng synchronous lineage:

```text
session_id
task_id
branch_id when present
correlation_id
trace_id
request_id
workflow_id
identity
```

Fresh delegation không tự tạo retry/fork lineage:

```text
retry_of_execution_id = null
base_execution_id = null
base_checkpoint_id = null
```

## Direct AGENT provenance

R3-D0 bổ sung typed provenance:

```text
CapabilityExecutionContext.caller_agent_execution_id
```

Agent-owned capability invocation:

```text
execution_id = E1
caller_agent_execution_id = E1
```

Direct/non-Agent invocation:

```text
caller_agent_execution_id = null
```

Vì vậy direct AGENT capability tạo root E2 với:

```text
parent_execution_id = null
```

và synthetic capability execution ID không thể trở thành dangling Agent
ancestry.

## Durable persistence

`agent_executions` có first-class:

```text
task_id
branch_id
parent_execution_id
retry_of_execution_id
base_execution_id
base_checkpoint_id
correlation_id
```

`context_state` round-trip:

```text
request_id
workflow_id
trace_id
causation_id
connection_id
limits
metadata
```

Real SQLite integration chứng minh E1 và E2 là hai row riêng, I1 vẫn thuộc E1,
iteration/tool-call/tool-result ownership không trộn execution, và E2 lineage
được reconstruct qua một `DurableAgentStore` mới.

## Test evidence

```text
D0/D1 contract                         6 passed
D2 real SQLite durable graph          1 passed
D3 Alembic migration smoke            1 passed
R3 A→C + R0/R2.1 regression          45 passed
Realtime/WS Phase 6.9→6.11           26 passed
Full se/tests + tools + cl/tests     494 passed, 4 warnings
```

## Known non-blocking warnings

- AnyIO `BlockingPortal` deprecation.
- passlib/argon2 version deprecation.
- Starlette HTTP 422 constant deprecation.
- Alembic `path_separator` deprecation warning.
- Windows asyncio Proactor subprocess cleanup warning.

Không warning nào ở trên làm sai R3 execution-lineage contract.

## Giới hạn có chủ đích

R3 không triển khai:

- active execution budget / wait TTL / deadline hierarchy — R4;
- shared TaskBudget / cancellation tree / delegation depth — R5;
- invocation reconciliation / durable replay — R6;
- checkpoint-directed resume protocol — R7;
- TaskBranch fork semantics — R8+.

## Next phase

R4 — **Active Budget / Wait TTL / Deadline Hierarchy**.
