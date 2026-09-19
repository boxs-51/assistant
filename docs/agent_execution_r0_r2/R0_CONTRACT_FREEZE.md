# R0 — Contract Freeze

Trạng thái: **COMPLETE**  
Ngày đóng băng: 2026-09-19

Tài liệu này là contract thực thi cho R1–R2 và là đầu vào bắt buộc cho R3+. Nội dung dưới đây không đồng nghĩa các phase sau R2 đã được triển khai.

## 1. Contract cốt lõi

| Khái niệm | Contract đóng băng |
|---|---|
| Task | Mục tiêu logic, sống lâu hơn một execution. |
| Branch | Một hướng giải độc lập; Task 1:N Branch. |
| Execution | Chính xác một lần gọi `AgentRuntime.execute()`; Branch 1:N Execution. |
| Checkpoint | Safe point bền vững, bất biến. |
| RESUME | Cùng task, branch và execution ID. |
| RETRY | Cùng task/branch, execution ID mới. |
| FORK | Cùng task, branch ID mới và execution ID mới. |
| SUBTASK | Task ID mới, có `parent_task_id`. |

Lineage không được dùng lẫn: `parent_execution_id` chỉ dành cho delegation; `retry_of_execution_id` dành cho retry; `base_execution_id/base_checkpoint_id` dành cho fork/recovery.

## 2. Ma trận trạng thái Execution

| Từ | Đến hợp lệ | Điều kiện |
|---|---|---|
| `CREATED` | `RUNNING`, `CANCELLED` | `CREATED → RUNNING` phải được claim bằng CAS. |
| `RUNNING` | `WAITING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT` | `WAITING` bắt buộc có `wait_reason`; terminal không có reason. |
| `WAITING` | `RUNNING`, `FAILED`, `CANCELLED`, `TIMEOUT` | Resume giữ nguyên execution ID và claim bằng CAS. |
| Terminal | Không có | Không bao giờ resurrect terminal execution. |

Invariant:

```text
state == WAITING  => wait_reason != NONE/null
state != WAITING  => wait_reason == NONE/null
```

## 3. Ma trận tổng hợp Task

| Điều kiện ưu tiên | Task state |
|---|---|
| Task bị hủy rõ ràng | `CANCELLED` |
| Có kết quả authoritative đã accept | `COMPLETED` |
| Có execution `CREATED/RUNNING` | `RUNNING` |
| Có execution resumable `WAITING` | `WAITING`; `wait_reasons` là hợp các reason đang chặn |
| Còn quyền retry/fork | Scheduler quyết định `WAITING` hoặc `FAILED` |
| Không còn đường tiến | `FAILED` |

Một branch thất bại không tự động làm Task thất bại.

## 4. Ma trận Branch resolution

| State | Ý nghĩa | Có thể chuyển đến |
|---|---|---|
| `OPEN` | Còn là ứng viên | `ADOPTED`, `SUPERSEDED`, `DISCARDED`, `CANCELLED` |
| `ADOPTED` | Kết quả được chấp nhận | Terminal |
| `SUPERSEDED` | Bị kết quả khác thay thế | Terminal |
| `DISCARDED` | Bị loại theo policy/user | Terminal |
| `CANCELLED` | Bị hủy | Terminal |

Branch không lưu bản sao execution lifecycle state; UI derive từ current execution.

## 5. Ma trận identity/lineage

| Trường | Chủ thể cấp | Quy tắc |
|---|---|---|
| `task_id` | Task runtime | Giữ nguyên qua resume/retry/fork. |
| `branch_id` | Task runtime | Giữ qua resume/retry; mới khi fork. |
| `execution_id` | Agent runtime boundary | Một ID cho một lần `execute`; giữ khi resume. |
| `parent_execution_id` | Agent runtime | Chỉ child-agent delegation. |
| `retry_of_execution_id` | Task/Agent runtime | Chỉ retry. |
| `base_execution_id` | Task runtime | Nguồn fork/recovery. |
| `checkpoint_id` | Continuation runtime | Safe point bất biến. |
| `invocation_id` | Capability runtime | Giữ nguyên khi reconcile. |

