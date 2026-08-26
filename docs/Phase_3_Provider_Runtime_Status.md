# Phase 3 — Provider Runtime Cutover

## 1. Mục tiêu

Phase 3 là phase lớn nhất trong roadmap Phase 0–3.

Mục tiêu:

```text
ProviderRuntime
        ↓
Provider execution authority
```

và:

```text
ModelRouter
        ↓
compatibility bridge
```

không còn là execution authority chính.

Phase này đồng thời phải chuyển dần:

```text
chat
models
embeddings
files
admin/provider operations
```

sang runtime/application abstraction mới.

---

## 2. Trạng thái tổng quan

| Hạng mục | Trạng thái |
|---|---|
| `ProviderRuntime` | Hoàn thành một phần lớn |
| Provider discovery | Hoàn thành |
| Provider registry | Hoàn thành |
| Provider factory | Hoàn thành |
| Routing policy | Hoàn thành |
| Retry policy | Hoàn thành |
| Circuit breaker | Có và được tích hợp |
| Provider executor | Hoàn thành |
| Chat handler | Hoàn thành tương đối |
| Streaming chat handler | Hoàn thành |
| Model handler | Hoàn thành |
| File handler | Hoàn thành tương đối |
| Embedding handler | **Chưa hoàn thành — `pass`** |
| Event-driven provider execution | Hoàn thành cho các nhánh đã nối |
| Legacy `ModelRouter` compatibility | Có |
| `LegacyModelRouterFacade` | Có |
| Chat HTTP path dùng event-driven runtime | Có |
| Chat Application Service | Chưa có |
| Model Catalog Service | Chưa có |
| Embedding Application Service | Chưa có |
| File Application Service | Chưa có |
| Shadow Mode parity | Chưa thấy triển khai |
| Percentage rollout | Chưa thấy triển khai |
| Typed Inference contract đầy đủ | Chưa hoàn thiện |
| Full provider cutover | Chưa hoàn thành |

### Kết luận

**Phase 3 đang ở trạng thái `PARTIAL / ACTIVE CUTOVER`.**

Phần Provider Runtime core đã có thật và đang chạy trong bootstrap. Chat path mới đã được nối vào Event Bus + ProviderRuntime. Tuy nhiên phase chưa thể xem là hoàn tất vì:

1. Embedding handler còn placeholder.
2. Application layer theo roadmap chưa được tạo.
3. Legacy path vẫn còn.
4. Shadow comparison/percentage rollout chưa có evidence.
5. Một số provider operations vẫn phụ thuộc trực tiếp vào provider object hoặc compatibility adapter.

---

## 3. ProviderRuntime hiện tại

File:

```text
src/runtimes/provider/runtime.py
```

ProviderRuntime quản lý:

```text
ProviderRegistry
ProviderDiscovery
RoutingPolicy
ProviderExecutor
ChatExecutionHandler
EmbeddingExecutionHandler
ModelOperationHandler
FileOperationHandler
```

Runtime được bootstrap trong:

```text
src/main.py
```

bằng:

```python
kernel.register_runtime(ProviderRuntime(cb_manager))
```

Sau kernel bootstrap:

```text
container.provider_runtime
```

được bind.

---

## 4. Provider discovery / registry / factory

Các file:

```text
src/provider/factory.py
src/provider/discovery.py
src/provider/registry.py
```

đã phân chia trách nhiệm khá rõ.

### ProviderFactory

Biết các implementation:

```text
openai
ollama
gemini
```

### ProviderDiscovery

Thực hiện:

```text
discover configured providers
→ create provider
→ register
```

### ProviderRegistry

Lưu:

```text
provider_name → ProviderEntry → BaseProvider
```

Đây phù hợp với target:

```text
registry ≠ orchestration
```

---

## 5. RoutingPolicy

File:

```text
src/provider/policies/routing_policy.py
```

đã có:

- default provider priority;
- routing rules YAML;
- model pattern matching;
- fallback chain;
- hot reload với `asyncio.Lock`.

Ví dụ flow:

```text
model
  ↓
specific routing rule?
  ├─ yes → rule provider chain
  └─ no  → default provider chain
```

Provider được kiểm tra availability/config trước khi đưa vào chain.

Đây là một thành phần đáng giữ lại trong kiến trúc mới.

---

## 6. ProviderExecutor

File:

```text
src/provider/executor.py
```

chịu trách nhiệm execution-level resilience.

Có:

```text
RetryPolicy
CircuitBreaker
Provider invocation
Streaming invocation
Generic invocation
```

### Retry

`RetryPolicy` phân biệt:

```text
timeout
connect error
429
5xx
provider unavailable
```

với:

```text
authentication error
response validation error
4xx non-retryable
```

và áp dụng:

```text
exponential backoff + jitter
```

### Circuit Breaker

Provider call đi qua:

```text
before_request
execution
on_success / on_failure
```

