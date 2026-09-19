# R1 — WAITING Normalization + Compatibility

Trạng thái: **COMPLETE**  
Ngày hoàn thành: 2026-09-19

## Kết quả

- Execution dùng duy nhất giá trị canonical `WAITING`.
- `AgentExecutionWaitReason` có đủ các reason đã đóng băng ở R0.
- Schema enforce hai invariant `WAITING/reason` theo cả hai chiều.
- State machine chỉ dùng `WAITING`; terminal → `RUNNING` vẫn bị cấm.
- Task DTO dùng `WAITING` và `wait_reasons[]`.
- Continuation nội bộ dùng `WAITING`; tên cũ chỉ còn alias/parser compatibility.
- CL normalize cả payload legacy và canonical.
- Migration chuyển dữ liệu `WAITING_FOR_CONNECTION`/`WAITING_AGENT` sang canonical.

## Tương thích rollout

| Producer | Payload | CL mới |
|---|---|---|
| SE cũ | `WAITING_FOR_CONNECTION` | Normalize thành `WAITING + CONNECTION` |
| SE cũ | `WAITING_AGENT` | Normalize thành `WAITING + AGENT` |
| SE mới | `WAITING + wait_reason` | Dùng trực tiếp |

Trong R1, workflow SE vẫn phát `status=WAITING_FOR_CONNECTION` cho CL cũ nhưng kèm `wait_reason=CONNECTION`. Đây là adapter ở biên; state bền vững và runtime không phụ thuộc enum cũ.

## Mã liên quan

- `se/src/domain/schemas/agent_execution.py`
- `se/src/runtimes/agent/state_machine.py`
- `se/src/runtimes/agent/contracts/continuation.py`
- `se/src/runtimes/agent/continuation.py`
- `se/src/domain/schemas/multi_agent.py`
- `cl/src/core/gateway_client.py`
- `se/src/infrastructure/storage/migrations/sql/versions/8a_agent_execution_waiting_cas.py`

## Bằng chứng

- Contract suite R0–R2: 9 test pass.
- Toàn bộ SE + CL regression: 375 test pass.
- Test xác nhận legacy normalization, canonical serialization, reason invariant và terminal non-resurrection.

## Giới hạn có chủ đích

- Active budget/wait TTL thuộc R4.
- Invocation reconciliation và durable reconnect đầy đủ thuộc R6–R7.
- Alias source cũ chỉ phục vụ migration và phải được gỡ ở R13 sau telemetry/rollout.
