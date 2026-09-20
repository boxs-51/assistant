# Phase 4 — Agent Runtime

## 1. Mục tiêu

Phase 4 mở rộng hệ thống từ single-agent metadata registry thành nền tảng hỗ trợ nhiều agent hoạt động trong cùng một phiên.

Mục tiêu hiện tại:

- Một session có thể có nhiều agent.
- Agent có thể gửi message cho agent khác.
- Agent có thể tạo task cho agent khác.
- Task có thể liên kết với task cha.
- Có ownership và membership isolation.
- Có thể hủy task hoặc đóng session.
- Tính năng mới hoạt động opt-in, không thay đổi API cũ.

Phạm vi này là nền tảng control/orchestration ban đầu cho multi-agent. Inference loop đầy đủ và distributed execution chưa thuộc lát triển khai hiện tại.

---

## 2. Trạng thái tổng quan

| Hạng mục | Trạng thái |
|---|---|
| Audit extension points | Hoàn thành |
| Multi-agent domain contracts | Hoàn thành |
| MultiAgentCoordinator | Hoàn thành |
| Multi-agent session lifecycle | Hoàn thành |
| Agent-to-agent messaging | Hoàn thành |
| Task delegation | Hoàn thành |
| Task cancellation | Hoàn thành |
| Owner/membership isolation | Hoàn thành |
| Opt-in API routes | Hoàn thành |
| Backward compatibility với API cũ | Được giữ nguyên |
| SQL persistence | Đã bổ sung model/repository/migration; cần chạy migration môi trường triển khai |
| Execution envelope/state machine | Đã triển khai |
| Parallel agent execution | Đã triển khai bounded coordinator method |
| Supervisor strategy | Đã triển khai sequential supervisor method |
| Agent inference loop | Đã nối task executor opt-in với ProviderRuntime; tool-loop đầy đủ còn tiếp tục |
| Distributed coordination | Chưa triển khai |

---

## 3. Kiến trúc hiện tại

```text
Client
  |
  |  Opt-in /v1/multi-agent/*
  v
Multi-Agent Router
  |
  v
MultiAgentCoordinator
  |-- AgentRegistry
  |-- AgentSession store
  |-- AgentTask store
  `-- AgentMessage store
