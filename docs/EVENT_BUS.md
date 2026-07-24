1. Vai trò của Event Bus

Thay vì:

Ingress
    │
    ▼
Context Runtime
    │
    ▼
Workflow Runtime
    │
    ▼
Agent Runtime
    │
    ▼
Tool Runtime

Mọi Runtime gọi trực tiếp nhau.

Ta chuyển thành

               Internal Event Bus
        ┌─────────┼─────────┐
        │         │         │
        ▼         ▼         ▼
 Context     Workflow    Agent
 Runtime      Runtime    Runtime
        ▲         ▲         ▲
        │         │         │
        └─────────┼─────────┘
                  │
              Tool Runtime

Mỗi Runtime chỉ biết:

Publish Event
Subscribe Event

Không biết Runtime khác tồn tại.

Đây là nguyên lý Event-Driven Architecture (EDA).

2. Event Lifecycle

Giả sử người dùng gửi:

"Tóm tắt 5 email mới nhất"

Bước 1 — Ingress

Gateway nhận HTTP Request.

Tạo Event đầu tiên:

UserRequestReceived

Publish:

Ingress Runtime

↓

EventBus.publish(UserRequestReceived)
Bước 2 — Context Runtime

Context Runtime subscribe:

UserRequestReceived

Nhận được Event:

Load Session

Load Memory

Load User

Load RAG

Sau khi hoàn thành:

Publish:

ContextReady
Bước 3 — Workflow Runtime

Workflow Runtime subscribe

ContextReady

Nhận Event

Phân tích:

dùng workflow nào
có cần Agent không

Sau đó

Publish

WorkflowCreated
Bước 4 — Agent Runtime

Subscribe

WorkflowCreated

Agent Runtime khởi tạo Agent

Publish

AgentStarted
Bước 5 — Agent suy nghĩ

Agent quyết định

Cần

gmail.list_messages

Không gọi Tool trực tiếp.

Publish

ToolExecutionRequested
Bước 6 — Tool Runtime

Subscribe

ToolExecutionRequested

Nhận Event

Tra Registry

Nếu Local Tool

↓

Execute

Nếu MCP

↓

Call MCP

Nếu Native

↓

Call Provider

Kết thúc

Publish

ToolExecutionCompleted
Bước 7 — Agent

Subscribe

ToolExecutionCompleted

Tiếp tục reasoning.

Có thể lại publish

ToolExecutionRequested

Lần thứ hai.

Hoặc

AgentCompleted
Bước 8 — Workflow Runtime

Subscribe

AgentCompleted

Ghép kết quả.

Publish

WorkflowCompleted
Bước 9 — Response Runtime

Subscribe

WorkflowCompleted

Trả HTTP Response.

Toàn bộ Event Flow
HTTP Request

↓

UserRequestReceived

↓

ContextReady

↓

WorkflowCreated

↓

AgentStarted

↓

ToolExecutionRequested

↓

ToolExecutionCompleted

↓

AgentCompleted

↓

WorkflowCompleted

↓

HTTP Response

Không Runtime nào gọi Runtime khác.

3. Event là gì?

Một Event nên bất biến (immutable).

Ví dụ

{
  "id": "evt_123",

  "type": "tool.execution.requested",

  "timestamp": "...",

  "session_id": "...",

  "workflow_id": "...",

  "agent_id": "...",

  "correlation_id": "...",

  "causation_id": "...",

  "source": "AgentRuntime",

  "payload": {

  }

}

Sau khi Publish

Không sửa Event.

4. Correlation ID

Ví dụ

User gửi

Summarize Gmail

Correlation ID

ABC

Mọi Event

UserRequestReceived

Correlation=ABC

↓

ContextReady

Correlation=ABC

↓

WorkflowCreated

Correlation=ABC

↓

ToolExecutionRequested

Correlation=ABC

Toàn bộ request dùng cùng Correlation.

Dễ tracing.

5. Causation ID

Correlation:

Toàn bộ request.

Causation:

Event sinh ra từ Event nào.

Ví dụ

Event 1

ID=1

↓