## 6. Ma trận timeout

| Đồng hồ | Chạy khi | Dừng khi |
|---|---|---|
| Active execution budget | `CREATED/RUNNING` | `WAITING` hoặc terminal |
| Wait TTL | `WAITING` | Resume hoặc terminal |
| Iteration/inference/tool timeout | Operation tương ứng đang chạy | Operation kết thúc |
| Parent deadline | Luôn là upper bound của child | Parent kết thúc |

Chi tiết lưu/reconstruct active budget thuộc R4, chưa triển khai trong R0–R2.

## 7. Ma trận resume trigger

| Wait reason | Auto-resume mặc định | Quyền |
|---|---:|---|
| `CONNECTION` | Có | Cùng `user_id`, cùng `client_id`, connection mới |
| `HUMAN_APPROVAL` | Không | Quyết định HITL hợp lệ |
| `DEPENDENCY` | Có | Dependency hoàn tất |
| `RESOURCE` | Theo policy | Policy/resource owner |
| `EXPLICIT_PAUSE` | Không | User/control-plane |
| `RECOVERY` | Theo policy | Recovery coordinator |
| `RETRY_BACKOFF` | Có khi lịch đến hạn | Scheduler |
| `AGENT` | Theo ownership | Agent/task runtime |

## 8. Error taxonomy

| Nhóm | Ví dụ | Retry trực tiếp? |
|---|---|---:|
| Contract | invalid transition, missing wait reason | Không |
| Conflict | duplicate creation, stale revision, resume race | Không; reload state |
| Authorization | wrong user/client | Không |
| Timeout | active budget, wait TTL | Theo policy |
| Cancellation | task/execution/user cancel | Không tự động |
| Remote unknown outcome | mất kết nối sau dispatch | Không replay mù |
| Provider transient | rate limit/unavailable | R10 policy |
| Persistence | commit/CAS/storage failure | Chỉ retry khi chứng minh an toàn |

## 9. Ma trận quyền sở hữu persistence

| Entity | Authority |
|---|---|
| Session, Task, Branch, TaskBudget | Task runtime / MultiAgentCoordinator |
| AgentExecution, iteration, tool call/result, execution checkpoint | AgentRuntime |
| CapabilityInvocation/Attempt | CapabilityRuntime |
| Provider request/retry/fallback | ProviderRuntime |

`AgentRuntime.execute()` phải tạo hoặc claim durable execution trước iteration đầu tiên.

## 10. Ma trận tương thích protocol

| Input/Wire | Canonical nội bộ | Giai đoạn R1 |
|---|---|---|
| `WAITING_FOR_CONNECTION` | `WAITING + CONNECTION` | Đọc được |
| `WAITING_AGENT` | `WAITING + AGENT` | Đọc được |
| `WAITING + wait_reason` | Không đổi | Đọc được |
| `WAITING` thiếu reason | Invalid | Từ chối |
| Non-WAITING có reason | Invalid | Từ chối |

SE có thể phát legacy wire form trong cửa sổ migration nhưng phải kèm `wait_reason`; CL mới normalize về canonical.

## 11. Idempotency, TaskBudget và protocol đã đóng băng

- Capability idempotency: `IDEMPOTENT`, `RECONCILABLE`, `NON_IDEMPOTENT`.
- Unknown non-idempotent outcome không được replay tự động.
- TaskBudget được chia sẻ cho child agents, retries và forks; retry/fork không reset budget.
- Multi-worker correctness dựa trên DB transaction/CAS, không dựa vào lock trong process.

## 12. Contract tests

Test thực thi nằm tại `se/tests/architecture/test_roadmap_r0_r2.py` và bao phủ invariant WAITING, legacy normalization, terminal non-resurrection, stale CAS và resume race. Các contract dành cho R3+ được đóng băng ở tài liệu này nhưng chỉ trở thành exit gate triển khai ở phase tương ứng.