đây là design phù hợp với provider runtime.

---

## 7. ChatExecutionHandler

File:

```text
src/provider/handlers/chat_handler.py
```

hiện hỗ trợ:

```text
sync chat
streaming chat
provider preference
fallback chain
health filtering
capability check
ProviderExecutor
OpenTelemetry span
```

Non-stream flow:

```text
request
 ↓
routing policy
 ↓
preferred provider (optional)
 ↓
healthy chain
 ↓
capability check
 ↓
executor
 ↓
response
```

Streaming flow tương tự nhưng dùng:

```text
AsyncGenerator
```

và fallback giữa provider nếu stream fail.

Đây là phần Phase 3 đã tương đối mature.

---

## 8. ProviderRuntime + Event Bus

ProviderRuntime subscribe:

```text
provider.chat.execute
provider.embeddings.execute
provider.model.execute
provider.file.execute
```

và publish:

```text
provider.chat.responded
provider.embeddings.responded
provider.model.responded
provider.file.responded
provider.stream.chunk_emitted
provider.stream.completed
provider.failed
```

Như vậy provider execution đã được chuyển thành runtime event boundary.

---

## 9. Chat cutover thực tế

`main.py` đang register:

```text
src.transport.gateway.http.chat_router
```

không phải legacy `router/chat.py`.

Chat HTTP path hiện:

```text
POST /v1/chat/completions
        ↓
GatewayChatRequest
        ↓
transport.event.request_received
        ↓
SessionRuntime
        ↓
session.event.loaded
        ↓
WorkflowRuntime
        ↓
context.command.build
        ↓
ContextRuntime
        ↓
context.event.built
        ↓
WorkflowRuntime
        ↓
provider.chat.execute
        ↓
ProviderRuntime
        ↓
provider.chat.responded
```

Đây là bằng chứng rằng **ProviderRuntime không chỉ tồn tại mà đã tham gia execution path thật**.

---

## 10. LegacyModelRouter vẫn tồn tại

File:

```text
src/provider/__init__.py
```

vẫn chứa:

```text
ModelRouter
```

được đánh dấu deprecated.

Đồng thời có:

```text
LegacyModelRouterFacade
```

với mục đích chuyển API cũ sang ProviderRuntime.

`main.py` vẫn gắn:

```text
app.state.router = LegacyModelRouterFacade(provider_runtime)
```

Đây là một compatibility bridge hợp lý cho migration an toàn.

Tuy nhiên điều này chứng minh:

```text
ProviderRuntime cutover != complete removal of old architecture
```

---

## 11. Embeddings là gap lớn nhất hiện tại

File:

```text
src/provider/handlers/embedding_handler.py
```

hiện có:

```python
async def execute(...):
    pass
```

Do đó nhánh:

```text
ProviderRuntime._handle_execute_embeddings()
```

có thể gọi handler nhưng không có implementation thật.

Trong khi đó legacy `ModelRouter` vẫn chứa implementation embeddings cũ.

Điều này tạo một trạng thái:

```text
Chat        → new runtime path
Models      → new handler path
Files       → new handler path
Embeddings  → facade → placeholder handler
```

Đây là lý do Phase 3 chưa thể đánh dấu complete.

---

## 12. Application Services chưa được tạo

Roadmap yêu cầu:

```text
src/application/chat/service.py
src/application/providers/model_catalog.py
src/application/inference/embedding_service.py
src/application/providers/file_service.py
```

Các file này **không có trong archive**.

Điều này cho thấy kiến trúc hiện tại đã làm một bước trung gian:

```text
Transport
   ↓
Event Bus
   ↓
Runtime
   ↓
Provider Handler
```

nhưng chưa đạt target:

```text
Transport
   ↓
Application Service
   ↓
Runtime
   ↓
Provider
```

Đây là gap kiến trúc quan trọng.

---

## 13. Models

Đang có:

```text
src/provider/handlers/model_handler.py
```

và HTTP models router gửi:

```text
provider.model.execute
```

ProviderRuntime trả:

```text
provider.model.responded
```

Đây là execution path mới có thật.

Nhưng thiếu:

```text
ModelCatalogService
```

theo roadmap.

---

## 14. Files

Đang có:

```text
src/provider/handlers/file_handler.py
```

và event path:

```text
provider.file.execute
```

File operations hiện hỗ trợ:

```text
list
upload
metadata
download
delete
```

Nhưng application service abstraction vẫn chưa tồn tại.

Ngoài ra legacy router `src/transport/gateway/router/files.py` vẫn truy cập:

```text
request.app.state.router.providers
```

trực tiếp.

---

## 15. Shadow Mode

Roadmap đề xuất:

```text
legacy execution → real response
             ↘
              ProviderRuntime shadow
```

để so sánh:

```text
provider chosen
model
status
latency
token estimate
error category
```

Archive hiện không có evidence về:

```text
shadow runner
parity comparator
shadow metrics
percentage rollout
```