Event 2

Causation=1

↓

Event 3

Causation=2

Có thể dựng cây Event.

6. Event Dispatcher

EventBus không tự chạy Handler.

Có Dispatcher.

Publish

↓

Queue

↓

Dispatcher

↓

Subscriber

Dispatcher:

tìm Subscriber
retry
timeout
dead letter
metrics
7. Subscriber

Ví dụ

Context Runtime

subscribe

UserRequestReceived

Agent Runtime

subscribe

WorkflowCreated

Tool Runtime

subscribe

ToolExecutionRequested
8. Event Queue

Không phải Queue của Kafka.

Là Queue trong Gateway.

Ví dụ

Concurrent Queue

↓

Dispatcher

↓

Handler

Để tránh recursion.

9. Sync và Async

Một số Event

Sync

ToolExecutionRequested

Agent phải chờ.

Một số

Async

Telemetry

Audit

Analytics

Cache Warmup

Không cần chờ.

10. Event Priority

Ví dụ

HIGH

ToolExecutionRequested

LOW

MetricsCollected

Dispatcher ưu tiên.

11. Retry

Nếu

Tool Runtime

bị lỗi

Dispatcher

Retry

1

2

3

Nếu vẫn lỗi

Publish

ToolExecutionFailed
12. Dead Letter Queue

Nếu Event lỗi nhiều lần

↓

DLQ

Có thể debug sau.

13. Session Lock

Hai request

Session A

đến cùng lúc.

Dispatcher

Lock Session A

Đảm bảo Context không bị ghi chồng.

14. Idempotency

Nếu Event bị gửi lại

ToolExecutionRequested

Runtime kiểm tra

event_id

Nếu đã xử lý

↓

Bỏ qua.

Không chạy Tool lần hai.

15. Plugin

Plugin chỉ cần

class GmailPlugin:

    @Subscribe("tool.execution.completed")
    async def on_tool(...):
        ...

Không cần sửa Gateway.

16. MCP

MCP Runtime

@Subscribe("tool.execution.requested")

Nếu

provider=mcp

↓

Call MCP

↓

Publish

ToolExecutionCompleted
17. Monitoring

Monitoring Runtime

Subscribe

*

Thu toàn bộ Event

↓

Grafana

↓

OpenTelemetry

↓

Audit

Không ảnh hưởng Runtime khác.

Kiến trúc tổng thể
                        HTTP / WebSocket / gRPC
                                  │
                                  ▼
                           Ingress Runtime
                                  │
                    publish(UserRequestReceived)
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │    Internal Event Bus   │
                     │                         │
                     │  Queue + Dispatcher     │
                     │  Routing + Retry        │
                     │  Session Lock           │
                     │  Idempotency            │
                     └──────────┬──────────────┘
                                │
     ┌──────────────┬────────────┼─────────────┬──────────────┐
     ▼              ▼            ▼             ▼              ▼
Context Runtime  Workflow    Agent Runtime  Tool Runtime  Response Runtime
                  Runtime                         │
                                                  ▼
                                          Provider Runtime
                                      ┌────────┼─────────┐
                                      ▼        ▼         ▼
                                   Local     MCP     Native LLM
Triết lý thiết kế

Mình khuyến nghị xem Event Bus như "hệ thần kinh trung ương" của Gateway:

Runtime là các cơ quan chức năng (Context, Agent, Tool, Workflow...).
Event là các tín hiệu thần kinh.
Event Bus là hệ thần kinh truyền tín hiệu.
Dispatcher là bộ điều phối.
Provider Runtime là lớp kết nối ra thế giới bên ngoài (Local, MCP, Native LLM, REST...).

Nhờ đó, các Runtime không phụ thuộc trực tiếp vào nhau, chỉ phụ thuộc vào hợp đồng sự kiện (Event Contract). Điều này giúp hệ thống dễ mở rộng, dễ kiểm thử và có thể thêm Runtime mới (ví dụ Memory Runtime, MCP Runtime, Plugin Runtime, Monitoring Runtime...) mà không cần sửa các Runtime hiện có.