# Phase 2 — Event Bus Stabilization / Event Dispatch DI

## 1. Mục tiêu

Phase 2 có nhiệm vụ làm Event Bus đủ ổn định để Session, Context, Workflow và Provider Runtime chuyển sang event-driven execution mà không tồn tại nhiều registry/bus song song.

Roadmap đặt các yêu cầu chính:

```text
single EventRegistry
single EventBus
single EventDispatcher
dependency injection vào handler
event naming có semantics rõ ràng
PriorityQueue không compare event object
```

---

## 2. Trạng thái tổng quan

| Hạng mục | Trạng thái |
|---|---|
| Shared `EventRegistry` | Hoàn thành |
| `EventBus` dùng shared registry | Hoàn thành |
| `EventDispatcher` | Hoàn thành |
| Subscriber registration vào shared registry | Hoàn thành |
| PriorityQueue sequence number | Hoàn thành |
| Handler dependency injection | Hoàn thành một phần |
| UoW/repository dependency resolution | Có |
| Retry cho event handler | Có |
| DLQ event | Có |
| Event idempotency | Có |
| WebSocket broadcast subscriber | Có |
| Event naming mới | Có một phần |
| Domain `commands.py` / `events.py` theo roadmap | Chưa có đúng cấu trúc |
| Event semantics chuẩn hóa toàn hệ thống | Chưa hoàn tất |
| Event lifecycle end-to-end | Đã hoạt động một phần |
| Event-driven Provider/Session/Context/Workflow | Đã triển khai |
| Production-grade delivery semantics | Chưa hoàn tất |

### Kết luận

**Phase 2 về infrastructure đã triển khai khá sâu và là một trong những phần hoàn thiện hơn Phase 0–3. Tuy nhiên phase vẫn chưa hoàn thành hoàn toàn ở lớp event contract/semantic chuẩn hóa.**

---

## 3. EventingManager là composition root của Event Bus

File:

```text
src/infrastructure/event_bus/manager.py
```

hiện sở hữu:

```text
registry
bus
dispatcher
ws_manager
uow_factory
dependency container reference
```

Chuỗi khởi tạo:

```text
EventRegistry
     ↓
EventBus
     ↓
EventDispatcher
     ↓
WebSocketConnectionManager
```

đúng với nguyên tắc single infrastructure owner.

---

## 4. Shared EventRegistry

File:

```text
src/infrastructure/event_bus/registry.py
```

có:

```text
_handlers_by_name
_all_event_handlers
```

API chính:

```text
register()
unregister()
subscribe()
unsubscribe()
register_for_all()
get_handlers()
```

Không thấy một `EventRegistry()` thứ hai trong production source ngoài:

```text
EventingManager
```

Điều này là bằng chứng rằng lỗi “registry bị tách đôi” trong kiến trúc cũ đã được sửa phần cốt lõi.

---

## 5. Subscriber registration

File:

```text
src/infrastructure/event_bus/subscribers.py
```

không tự tạo registry.

Nó expose:

```python
register_subscribers(registry)
```

và `EventingManager` gọi:

```python
eventing_manager.register_subscribers()
```

sau khi tạo registry.

Đây là đúng hướng mà Phase 2 yêu cầu.

---

## 6. Dependency Injection cho event handler

`EventDispatcher` phân tích signature handler:

```text
inspect.signature(handler)
```

sau đó resolve dependency từ dependency container.

Có hỗ trợ:

```text
static dependency
repository type
UoW
```

Ví dụ subscriber có thể yêu cầu:

```python
async def handle_user_created(
    event,
    session_repo: SessionRepository
)
```

và dispatcher tìm repository phù hợp từ UoW.

Đây là một bước quan trọng vì event handler không phải tự lấy storage/application singleton.

---

## 7. Retry và DLQ

`EventDispatcher` có:

```text
max_retries = 3
```

và khi handler thất bại đủ số lần sẽ publish:

```text
system.event.failed
```

payload chứa:

```text
failed_event
failed_handler
error_message
stack_trace
```

Subscriber:

```text
handle_failed_event_dlq()
```

ghi lại thông tin lỗi.

Đây là một implementation thực tế chứ không chỉ interface.

---

## 8. Idempotency

EventDispatcher kiểm tra:

```text
processed_event:<event_id>
```

thông qua cache driver.

Trong `main.py`, EventingManager nhận:

```text
storage.drivers.get("redis")
```

và truyền Redis driver vào dispatcher.

Đây là điểm đã được cải thiện so với thiết kế cũ dùng tên driver không thống nhất.

Tuy nhiên cơ chế hiện vẫn cần được harden cho:

```text
atomic set-if-not-exists
distributed race
event replay
exactly-once expectations
```

Nó mới gần với:

```text
best-effort idempotency
```

hơn là durable event processing semantics.

---

## 9. PriorityQueue stabilization

`EventBus` sử dụng:

```text
(priority, sequence, event, future)
```

thay vì:

```text
(priority, event, future)
```

`sequence` tăng tuần tự:

