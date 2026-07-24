# Mục tiêu:

- Hỗ trợ Chat, Voice, Computer Use, Automation.

- Hỗ trợ Session kéo dài nhiều giờ/ngày.

- Hỗ trợ scale nhiều Gateway instance.

- Hỗ trợ replay/debug.

- Hỗ trợ workflow dài hạn.
## 1. Vị trí Ingress Runtime
- REST
- WebSocket
- gRPC
- MCP
- Realtime Voice
Automation Scheduler

          │
          ▼

    Ingress Runtime

          │
          ▼

    Session Runtime

          │
          ▼

    Internal Event Bus

          │
          ▼

       Runtimes

- Ingress là cổng vào duy nhất.
- Không Runtime nào được nhận request trực tiếp từ endpoint.
## 2. Nhiệm vụ Ingress Runtime
Ingress Runtime

├── Authentication
├── Authorization
├── Request Validation
├── Rate Limiting
├── Idempotency
├── Session Routing
├── Event Creation
├── Correlation Tracking
├── Audit Logging
└── Event Publishing

## 3. Event Schema
- Đây là schema lõi của toàn hệ thống.


class RuntimeEvent:
    event_id: str

    event_type: str

    timestamp: datetime

    session_id: str

    project_id: str | None

    workspace_id: str | None

    user_id: str

    correlation_id: str

    causation_id: str | None

    source: str

    priority: int

    payload: dict

    metadata: dict

## 4. Ý nghĩa các trường
- `event_id`
- Mỗi event duy nhất.

- `evt_123456`

- `correlation_id`
- 
- Theo dõi toàn bộ một request.
- Ví dụ:

TextMessageReceived

↓

PlanCreated

↓

CapabilityResolved

↓

WorkflowStarted

↓

WorkflowCompleted

Tất cả dùng chung:

- `corr_001`
  
- `causation_id`
  
- Event nào sinh ra event này.
  
Ví dụ:

TextMessageReceived


↓

PlanCreated


PlanCreated:

causation_id = event_id(TextMessageReceived)


### 1. Event Type
- `Session Events`

- `Chat Events`

- `Voice Events`

- `Workflow Events`

- `Agent Events`

- `Tool Events`

- `Plugin Events`

- `Provider Events`

- `Memory Events`

- `System Events`

Ví dụ

TextMessageReceived

AudioChunkReceived

WorkflowStarted

WorkflowPaused

ToolRequested

ToolCompleted

PluginFailed

LLMDeltaReceived

MemoryUpdated

6. Internal Event Bus
Không dùng Kafka.
Không dùng RabbitMQ.
Đây là Bus nội bộ.


class EventBus:

    async def publish(
        self,
        event: RuntimeEvent
    )

    async def subscribe(
        self,
        event_type: str,
        handler
    )

    async def unsubscribe(...)

7. Event Dispatcher
Dispatcher nhận event.

Event

↓

Dispatcher

↓

Subscribers

Ví dụ

TextMessageReceived


↓

Context Runtime

Agent Runtime

Audit Runtime

8. Event Queue
Mỗi Session có Queue riêng.

Session A

Queue A

Session B

Queue B

Session C

Queue C

Không dùng Queue toàn cục.
Lý do:

Voice Session


không được block bởi

Long Research Session


9. Session Kernel
Session Runtime sở hữu:


class SessionKernel:

    session_id

    queue

    state

    lock

    context_snapshot

    workflow_states

    agent_states

Mọi Runtime phải đi qua Session Kernel.
10. Session Locking
Nguyên tắc:

1 Session

1 Mutating Operation

Ví dụ:

Update Context


và

Update Workflow


không được ghi cùng lúc.
Dùng:


asyncio.Lock()


cho single instance.
Nếu multi-instance:

Redis Lock


hoặc

Postgres Advisory Lock


11. Concurrency Model
Mình đề xuất:

Session

↓

Actor Model

Session A

Actor A

Session B

Actor B

Mỗi Actor xử lý tuần tự.
Bên trong có thể spawn task song song.
12. Idempotency
Bắt buộc với:

WebSocket reconnect

Client retry

Network failure

Ingress nhận:


Idempotency-Key


Ví dụ

req_123


Lưu:


IdempotencyRecordkeyuserresultcreated_at

Nếu request lặp:

return cached result


13. Event Store
Nên lưu toàn bộ Event.


EventStoreevent_idsession_idevent_typepayloadtimestamp

Lợi ích:

Replay

Audit

Debug

Analytics

14. Error Handling
Không ném exception xuyên Runtime.
Sai:

Plugin

↓

raise Exception

Đúng:

PluginFailed


Event


{
  "type":"PluginFailed",
  "error":"OAuthExpired"
}

Workflow quyết định:

Retry

Fallback

Abort

15. Retry Policy
Ví dụ:


RetryPolicymax_attempts = 3backoff = exponential

Event:

ToolFailed


↓

RetryRequested


↓

ToolRequested


16. Dead Letter Queue
Nếu event lỗi liên tục.

ToolRequested

↓

Fail

↓

Fail

↓

Fail

↓

DLQ

Phục vụ:

Debug

Recovery

17. Multi Gateway Instance
Ví dụ:

Gateway-1

Gateway-2

Gateway-3

Không được để:

Session A

lúc ở Gateway-1

lúc ở Gateway-3

18. Session Affinity
Dùng:

Consistent Hashing



hash(session_id)


↓

Gateway Instance


Ví dụ:

Session A

→ Node 1

Session B

→ Node 3

19. Distributed Event Bus
Khi scale.

Internal Event Bus


chỉ trong node.
External Event Bus


giữa node.
Ví dụ:

NATS

Kafka

Redis Streams

20. Kiến trúc cuối cùng
                Transport Layer

REST
WS
MCP
gRPC
Voice

        │

        ▼

     Ingress Runtime

        │

        ▼

   Session Router

        │

        ▼

    Session Kernel
      (Actor)

        │

        ▼

 Internal Event Bus

        │

 ┌──────┼──────────┐
 ▼      ▼          ▼

Context Agent Workflow

 ▼      ▼          ▼

Capability Runtime

 ▼

Skill Runtime

 ▼

Tool Runtime

 ▼

Plugin Runtime

 ▼

Provider Runtime

        │

        ▼

 Event Store

Những thay đổi mình sẽ thực hiện
Sau khi đi hết kiến trúc này, mình sẽ bổ sung thêm Command Bus và tách khỏi Event Bus.
Hiện tại chúng ta đang coi mọi thứ là Event, nhưng thực tế có hai loại:

Command
SendEmail

ExecuteWorkflow

CallTool

Chỉ có một handler.
Event
EmailSent

WorkflowCompleted

ToolFinished

Có nhiều subscriber.
Do đó kiến trúc hoàn chỉnh sẽ là:

Ingress

↓

Command Bus

↓

Session Kernel

↓

Runtime

↓

Event Bus

↓

Subscribers

Mô hình này gần với CQRS + Actor Model hơn, phù hợp với hệ thống Agent phức tạp, workflow dài hạn và realtime.