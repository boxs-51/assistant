# R2 — Durable AgentExecution Authority + Revision/CAS

Trạng thái: **COMPLETE**  
Ngày hoàn thành: 2026-09-19

## Kết quả

- `AgentRuntime.execute()` tạo durable `AgentExecution` trước iteration đầu tiên.
- Resume chỉ claim execution đang `WAITING` và giữ nguyên execution ID.
- Startup mới đi qua `CREATED → RUNNING`; resume đi qua `WAITING → RUNNING`.
- Mỗi lifecycle mutation do runtime thực hiện dùng `expected_revision` và tăng revision đúng một đơn vị.
- Kết quả/error/state/completed timestamp được ghi trong cùng terminal CAS.
- Duplicate creation, stale revision và terminal resurrection bị từ chối.
- Hai resume cạnh tranh trên cùng revision chỉ có một winner.
- Coordinator truyền một execution/correlation ID duy nhất vào runtime, loại split identity `exec_*`/`agent_*` trên đường production.

## Transaction contract

```text
UPDATE agent_executions
SET ..., revision = expected_revision + 1
WHERE id = execution_id
  AND revision = expected_revision
```

`rowcount != 1` là `ExecutionConflictError`. Caller phải reload durable state; không được tự suy đoán và chạy tiếp.

## Vòng đời durable

```text
NEW:    absent -> INSERT CREATED@0 -> CAS RUNNING@1
RESUME: WAITING@n -> CAS RUNNING@(n+1)
END:    RUNNING@n -> CAS TERMINAL/WAITING@(n+1)
```

Checkpoint/iteration chỉ được ghi sau khi record đã tồn tại và đã được claim `RUNNING`.

## Mã liên quan

- `se/src/runtimes/agent/runtime.py`
- `se/src/runtimes/agent/persistence.py`
- `se/src/infrastructure/storage/repositories/agent.py`
- `se/src/infrastructure/storage/models/sql/agent/execution.py`
- `se/src/main.py`
- `se/src/runtimes/agent/coordinator.py`
- `se/src/infrastructure/storage/migrations/sql/versions/8a_agent_execution_waiting_cas.py`

## Bằng chứng

- Test record tồn tại và ở `RUNNING` trước lần ghi iteration đầu tiên.
- Test terminal CAS tạo `COMPLETED@2` từ execution mới.
- Test stale revision giữ record nguyên trạng.
- Test hai resume đồng thời: một thành công, một `ExecutionConflictError`.
- Test CAS trực tiếp trên SQL repository xác nhận stale revision có `rowcount=0`.
- Toàn bộ SE + CL regression: 375 test pass.

## Giới hạn có chủ đích

- Lease/owner instance và crash recovery thuộc R12.
- Execution lineage đầy đủ (`retry_of`, `base_execution`, `branch_id`) thuộc R3.
- Generic coordinator callback chỉ còn dùng được ở chế độ in-memory để tương thích test/API cũ; khi có durable store, callback bắt buộc nhận `execution_id` và giao vòng đời cho `AgentRuntime`.