Do đó:

**Shadow Mode chưa được triển khai hoặc chưa được đưa vào source archive.**

---

## 16. Inference contract

Archive có:

```text
src/domain/schemas/request.py
src/domain/schemas/response.py
src/domain/schemas/execution.py
```

nhưng không có:

```text
src/domain/schemas/inference.py
```

theo tên roadmap.

Điều này cho thấy domain contract đang phát triển nhưng chưa đạt thiết kế inference envelope đầy đủ.

---

## 17. Provider Runtime initialization có một vấn đề type/design

`BaseRuntime.initialize()` nhận:

```text
RuntimeContext
```

nhưng ProviderRuntime khai báo:

```python
async def initialize(self, context: Dict[str, Any])
```

và runtime lại sử dụng:

```text
context.config
context.event_bus
```

Thực tế vẫn hoạt động với object RuntimeContext nhưng type contract không thống nhất.

Nên chuẩn hóa về:

```python
async def initialize(self, context: RuntimeContext)
```

cho toàn bộ runtime.

---

## 18. Provider error normalization

ProviderRuntime đã normalize lỗi thành:

```text
provider.failed
status_code
error
```

đây là bước tốt.

Nhưng application layer chưa có policy trung tâm để mapping:

```text
ProviderError
NoAvailableProviderError
AuthenticationError
RateLimit
ValidationError
Timeout
```

thành public API contract thống nhất.

---

## 19. Test evidence

Có:

```text
tests/architecture/test_phase3.py
```

kiểm tra:

- Authorization requires all scopes.
- CapabilityRuntime filters unauthorized capability.
- Capability execution.
- ToolRegistry delegation.

Điều này chứng minh phần Capability/Tool của Phase 3 đã được bắt đầu.

Tuy nhiên test chưa phải là một ProviderRuntime end-to-end suite.

Hiện chưa có test riêng cho:

```text
ProviderRuntime chat event path
ProviderRuntime streaming event path
Model handler
File handler
Embedding handler success
Provider fallback at runtime boundary
```

---

## 20. Tình trạng từng feature

### Chat

```text
Status: NEARLY COMPLETE
```

Có execution path mới, streaming, fallback, circuit breaker.

Thiếu chủ yếu:

```text
ChatApplicationService
full contract/e2e parity
```

### Models

```text
Status: IMPLEMENTED AT RUNTIME LEVEL
```

Handler + event path đã có.

Thiếu:

```text
ModelCatalogService
```

### Files

```text
Status: IMPLEMENTED AT RUNTIME LEVEL
```

Handler có logic list/upload/get/download/delete.

Thiếu:

```text
FileApplicationService
```

### Embeddings

```text
Status: NOT COMPLETE
```

EmbeddingExecutionHandler hiện là placeholder.

---

## 21. Definition of Done thực tế

Phase 3 chỉ nên đánh dấu complete khi đạt:

```text
ProviderRuntime = single execution authority
LegacyModelRouter = compatibility only
ChatApplicationService
ModelCatalogService
EmbeddingApplicationService
FileApplicationService
InferenceRequest/Response contract
Shadow parity
feature flag / percentage rollout
provider runtime tests
legacy compatibility tests
```

Đặc biệt:

```text
EmbeddingExecutionHandler
```

phải được triển khai.

---

## 22. Thứ tự hoàn thiện nên làm

### Bước 1

Hoàn thiện:

```text
EmbeddingExecutionHandler
```

### Bước 2

Tạo:

```text
ChatApplicationService
EmbeddingApplicationService
ModelCatalogService
FileApplicationService
```

### Bước 3

Chuyển HTTP routers sang application services.

### Bước 4

Tạo parity test:

```text
legacy vs ProviderRuntime
```

### Bước 5

Thêm shadow metrics.

### Bước 6

Thêm feature flags:

```text
provider_runtime_enabled
new_chat_path_percentage
```

### Bước 7

Giảm dần `LegacyModelRouter`.

### Bước 8

Chỉ xóa legacy sau khi compatibility tests pass liên tục.

---

## 23. Kết luận

**Current status: `PARTIAL / ACTIVE CUTOVER`**

Phase 3 đã vượt xa mức skeleton:

```text
ProviderRegistry
ProviderDiscovery
RoutingPolicy
RetryPolicy
CircuitBreaker
ProviderExecutor
ProviderRuntime
ChatHandler
ModelHandler
FileHandler
```

đều đã tồn tại và một phần đang chạy trên request path thật.

Nhưng Phase 3 chưa hoàn thành vì:

```text
Embedding = placeholder
Application services = missing
Shadow mode = missing
Percentage rollout = missing
Legacy path = still active
Inference contract = incomplete
```

Vì vậy cách mô tả chính xác nhất là:

> **Provider Runtime đã được triển khai và đang được cutover, nhưng provider architecture chưa hoàn toàn chuyển sang target architecture.**