```python
self._sequence += 1
```

Điều này tránh trường hợp hai event có cùng priority khiến Python phải so sánh hai `BaseEvent` object.

Test tương ứng nằm tại:

```text
tests/architecture/test_phase1.py
```

và kiểm tra:

```text
same priority
sequence ordering
event identity
```

---

## 10. Event-driven execution đã hình thành

Luồng Chat mới hiện tại:

```text
HTTP Chat Router
      |
      | transport.event.request_received
      v
 SessionRuntime
      |
      | session.event.loaded
      v
 WorkflowRuntime
      |
      | context.command.build
      v
 ContextRuntime
      |
      | context.event.built
      v
 WorkflowRuntime
      |
      | provider.chat.execute
      v
 ProviderRuntime
      |
      | provider.chat.responded
      v
 SessionRuntime / HTTP bridge
```

Đây là bằng chứng quan trọng rằng Phase 2 không còn chỉ là infrastructure stub.

---

## 11. Nhưng event semantic chưa hoàn thiện

Roadmap yêu cầu các domain command/event chuẩn hóa riêng:

```text
src/domain/events/commands.py
src/domain/events/events.py
```

archive hiện không có package này.

Thay vào đó, event name đang nằm trực tiếp trong runtime/transport:

```text
transport.event.request_received
session.event.loaded
context.command.build
context.event.built
provider.chat.execute
provider.chat.responded
provider.failed
provider.stream.chunk_emitted
provider.stream.completed
```

Điều này hoạt động được, nhưng event contract vẫn mang tính:

```text
string-based
module-local knowledge
```

thay vì central typed contract.

---

## 12. Một khoảng trống lớn: Workflow chưa phải state machine

`WorkflowRuntime` hiện chủ yếu làm routing:

```text
session loaded
→ context build

context built
→ provider execute

capability executed
→ context rebuild
```

Nó chưa có:

```text
workflow state
transition validation
execution id
correlation id
causation id
retry policy per step
join/barrier
cancellation state
```

Do đó Phase 2 đã tạo event pipeline nhưng chưa tạo workflow engine hoàn chỉnh.

---

## 13. Session/Context integration

`SessionRuntime` đã:

- load session từ UoW;
- tạo session nếu chưa tồn tại;
- validate ownership;
- persist messages;
- publish `session.event.loaded`;
- persist assistant response.

`ContextRuntime` đã:

- dùng `ContextEngine`;
- load persisted context;
- thay request history bằng persisted messages;
- publish `context.event.built`.

Test có trong:

```text
tests/architecture/test_phase2.py
```

Các test này là evidence tốt cho phần Session/Context integration.

---

## 14. Vấn đề còn tồn tại

### 14.1. Event contracts chưa centralize

Event string được rải trong code.

### 14.2. EventBus vẫn là low-level infrastructure

Application/runtime layers vẫn phải biết event name cụ thể.

### 14.3. Subscriber lifetime

Transport có thể subscribe handler local vào event bus cho từng request. Đây là pattern đang dùng cho SSE/Future bridge.

Cần kiểm soát:

```text
handler leak
duplicate registration
request cancellation
subscriber cleanup
```

hiệu quả trong production.

### 14.4. Delivery semantics

Chưa có đầy đủ:

```text
durable queue
replay
consumer offset
dead-letter persistence
partitioning
ordering scope
```

---

## 15. Test evidence

File:

```text
tests/architecture/test_phase2.py
```

kiểm tra:

- ContextEngine tạo snapshot.
- SessionRuntime tạo session.
- SessionRuntime lưu message.
- Session owner isolation.
- ContextRuntime dùng persisted snapshot thay request history.

Tất cả đều là test logic có ý nghĩa kiến trúc.

Tuy nhiên full pytest trong archive environment không collection được do:

```text
ModuleNotFoundError: structlog
```

Vì vậy status test nên ghi:

```text
Tests present and statically verified,
runtime execution requires project dependencies.
```

---

## 16. Definition of Done thực tế

Phase 2 có thể coi hoàn chỉnh khi:

```text
single EventRegistry
single EventBus
single EventDispatcher
typed domain commands/events
dependency injection
durable idempotency
defined delivery semantics
correlation/causation
workflow state machine
```

Trong archive hiện đạt mạnh phần:

```text
single bus
dispatch
DI
retry
DLQ
idempotency
runtime integration
```

nhưng còn thiếu:

```text
typed event contracts
durable semantics
workflow state machine
```

---

## 17. Kết luận

**Current status: `LARGELY IMPLEMENTED / CONTRACTS NOT FULLY STANDARDIZED`**

Phase 2 đã tạo thành một event-driven backbone thật sự và đang được Phase 3–4 sử dụng.

Khoảng cách chính không còn là “Event Bus có tồn tại hay không”, mà là:

```text
Event Bus infrastructure
        ↓
Domain Event Contract
        ↓
Execution semantics
        ↓
Durable delivery
```

Ba lớp sau chưa hoàn thiện.