```

Các API cũ vẫn giữ đường xử lý riêng:

```text
/v1/chat/completions
/v1/models
/v1/tools
/v1/embeddings
/v1/files
```

Multi-agent hiện chưa tự động thay thế chat runtime cũ.

---

## 4. Domain contracts

File: `src/domain/schemas/multi_agent.py`

### AgentSession

Đại diện cho một phiên làm việc multi-agent:

```text
session_id
owner_user_id
agent_ids
status
created_at
updated_at
```

### AgentMessage

Đại diện cho message giữa user và agent hoặc giữa các agent:

```text
message_id
session_id
sender_id
recipient_id
message_type
payload
created_at
```

Các loại message hiện tại:

```text
user.message
task.request
task.result
agent.message
```

### AgentTask

Đại diện cho task được giao cho một agent:

```text
task_id
session_id
created_by
assigned_agent_id
parent_task_id
status
input
output
error
created_at
updated_at
```

Trạng thái task:

```text
CREATED
ASSIGNED
RUNNING
COMPLETED
FAILED
CANCELLED
```

---

## 5. Coordinator

File: `src/runtimes/agent/coordinator.py`

`MultiAgentCoordinator` hiện chịu trách nhiệm:

- Tạo agent session.
- Kiểm tra agent đã đăng ký.
- Thêm agent vào session.
- Kiểm tra owner của session.
- Kiểm tra sender/recipient có thuộc session.
- Gửi agent message.
- Đọc message theo session.
- Tạo task delegation.
- Liên kết `parent_task_id`.
- Lấy task.
- Hủy task.
- Đóng session.

### Isolation rules

- Chỉ owner mới được truy cập session.
- Agent gửi message phải là member của session.
- Recipient phải là member của session.
- Agent được giao task phải là member của session.
- Agent không tồn tại trong `AgentRegistry` sẽ bị từ chối.

---

## 6. API mới

Các API này là opt-in và không thay đổi API cũ.

### Tạo session

```http
POST /v1/multi-agent/sessions
```

Request:

```json
{
  "agent_ids": ["planner", "worker"]
}
```

### Thêm agent vào session

```http
POST /v1/multi-agent/sessions/{session_id}/agents
```

Request:

```json
{
  "agent_id": "reviewer"
}
```

### Gửi message

```http
POST /v1/multi-agent/messages
```

Request:

```json
{
  "session_id": "as_123",
  "sender_id": "planner",
  "recipient_id": "worker",
  "message_type": "agent.message",
  "payload": {
    "text": "Start research"
  }
}
```

### Đọc message

```http
GET /v1/multi-agent/sessions/{session_id}/messages
```

### Tạo task

```http
POST /v1/multi-agent/tasks
```

Request:

```json
{
  "session_id": "as_123",
  "assigned_agent_id": "worker",
  "input": {
    "query": "Research the topic"
  },
  "parent_task_id": null
}
```

### Đọc task

```http
GET /v1/multi-agent/tasks/{task_id}
```

### Hủy task

```http
POST /v1/multi-agent/tasks/{task_id}/cancel
```

### Đóng session

```http
POST /v1/multi-agent/sessions/{session_id}/close
```

---

## 7. Bootstrap và dependency wiring

`MultiAgentCoordinator` dùng chung instance `AgentRegistry` với agent registration hiện tại.

Bootstrap được nối tại:

```text
src/main.py
```

Container hiện giữ:

```text
agent_registry
tool_registry
multi_agent_coordinator
```

Các API cũ tiếp tục dùng compatibility state trên `app.state`. Multi-agent router lấy coordinator từ application container.

---

## 8. Tương thích ngược

Phase 4 không thay đổi request/response contract của:

```text
POST /v1/chat/completions
/v1/models
/v1/tools
/v1/embeddings
/v1/files
```

Client cũ không cần gửi `mode: multi_agent` và không cần migrate.

Multi-agent chỉ được sử dụng khi client chủ động gọi namespace:

```text
/v1/multi-agent/*
```

Trong tương lai có thể thêm `mode: multi_agent` vào execution API mới mà không thay đổi behavior mặc định của chat API cũ.

---

## 9. Test evidence

File test:

```text
tests/architecture/test_phase4_multi_agent.py
```

Các scenario đã kiểm tra:

- Một session có nhiều agent.
- Agent-to-agent message.
- Task delegation.
- Task cancellation.
- Session owner isolation.
- Agent membership validation.

Kết quả focused test:

```text
3 passed, 1 warning
```

Warning hiện tại là Pydantic deprecation warning trong codebase, không làm test fail.

Các file Phase 4 cũng đã được kiểm tra bằng Pylance và không có lỗi được báo.

---

## 10. Giới hạn hiện tại

### 10.1. Runtime cache vẫn giữ in-memory

Coordinator vẫn giữ cache object trong memory để phục vụ request nhanh; các thao tác async mới đồng thời ghi qua durable store:

```text
_process restart -> cache mất, dữ liệu SQL còn nếu migration đã chạy
```

Chưa phù hợp cho production multi-instance.

### 10.2. Agent execution loop còn ở mức provider task

Coordinator đã thực thi task qua callback ProviderRuntime, nhưng chưa tự:

```text
load context
call provider
inspect tool call
execute capability
resume inference
```

### 10.3. Parallel execution chưa durable scheduler

Đã hỗ trợ bounded `asyncio.gather`; chưa có scheduler durable với:

```text
concurrency limit
join/barrier
partial failure policy
```

### 10.4. Supervisor mới là sequential policy

Chưa có policy engine đầy đủ cho:

```text
supervisor
planner
reviewer
consensus
retry-and-review
```

### 10.5. Chưa có durable event log

Message/task/execution records đã có SQL model/repository; event log/replay riêng chưa triển khai.

---

## 11. Kế hoạch triển khai tiếp theo

### Phase 4.1 — Durable persistence

Thêm các bảng:

```text
agent_sessions
agent_messages
agent_tasks
agent_executions
agent_relationships
```

Thêm repository và migration theo quy trình:

```text
expand
→ migrate
→ backfill
→ switch
→ contract
```

### Phase 4.2 — Agent execution envelope

Bổ sung:

```text
execution_id
parent_execution_id
correlation_id
causation_id
trace_id
```

### Phase 4.3 — Task state machine

Chuẩn hóa transition:

```text
CREATED → ASSIGNED → RUNNING → COMPLETED
                         ├────→ FAILED
                         └────→ CANCELLED
```

Chặn transition không hợp lệ.

### Phase 4.4 — Agent runtime execution

Tạo runtime thực thi:

```text
AgentRuntime
ExecutionManager
AgentExecutionContext
```

Runtime sẽ gọi:

```text
ContextRuntime
ProviderRuntime
CapabilityRuntime
```

thông qua event/command contract, không gọi trực tiếp implementation nội bộ.

### Phase 4.5 — Parallel execution

Thêm:

```text
max_parallel_agents
fan-out
fan-in
barrier
partial failure handling
```

### Phase 4.6 — Supervisor strategies

Hỗ trợ:

```text
SEQUENTIAL
PARALLEL
SUPERVISOR
DELEGATION
REVIEW_AND_RETRY
CONSENSUS
```

### Phase 4.7 — Streaming và observability

Bổ sung event:

```text
agent.session.started
agent.task.created
agent.task.assigned
agent.execution.started
agent.message.sent
agent.execution.completed
agent.execution.failed
agent.execution.cancelled
```

Các event này được phát qua SSE/WebSocket mới mà không ảnh hưởng stream cũ.

---

## 12. Acceptance criteria

Phase 4 mở rộng chỉ được xem là hoàn chỉnh khi:

- Multi-agent session được persistence.
- Nhiều agent có thể hoạt động độc lập trong cùng session.
- Agent có thể tạo task cho agent khác.
- Agent có thể gửi/nhận message có correlation.
- Có `execution_id` và `parent_execution_id`.
- Có state machine cho task/execution.
- Có timeout, cancellation và concurrency limit.
- Có authorization giữa agent và session.
- Một agent con lỗi không làm hỏng toàn bộ session nếu policy cho phép.
- Có thể resume sau restart.
- Có tracing từ user request đến agent/task chain.
- API cũ vẫn pass compatibility tests.
- Multi-agent chỉ chạy khi client chủ động opt-in.

---

## 13. Kết luận

Phần triển khai hiện tại đã tạo nền tảng multi-agent an toàn và không phá API cũ:

```text
Agent Registry
  ↓
Multi-Agent Session
  ↓
Agent Membership
  ↓
Message / Task Coordination
```

Đây là bước control-plane đầu tiên. Bước tiếp theo nên ưu tiên durable persistence và execution envelope trước khi triển khai supervisor hoặc parallel inference.
