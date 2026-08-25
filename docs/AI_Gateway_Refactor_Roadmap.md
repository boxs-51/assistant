# Kế hoạch refactor từng file/module sang kiến trúc AI Execution Platform

## 0. Mục tiêu của kế hoạch

Mục tiêu không phải "đập đi viết lại", mà là:

```text
Hệ thống hiện tại
      ↓
giữ nguyên traffic đang chạy
      ↓
dựng architecture mới song song
      ↓
chuyển từng execution path
      ↓
xác nhận parity
      ↓
mở rộng runtime mới
      ↓
deprecate code cũ
      ↓
xóa code cũ sau cùng
```

Kiến trúc đích:

```text
Client / SDK
    ↓
Transport
    ↓
Application Facade
    ↓
Execution Kernel
    ├── Auth / Identity
    ├── Session Runtime
    ├── Context Runtime
    ├── Agent Runtime
    ├── Capability Runtime
    ├── Workflow Runtime
    ├── Inference Runtime
    └── Connection Runtime
           ↓
     Provider Adapters
           ↓
 OpenAI / Google / Ollama / ...
```

Infrastructure:

```text
SQL
Redis
Vector DB
Object Storage
Event Bus
Observability
Secret Store
```

---

# 1. Nguyên tắc refactor bắt buộc

## 1.1. Không big-bang rewrite

Không được thực hiện:

```text
delete old router
→ rewrite new runtime
→ switch production
```

Phải thực hiện:

```text
old path
new path
  ↓
feature flag
  ↓
shadow / canary
  ↓
switch
```

---

## 1.2. Giữ backward compatibility ở API

Các endpoint hiện tại phải giữ:

```text
/v1/chat/completions
/embeddings
/files
/models
/auth/*
```

cho tới khi runtime mới đạt parity.

Client cũ không được buộc phải migrate chỉ vì refactor nội bộ.

---

## 1.3. Không thay đổi database destructive trong Phase 1

Không drop:

```text
users
sessions
messages
projects
attachments
```

Không rename column đang được production sử dụng nếu chưa có:

```text
expand
→ migrate
→ backfill
→ switch
→ contract
```

---

## 1.4. Một source of truth cho mỗi responsibility

Sau refactor:

```text
ProviderRuntime      → provider execution
SessionRuntime       → session lifecycle
ContextRuntime       → context building
CapabilityRuntime    → tool/capability execution
AgentRuntime         → agent loop
ExecutionRuntime     → execution state
EventBus             → event transport
ApplicationContainer → dependency wiring
```

Không có:

```text
ModelRouter
+ ProviderRuntime
```

cùng là execution authority.

---

## 1.5. Không phụ thuộc trực tiếp `app.state` trong domain/runtime

`app.state` chỉ nên là compatibility bridge ở transport.

Runtime mới lấy dependency từ:

```text
ApplicationContainer
RuntimeContext
Dependency injection
```

---

# 2. Trạng thái hiện tại và target

## 2.1. Current

```text
FastAPI
  ↓
routers
  ├── app.state.router
  ├── app.state.event_bus
  ├── app.state.event_manager
  ├── app.state.storage
  ├── app.state.tool_registry
  └── app.state.agent_registry

provider.ModelRouter
  ↓
ProviderExecutor
```

Song song:

```text
RuntimeKernel
  ↓
ProviderRuntime
ContextRuntime
SessionRuntime
WorkflowRuntime
CapabilityRuntime
ConnectionRuntime
EventRuntime
```

## 2.2. Target

```text
FastAPI
  ↓
Transport Adapter
  ↓
Application Facade
  ↓
Execution Kernel
  ↓
Execution Runtime
  ├── Session
  ├── Context
  ├── Agent
  ├── Capability
  ├── Workflow
  └── Inference
       ↓
ProviderRuntime
       ↓
Provider Adapter
```

---

# 3. Nguyên tắc phân biệt các khái niệm

Đây là phần phải cố định trước khi refactor.

## Session

Dữ liệu hội thoại lâu dài:

```text
session_id
tenant_id
user_id
messages
summary
metadata
```

## ContextSnapshot

Snapshot của context phục vụ một inference:

```text
system instructions
recent messages
summary
retrieved memory
files
tool results
runtime metadata
```

## Execution

Một lần chạy:

```text
execution_id
session_id
agent_id
input
provider attempts
tool calls
output
usage
cost
status
```

## Agent

Định nghĩa một AI worker:

```text
goal
instructions
model policy
capabilities
memory policy
workflow
limits
```

## Capability

Một khả năng logic:

```text
github.search
github.issue.create
```

## Tool

Một operation có thể được LLM gọi.

## Extension

Gói tích hợp có thể cung cấp nhiều Capability/Tool:

```text
GitHub Extension
  ├── github.search
  ├── github.issue.create
  └── github.pull_request.list
```

## Provider

Một implementation của inference API:

```text
OpenAI
Google
Ollama
```

---

# 4. Chiến lược triển khai tổng thể

Có 11 phase.

```text
P0  Baseline / Safety
P1  Dependency Container
P2  Event Bus Stabilization
P3  Provider Runtime Cutover
P4  Session + Context
P5  Tool / Capability Unification
P6  Agent + Execution Runtime
P7  Extension / MCP Platform
P8  Streaming / Connection / Cancellation
P9  Persistence / Usage / Audit
P10 Distributed Runtime
P11 Legacy Removal
```

Không nên nhảy Phase.

---

# 5. PHASE 0 — BASELINE / SAFETY

## Mục tiêu

Trước khi sửa architecture, phải biết hệ thống hiện tại đang chạy thế nào.

### Thêm

```text
tests/
  architecture/
  contracts/
  integration/
  e2e/
```

## File mới

### `tests/architecture/test_imports.py`

Kiểm tra import graph không vòng.

### `tests/contracts/test_provider_contract.py`

Contract chung cho provider.

### `tests/e2e/test_legacy_chat.py`

Test:

```text
POST /v1/chat/completions
```

### `tests/e2e/test_legacy_embeddings.py`

### `tests/e2e/test_legacy_models.py`

### `tests/e2e/test_legacy_files.py`

### `tests/e2e/test_auth.py`

---

## Thêm configuration

### `infrastructure/config/...`

Thêm:

```yaml
refactor:
  provider_runtime_enabled: false
  execution_runtime_enabled: false
  context_runtime_enabled: false
  new_chat_path_percentage: 0
```

Không cần xóa config cũ.

---

## Definition of Done

Phải ghi nhận baseline:

```text
latency p50
latency p95
error rate
stream success rate
provider fallback success
model list success
embedding success
file operation success
auth success
```

Nếu chưa có baseline thì chưa bắt đầu cutover.

---

# 6. PHASE 1 — APPLICATION CONTAINER

Đây là phase quan trọng nhất để bỏ phụ thuộc trực tiếp `app.state`.

## 6.1. Tạo file mới

### `src/application/container.py`

Định nghĩa:

```python
class ApplicationContainer:
    config
    storage
    uow_factory
    http_client
    event_bus
    eventing_manager
    runtime_kernel
    provider_runtime
    session_runtime
    context_runtime
    capability_runtime
    workflow_runtime
    connection_runtime
    auth_manager
    oauth
    limiter
```

Mục tiêu:

```text
một object
→ một dependency graph
```

---

## 6.2. Tạo gateway dependency

### `src/transport/gateway/dependencies.py`

Các function:

```python
get_container(request)
get_runtime_kernel(request)
get_provider_runtime(request)
get_session_runtime(request)
get_context_runtime(request)
get_capability_runtime(request)
```

---

# 7. PHASE 1 — REFUSE DIRECT APP.STATE IN RUNTIME

Các file phải đổi.

## `src/main.py`

### Hiện tại

Bootstrap nhiều object trực tiếp và đưa vào:

```text
app.state.storage
app.state.event_bus
...
```

### Thay đổi

Giữ `app.state.container` là object chính.

Trong thời gian chuyển tiếp vẫn mirror:

```python
app.state.storage = container.storage
app.state.event_bus = container.event_bus
```

nhưng đánh dấu:

```text
DEPRECATED
```

### Quan trọng

Không xóa compatibility state trong phase này.

---

## `src/kernel/base.py`

Đổi `RuntimeContext` thành dependency boundary thực sự:

```text
config
event_bus
storage
uow_factory
http_client
metrics
tracer
clock
container
```

Không để:

```text
storage=None
metrics=None
clock=None
```

nếu runtime yêu cầu dependency đó.

---

## `src/kernel/lifecycle.py`

Sửa:

```text
initialize_all()
```

để truyền cùng một `RuntimeContext`.

Không dùng:

```python
global_config.get(r_id, {})
```

làm dependency injection chính.

Nên:

```python
RuntimeContext(
    container=container,
    config=container.config,
    storage=container.storage,
    uow_factory=container.uow_factory,
    event_bus=container.event_bus,
    http_client=container.http_client,
)
```

---

## `src/kernel/kernel.py`

Giữ `RuntimeKernel`.

Thêm:

```text
resolve_runtime(id)
resolve_service(type)
```

và tạo một facade:

```text
kernel.container
```

---

## `src/kernel/registry.py`

Giữ registry nhưng bổ sung:

```text
get_required()
register_service()
get_service()
```

Không để runtime tự tạo dependency.

---

# 8. PHASE 2 — EVENT BUS STABILIZATION

Không chuyển Provider/Context sang event bus mới trước khi event infrastructure ổn định.

## `src/infrastructure/event_bus/registry.py`

Giữ làm **single registry implementation**.

Xóa ý tưởng registry module-local.

---

## `src/infrastructure/event_bus/subscribers.py`

### Hiện tại

Có:

```python
registry = EventRegistry()
```

đây là sai.

### Thay đổi

Không tạo registry riêng.

Chuyển thành:

```python
def register_subscribers(registry, container):
    ...
```

Ví dụ:

```python
registry.register(
    "system.event.failed",
    handle_failed_event_dlq
)
```

---

## `src/infrastructure/event_bus/manager.py`

### Thay đổi

`EventingManager` sở hữu duy nhất:

```text
registry
bus
dispatcher
ws_manager
```

và import subscriber module phải thực sự register vào:

```text
self.registry
```

---

## `src/infrastructure/event_bus/bus.py`

Sửa priority queue.

Không dùng:

```python
(priority, event, future)
```

Dùng:

```python
(priority, sequence, event, future)
```

`sequence` tăng đơn điệu để tránh event object bị compare.

---

## Event naming chuẩn

Tạo:

### `src/domain/events/commands.py`

```text
session.load
context.build
inference.execute
capability.execute
workflow.execute
connection.send
```

### `src/domain/events/events.py`

```text
session.loaded
context.built
inference.started
inference.completed
capability.started
capability.completed
execution.completed
execution.failed
```

Không dùng lẫn:

```text
provider.chat.responded
provider.execution.succeeded
provider.stream.completed
```

khi đã qua execution layer.

---

## `src/runtimes/event/runtime.py`

Không cần chứa business logic.

Nó chỉ quản lý lifecycle event subsystem.

---

# 9. PHASE 2 — EVENT DISPATCH DEPENDENCY INJECTION

`EventDispatcher` phải có:

```text
container.resolve(...)
```

để handler có thể inject:

```text
SessionRepository
ContextEngine
WebSocketConnectionManager
StorageEngine
```

Không import/global singleton.

---

# 10. PHASE 3 — PROVIDER RUNTIME CUTOVER

Đây là phase refactor lớn nhất nhưng có thể làm không downtime.

## Mục tiêu

Chọn:

```text
ProviderRuntime
```

làm provider execution authority.

`ModelRouter` cũ chỉ tồn tại compatibility bridge trong một thời gian.

---

# 11. PHASE 3 — FILE MAPPING PROVIDER

## `src/provider/core/provider.py`

Giữ.

Đây là abstraction provider thấp nhất.

Nên đổi tên logic conceptual:

```text
BaseProvider = provider adapter
```

Không chứa orchestration.

---

## `src/provider/executor.py`

Giữ nhưng giới hạn trách nhiệm:

```text
provider adapter invocation
```

Không giữ:

```text
session
context
agent
workflow
```

---

## `src/provider/registry.py`

Giữ.

Đây trở thành registry vật lý:

```text
provider_name → BaseProvider
```

---

## `src/provider/discovery.py`

Giữ.

Bổ sung:

```text
discover()
validate()
register()
```

---

## `src/provider/factory.py`

Giữ.

Factory chỉ:

```text
config
→ provider adapter
```

không điều phối request.

---

## `src/provider/policies/routing_policy.py`

Giữ nhưng đổi vai trò thành:

```text
ProviderSelectionPolicy
```

Input:

```text
InferenceRequest
TenantPolicy
ModelPolicy
ProviderHealth
Capability
```

Output:

```text
ProviderCandidate[]
```

---

## `src/provider/policies/retry.py`

Giữ.

Đưa thành policy riêng:

```text
RetryPolicy
```

---

## `src/provider/policies/load_balancer.py`

Giữ.

Nhưng load balancing chỉ chọn trong danh sách candidate.

---

## `src/circuit_breaker.py`

Giữ.

ProviderRuntime sở hữu instance.

---

# 12. PHASE 3 — TÁCH `ModelRouter`

## `src/provider/__init__.py`

Không xóa ngay.

Đổi:

```python
class ModelRouter
```

thành compatibility facade:

```python
class LegacyModelRouterFacade:
    ...
```

Nó gọi:

```text
ProviderRuntime
```

thay vì trực tiếp thực hiện logic provider.

Ví dụ:

```text
old router.execute_with_fallback()
        ↓
provider_runtime.execute()
```

---

## Feature flag

```text
provider_runtime_enabled=false
```

khi false:

```text
legacy facade behavior
```

khi true:

```text
legacy facade → ProviderRuntime
```

API không đổi.

---

# 13. PHASE 3 — PROVIDER HANDLERS

## `src/provider/handlers/base.py`

Giữ abstraction.

## `chat_handler.py`

Đổi signature sang:

```text
InferenceRequest
```

thay vì raw:

```text
body: dict
```

## `embedding_handler.py`

Tương tự.

## `file_handler.py`

Giữ riêng vì file operation không nên bị ép vào chat execution.

## `model_handler.py`

Giữ cho Model Catalog compatibility.

---

# 14. PHASE 3 — DOMAIN INFERENCE CONTRACT

### `src/domain/schemas/request.py`

Không nên để request schema transport-specific trở thành runtime contract.

Thêm:

### `src/domain/schemas/inference.py`

```python
class InferenceRequest:
    request_id
    tenant_id
    user_id
    session_id
    execution_id
    model
    messages
    tools
    stream
    parameters
    policy
```

### `src/domain/schemas/inference.py`

Thêm:

```python
class InferenceResponse
class InferenceChunk
class ProviderAttempt
```

---

# 15. PHASE 3 — `src/domain/schemas/response.py`

Giữ:

```text
GatewayResponse
GatewayStreamChunk
```

nhưng biến thành compatibility DTO.

Runtime mới sử dụng:

```text
InferenceResponse
ExecutionEvent
```

Transport map ngược lại sang GatewayResponse.

---

# 16. PHASE 3 — ProviderRuntime

## `src/runtimes/provider/runtime.py`

Đây trở thành:

```text
Inference Runtime backend
```

Nó chịu trách nhiệm:

```text
provider discovery
registry
selection
retry
fallback
circuit breaker
provider execution
usage capture
```

Không chịu trách nhiệm:

```text
session persistence
context building
agent loop
tool authorization
```

---

# 17. PHASE 3 — Provider Runtime API

Expose:

```python
await provider_runtime.execute(
    request: InferenceRequest
) -> InferenceResponse

await provider_runtime.stream(
    request: InferenceRequest
) -> AsyncIterator[InferenceChunk]
```

---

# 18. PHASE 3 — Chat API migration

Hiện có hai chat router.

## `src/transport/gateway/router/chat.py`

Đây là **legacy router**.

Giữ.

Không thêm feature mới.

Mark:

```text
@deprecated internal path
```

---

## `src/transport/gateway/http/chat_router.py`

Đây là ứng viên migration chính.

Không để router publish raw:

```text
provider.chat.execute
```

thay vào đó:

```text
application.chat_service
```

---

# 19. Tạo Application Facade

### `src/application/chat/service.py`

```python
class ChatApplicationService:
    async def execute(...)
    async def stream(...)
```

Router chỉ làm:

```text
HTTP request
→ schema
→ identity
→ ChatApplicationService
→ response
```

---

# 20. PHASE 3 — ChatApplicationService

Execution hiện tại:

```text
ChatRequest
 ↓
Identity
 ↓
Session
 ↓
Context
 ↓
Inference
```

Nhưng phase này chỉ migration provider:

```text
ChatRequest
 ↓
InferenceRequest
 ↓
ProviderRuntime
```

Session/context chưa bắt buộc hoàn thiện.

Như vậy có thể cutover provider mà chưa phải đổi toàn bộ hệ thống.

---

# 21. PHASE 3 — Shadow Mode

Có thể thực hiện:

```text
request
 ↓
legacy ModelRouter ──→ real response
      │
      └──────────────→ ProviderRuntime shadow
```

So sánh:

```text
provider chosen
model
status
latency
token estimate
error category
```

Không dùng output của runtime mới.

Sau khi parity đạt:

```text
5% traffic
→ 25%
→ 50%
→ 100%
```

---

# 22. PHASE 3 — Models

## `src/transport/gateway/http/models_router.py`

Thay vì gọi provider event trực tiếp:

```text
ModelCatalogService
```

---

## Tạo

### `src/application/providers/model_catalog.py`

Responsibilities:

```text
list models
get model
capability lookup
health
```

---

## `src/transport/gateway/router/models.py`

Legacy adapter.

Không thêm behavior.

---

# 23. PHASE 3 — Embeddings

## `src/transport/gateway/http/embeddings_router.py`

Chuyển sang:

```text
EmbeddingApplicationService
```

### `src/application/inference/embedding_service.py`

Gọi:

```text
ProviderRuntime.execute_embedding()
```

---

## `src/transport/gateway/router/embeddings.py`

Legacy adapter.

---

# 24. PHASE 3 — Files

## `src/transport/gateway/http/files_router.py`

Giữ behavior nhưng đưa provider access vào:

```text
FileApplicationService
```

### `src/application/providers/file_service.py`

Không để router gọi:

```text
provider object
```

trực tiếp.

---

# 25. PHASE 3 — Admin Provider API

## `src/transport/gateway/router/admin.py`

Hiện dùng:

```text
app.state.router.routing_policy
app.state.router.circuit_breaker_manager
```

Đổi sang:

```text
ProviderAdminService
```

### File mới:

```text
src/application/providers/admin_service.py
```

Methods:

```text
reload_routing()
get_circuit_breaker_status()
get_provider_health()
```

---

# 26. PHASE 3 — Health

## `src/transport/gateway/http/health_router.py`

Không đọc:

```text
app.state.router
```

Dùng:

```text
RuntimeHealthService
ProviderHealthService
StorageHealthService
```

---

# 27. PHASE 4 — STORAGE/UOW NORMALIZATION

## `src/infrastructure/storage/core/unit_of_work.py`

Chuẩn hóa interface:

```text
users
organizations
sessions
projects
messages
attachments
...
```

Không tạo:

```text
uow.repositories
```

nếu architecture chuẩn là typed repositories.

---

# 28. PHASE 4 — Context Engine fix

## `src/context/manager.py`

Đây sẽ trở thành:

```text
ContextBuilder
```

nhiệm vụ:

```text
load session
load messages
load summary
load project
load attachments
load memory
apply token budget
build snapshot
```

Không phát event.

---

## Tạo

### `src/application/context/context_service.py`

```python
class ContextApplicationService:
    async def build_snapshot(...)
```

`ContextRuntime` gọi service này.

---

# 29. PHASE 4 — Context schema

## `src/domain/schemas/context.py`

Phân tách:

```text
ContextRequest
ContextSnapshot
ContextItem
ContextBudget
MemoryReference
```

Không trả raw DB entity.

---

# 30. PHASE 4 — Session Runtime

## `src/runtimes/session/runtime.py`

Biến thành owner của:

```text
session lifecycle
```

Operations:

```text
create
load
append_message
update
close
summarize
```

Không tự giữ toàn bộ session trong:

```text
self._sessions
```

trừ local cache có TTL.

---

# 31. PHASE 4 — Session Service

Tạo:

### `src/application/session/service.py`

```text
create_session
load_session
append_message
get_history
```

Repository:

```text
SessionRepository
MessageRepository
```

là persistence boundary.

---

# 32. PHASE 4 — Session DB files

Giữ nguyên trong Phase này:

```text
src/infrastructure/storage/models/sql/chat_data/session.py
src/infrastructure/storage/models/sql/chat_data/project.py
src/infrastructure/storage/models/sql/chat_data/attachment.py
src/infrastructure/storage/repositories/chat_data/sessions.py
src/infrastructure/storage/repositories/chat_data/projects.py
src/infrastructure/storage/repositories/chat_data/attachments.py
```

Không rename.

Chỉ bổ sung query cần thiết.

---

# 33. PHASE 4 — Message persistence

Đảm bảo mỗi request:

```text
user message
assistant message
tool message
system event
```

được persistence qua service.

Không save trực tiếp trong router.

---

# 34. PHASE 4 — Context cutover

Current:

```text
router
 → event
 → ContextRuntime
 → pass-through payload
```

Target:

```text
router
 → ChatApplicationService
 → ContextRuntime.build()
 → ContextSnapshot
```

Event Bus chỉ phát lifecycle events.

---

# 35. PHASE 5 — TOOL / CAPABILITY UNIFICATION

Hiện có:

```text
runtimes/capability/*
tool/*
```

Đây là lúc thống nhất.

---

# 36. Target abstraction

Tạo:

### `src/domain/extensions/`

```text
definition.py
types.py
permission.py
execution.py
manifest.py
```

Ví dụ:

```python
class ExtensionType(Enum):
    BUILTIN
    MCP
    REMOTE
    WORKFLOW
    CONNECTOR
```

---

# 37. Capability vs Tool

Không xóa ngay.

Thiết kế mapping:

```text
Extension
  ↓
Capability
  ↓
Tool
```

Ví dụ:

```text
GitHub Extension
  ↓
github.search Capability
  ↓
github.search Tool
```

---

# 38. `src/runtimes/capability/registry.py`

Đổi thành:

```text
CapabilityRegistry
```

nhưng không tự xử lý authorization.

Nó chỉ lưu metadata + driver.

---

# 39. `src/runtimes/capability/runtime.py`

Chịu:

```text
resolve capability
authorize
execute
emit lifecycle event
```

---

# 40. `src/tool/registry.py`

Không xóa ngay.

Chuyển thành compatibility wrapper:

```text
ToolRegistryFacade
```

internally:

```text
CapabilityRegistry
```

---

# 41. `src/tool/executor.py`

Giữ execution orchestration thấp-level.

Đổi dependency:

```text
ToolRegistry
```

→

```text
CapabilityResolver
```

---

# 42. `src/tool/base/executor.py`

Giữ abstraction executor.

Các loại:

```text
Local
Native
MCP
Workflow
```

sẽ trở thành:

```text
ExtensionExecutionAdapter
```

---

# 43. `src/runtimes/capability/drivers/base.py`

Đây trở thành interface chuẩn:

```python
class CapabilityDriver:
    metadata()
    validate()
    execute()
    health()
```

---

# 44. Python driver

## `src/runtimes/capability/drivers/python_driver.py`

Chỉ được dùng cho:

```text
trusted server-side extensions
```

Không expose arbitrary client code vào production.

---

# 45. PHASE 5 — Authorization

Tạo:

### `src/application/policy/authorization.py`

Input:

```text
Identity
Tenant
Capability
Arguments
ExecutionContext
```

Output:

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

---

## Identity mapping

### `src/domain/schemas/identity.py`

Giữ.

Bổ sung:

```text
tenant_id
application_id
scopes
permissions
policy_context
```

nếu còn thiếu.

---

# 46. PHASE 5 — Tool API

## `src/transport/gateway/router/tool.py`

Chuyển thành:

```text
CapabilityApplicationService
```

Các API:

```text
POST /v1/capabilities/execute
GET  /v1/capabilities
```

Giữ `/tool` hiện tại làm compatibility endpoint.

---

# 47. PHASE 6 — AGENT RUNTIME

## `src/domain/schemas/agent.py`

Giữ definition.

Bổ sung policy:

```text
model_policy
tool_policy
memory_policy
execution_limits
```

---

# 48. `src/agent/registry.py`

Giữ như control-plane registry.

Không execute agent.

---

# 49. `src/transport/gateway/router/agent.py`

Router chỉ:

```text
register agent
get agent
update agent
delete agent
```

Không resolve trực tiếp tool registry.

Dùng:

```text
AgentApplicationService
```

---

# 50. Tạo Agent Application Service

### `src/application/agent/service.py`

```text
register_agent
validate_agent
resolve_agent
update_agent
```

---

# 51. Tạo Execution Runtime

Folder hiện tại:

```text
src/runtimes/execution/
```

mới có `FOLDER_INFO.md`.

Đây là nơi cần triển khai first-class.

Tạo:

```text
runtime.py
state_machine.py
manager.py
repository.py
limits.py
cancellation.py
events.py
```

---

# 52. `ExecutionRuntime`

State:

```text
CREATED
VALIDATING
CONTEXT_BUILDING
INFERENCE
WAITING_TOOL
EXECUTING_TOOL
RESUMING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

---

# 53. Agent loop

Execution flow:

```text
create execution
 ↓
load agent
 ↓
load session
 ↓
build context
 ↓
invoke provider
 ↓
inspect tool calls
 ↓
authorize tool
 ↓
execute tool
 ↓
append result
 ↓
rebuild context
 ↓
invoke provider
 ↓
...
 ↓
finalize
```

---

# 54. Guardrails

Mỗi execution phải có:

```text
max_iterations
max_tool_calls
max_tokens
timeout
max_cost
```

Không được để Agent loop vô hạn.

---

# 55. Execution persistence

Tạo models:

```text
src/infrastructure/storage/models/sql/execution/
    execution.py
    tool_call.py
    provider_attempt.py
```

Repository:

```text
src/infrastructure/storage/repositories/execution/
    executions.py
    tool_calls.py
    provider_attempts.py
```

---

# 56. Domain schemas execution

## `src/domain/schemas/execution.py`

Đây trở thành source of truth.

Tối thiểu:

```text
Execution
ExecutionInput
ExecutionState
ExecutionResult
ToolCall
ProviderAttempt
ExecutionUsage
```

---

# 57. PHASE 7 — EXTENSION PLATFORM

Đây là phase biến hệ thống thành platform đúng nghĩa.

## Tạo

```text
src/extensions/
    registry.py
    service.py
    manifest.py
    lifecycle.py
    validators.py
    policy.py
```

---

# 58. Extension lifecycle

```text
REGISTERED
VALIDATING
READY
ACTIVE
DEGRADED
DISABLED
REVOKED
```

---

# 59. Extension registration API

Tạo:

```text
POST /v1/extensions
GET /v1/extensions
GET /v1/extensions/{id}
POST /v1/extensions/{id}/enable
POST /v1/extensions/{id}/disable
DELETE /v1/extensions/{id}
```

---

# 60. `src/tool/_mcp/*`

Không xóa.

Chuyển thành:

```text
src/extensions/mcp/
```

File mapping:

```text
tool/_mcp/connection.py
    → extensions/mcp/connection.py

tool/_mcp/executor.py
    → extensions/mcp/executor.py

tool/_mcp/factory.py
    → extensions/mcp/factory.py

tool/_mcp/mcp_manager.py
    → extensions/mcp/manager.py
```

Không move physical file ngay.

Phase đầu tạo facade import để tránh break.

---

# 61. MCP lifecycle

MCP extension phải chịu:

```text
register
validate
connect
initialize
discover
health
reconnect
disable
disconnect
```

---

# 62. MCP security

Bắt buộc thêm:

```text
command allowlist
credential isolation
network policy
timeout
process resource limit
tenant ownership
audit
```

---

# 63. Credential layer

## `src/tool/credential.py`

Chuyển dần thành:

```text
src/infrastructure/secrets/credential_resolver.py
```

Tạo abstraction:

```text
CredentialReference
CredentialResolver
SecretStore
```

Không truyền raw secret vào:

```text
Tool arguments
Event payload
logs
```

---

# 64. PHASE 8 — STREAMING / CONNECTION

## `src/runtimes/connection/runtime.py`

Giữ Runtime.

Nhưng state WebSocket local.

Thêm concept:

```text
execution stream
```

---

# 65. `src/infrastructure/event_bus/ws_manager.py`

Giữ transport concern.

Không để provider runtime biết WebSocket.

---

# 66. Stream event contract

Tạo:

```text
execution.started
execution.delta
execution.tool_call
execution.tool_result
execution.completed
execution.failed
execution.cancelled
```

SSE/WebSocket subscribe vào execution events.

---

# 67. `src/transport/gateway/http/chat_router.py`

Sau khi execution runtime ổn:

```text
POST /v1/chat/completions
```

chỉ:

```text
create execution
stream execution events
map to OpenAI compatible SSE
```

Không subscribe trực tiếp vào provider event.

---

# 68. Legacy streaming compatibility

`src/transport/gateway/router/chat.py`

Giữ.

Nhưng internally gọi:

```text
ChatApplicationService.stream()
```

thay vì:

```text
app.state.router.stream_with_fallback()
```

---

# 69. Cancellation

Tạo:

```text
src/runtimes/execution/cancellation.py
```

Mỗi transport disconnect phải:

```text
cancel(execution_id)
```

ExecutionRuntime propagate xuống:

```text
provider
tool
MCP
workflow
```

---

# 70. PHASE 9 — USAGE / COST / AUDIT

## `src/domain/schemas/usage.py`

Mở rộng thành:

```text
UsageRecord
CostBreakdown
QuotaConsumption
```

---

# 71. Tạo Usage Service

### `src/application/usage/service.py`

Ghi:

```text
tenant
application
user
agent
session
execution
provider
model
input_tokens
output_tokens
latency
cost
```

---

# 72. `src/domain/schemas/pricing.py`

Giữ làm pricing model.

Không để ProviderRuntime tự tính pricing bằng hardcoded logic.

Dùng:

```text
PricingCatalog
```

---

# 73. Persistence

Tạo:

```text
usage_records
provider_attempts
execution_events
```

Database migrations phải theo:

```text
expand
backfill
switch
contract
```

---

# 74. Semantic cache

## `src/infrastructure/storage/services/semantic_cache_service.py`

Giữ nhưng đổi key contract thành:

```text
tenant_id
user_scope
agent_id
model
system_prompt_hash
tool_set_hash
context_hash
request_embedding
```

Không cache theo prompt đơn thuần.

---

# 75. `src/infrastructure/storage/drivers/chroma/driver.py`

Giữ.

Không cho application layer biết Chroma cụ thể.

---

# 76. Redis

## `src/infrastructure/storage/drivers/redis/driver.py`

Giữ.

Tách logical services:

```text
Cache
RateLimit
Lock
Idempotency
Session ephemeral state
```

Không để code gọi:

```text
storage.drivers.get("cache")
```

nếu actual driver là:

```text
redis
```

Tạo service wrapper.

---

# 77. Rate limiter

Các file:

```text
transport/gateway/limiter/
```

giữ.

Nhưng dần chuyển dependency từ:

```text
RedisDriver
```

sang:

```text
RateLimitStore
```

để transport không phụ thuộc storage implementation.

---

# 78. PHASE 10 — DISTRIBUTED RUNTIME

Phase này chỉ làm sau khi single-instance runtime đã ổn định.

## EventBus interface

Tạo:

```text
src/application/events/ports.py
```

Interface:

```text
EventPublisher
EventSubscriber
```

---

# 79. Current EventBus

```text
src/infrastructure/event_bus/bus.py
```

được coi là:

```text
InMemoryEventBus
```

---

# 80. Distributed EventBus

Thêm:

```text
RedisEventBus
```

hoặc:

```text
NATS/Kafka implementation
```

Không đổi runtime API.

---

# 81. Distributed state

Phân loại:

### Local

```text
WebSocket connection
httpx client
provider adapter client
```

### Durable

```text
session
agent
execution
usage
extension
```

### Distributed

```text
rate limit
idempotency
locks
execution lease
```

---

# 82. PHASE 11 — LEGACY REMOVAL

Chỉ thực hiện khi:

```text
new path = 100%
```

và đã ổn định đủ lâu.

---

# 83. Legacy files cần loại bỏ / chuyển compatibility

## Provider

```text
src/provider/__init__.py
```

`ModelRouter` → remove sau cùng.

## Transport legacy

```text
src/transport/gateway/router/chat.py
src/transport/gateway/router/embeddings.py
src/transport/gateway/router/files.py
src/transport/gateway/router/models.py
```

Sau khi HTTP router mới hoàn toàn thay thế.

---

# 84. Legacy admin references

`src/transport/gateway/router/admin.py`

Sau khi `ProviderAdminService` ổn định:

```text
app.state.router
```

được loại bỏ hoàn toàn.

---

# 85. Legacy app.state

Trong `src/main.py`, chỉ còn:

```text
app.state.container
```

Các field:

```text
router
event_manager
tool_registry
agent_registry
```

xóa sau cùng.

---

# 86. File-by-file migration map

## Core / Kernel

| File hiện tại | Phase | Hành động |
|---|---:|---|
| `src/kernel/base.py` | P1 | Mở rộng RuntimeContext thành DI boundary |
| `src/kernel/event.py` | P2 | Chuẩn hóa event base |
| `src/kernel/kernel.py` | P1 | Giữ, thêm service resolution |
| `src/kernel/lifecycle.py` | P1 | Sửa DI/lifecycle |
| `src/kernel/registry.py` | P1 | Giữ, tăng contract |
| `src/kernel/events/provider.py` | P2/P3 | Chuyển sang inference event |
| `src/kernel/events/provider_extended.py` | P3 | Hợp nhất vào inference events |

---

# 87. Provider map

| File | Phase | Action |
|---|---:|---|
| `provider/__init__.py` | P3 | ModelRouter → legacy facade |
| `provider/core/provider.py` | P3 | Giữ provider adapter contract |
| `provider/core/api.py` | P3 | Giữ low-level API abstraction |
| `provider/core/api_mapper.py` | P3 | Giữ |
| `provider/core/auth.py` | P3 | Giữ |
| `provider/core/endpoint.py` | P3 | Giữ |
| `provider/core/model_mapper.py` | P3 | Giữ |
| `provider/core/capability/*` | P3 | Chuẩn hóa model capability |
| `provider/core/interfaces/*` | P3 | Giữ provider feature contracts |
| `provider/discovery.py` | P3 | Trở thành control-plane discovery |
| `provider/exceptions.py` | P3 | Hợp nhất domain/provider errors |
| `provider/executor.py` | P3 | Chỉ low-level execution |
| `provider/factory.py` | P3 | Chỉ instantiate provider |
| `provider/models.py` | P3 | Provider metadata |
| `provider/registry.py` | P3 | Physical provider registry |
| `provider/policies/routing_policy.py` | P3 | ProviderSelectionPolicy |
| `provider/policies/retry.py` | P3 | RetryPolicy |
| `provider/policies/load_balancer.py` | P3 | Candidate selection |
| `provider/handlers/base.py` | P3 | Handler abstraction |
| `provider/handlers/chat_handler.py` | P3 | Inference adapter |
| `provider/handlers/embedding_handler.py` | P3 | Embedding adapter |
| `provider/handlers/file_handler.py` | P3 | File adapter |
| `provider/handlers/model_handler.py` | P3 | Model catalog adapter |
| `provider/openai/*` | P3 | Giữ adapter |
| `provider/google/*` | P3 | Giữ adapter |
| `provider/ollama/*` | P3 | Giữ adapter |

---

# 88. Runtime map

| File | Phase | Action |
|---|---:|---|
| `runtimes/provider/runtime.py` | P3 | Provider execution authority |
| `runtimes/session/runtime.py` | P4 | Session lifecycle authority |
| `runtimes/context/runtime.py` | P4 | Build real ContextSnapshot |
| `runtimes/capability/runtime.py` | P5 | Authorization + capability execution |
| `runtimes/workflow/runtime.py` | P6 | Workflow orchestration |
| `runtimes/connection/runtime.py` | P8 | Connection lifecycle |
| `runtimes/event/runtime.py` | P2 | Event subsystem lifecycle |
| `runtimes/execution/` | P6 | Xây ExecutionRuntime mới |

---

# 89. Tool map

| File | Phase | Action |
|---|---:|---|
| `tool/__init__.py` | P5 | Compatibility facade |
| `tool/registry.py` | P5 | Wrap CapabilityRegistry |
| `tool/executor.py` | P5 | Move to capability execution |
| `tool/base/executor.py` | P5 | Execution adapter contract |
| `tool/credential.py` | P7 | Move to secret/credential service |
| `tool/_mcp/connection.py` | P7 | Move behind MCP Extension |
| `tool/_mcp/executor.py` | P7 | MCP executor adapter |
| `tool/_mcp/factory.py` | P7 | MCP factory |
| `tool/_mcp/mcp_manager.py` | P7 | Extension lifecycle owner |

---

# 90. Storage map

| File/group | Phase | Action |
|---|---:|---|
| `storage/core/manager.py` | P1 | Container-owned storage |
| `storage/core/registry.py` | P1 | Keep registry |
| `storage/core/unit_of_work.py` | P4 | Normalize UoW |
| `storage/core/transaction.py` | P4 | Keep |
| `storage/core/dependency.py` | P1/P4 | Keep as persistence DI |
| `storage/interfaces/*` | P1/P4 | Treat as ports |
| `storage/drivers/sqlite/*` | P4 | Keep implementation |
| `storage/drivers/redis/*` | P4/P10 | Keep implementation |
| `storage/drivers/chroma/*` | P4/P9 | Keep implementation |
| `storage/models/sql/*` | P4/P9 | Add migration only |
| `storage/repositories/*` | P4/P9 | Domain/application persistence boundary |
| `storage/services/embedding_service.py` | P3/P9 | Move under application adapter if appropriate |
| `storage/services/semantic_cache_service.py` | P9 | Secure tenant-aware semantic cache |

---

# 91. Event Bus map

| File | Phase | Action |
|---|---:|---|
| `event_bus/bus.py` | P2 | Stabilize queue |
| `event_bus/manager.py` | P2 | Single registry/container wiring |
| `event_bus/registry.py` | P2 | Single source of truth |
| `event_bus/subscribers.py` | P2 | Remove local registry |
| `event_bus/ws_manager.py` | P8 | Transport bridge |

---

# 92. Auth map

Các file:

```text
transport/gateway/authentication/*
```

không nên refactor sâu ở đầu dự án.

Lý do:

```text
Auth đang là security-critical
```

### Phase 1

Chỉ đưa dependency vào container.

### Phase 4+

Tách dần:

```text
AuthenticationService
IdentityResolver
AuthorizationService
PermissionPolicy
```

Giữ API behavior.

---

# 93. Auth files

| File | Phase | Action |
|---|---:|---|
| `authentication/manager.py` | P1/P4 | Container-owned |
| `authentication/dependency.py` | P1 | DI bridge |
| `authentication/middleware.py` | P1 | Keep transport concern |
| `authentication/authenticators/*` | P1/P4 | Keep |
| `authentication/services/*` | P4 | Move toward application services |
| `authentication/permission.py` | P5 | Feed unified policy engine |
| `authentication/oauth.py` | P1 | Keep |
| `authentication/password.py` | P1 | Keep |
| `authentication/jwt.py` | P1 | Keep |
| `authentication/api_key.py` | P1 | Keep |
| `authentication/exceptions.py` | P4 | Normalize errors |

---

# 94. Transport map

## `transport/gateway/http/*`

Đây là transport canonical sau refactor.

Các router:

```text
chat_router.py
embeddings_router.py
files_router.py
models_router.py
health_router.py
```

sẽ gọi:

```text
Application Services
```

không gọi runtime internals trực tiếp.

---

## `transport/gateway/router/*`

Đây là legacy compatibility layer.

Mục tiêu cuối:

```text
router/*.py
```

không còn là business path.

---

# 95. `src/main.py` target cuối cùng

Lý tưởng:

```python
container = build_application_container()
app.state.container = container
```

Startup:

```text
config
→ observability
→ storage
→ security
→ container
→ runtime kernel
→ runtimes
```

Shutdown:

```text
stop execution intake
→ stop runtimes
→ flush events
→ close HTTP
→ close storage
```

---

# 96. Thứ tự commit/PR nên dùng

Không nên tạo PR khổng lồ.

Đề xuất:

```text
PR-01 baseline tests
PR-02 ApplicationContainer
PR-03 RuntimeContext DI
PR-04 EventBus registry fix
PR-05 EventBus queue fix
PR-06 ProviderRuntime facade
PR-07 Provider shadow mode
PR-08 Provider 5% cutover
PR-09 Provider 100% cutover
PR-10 Session service
PR-11 Context service
PR-12 Context cutover
PR-13 Capability unification
PR-14 Authorization engine
PR-15 Agent application service
PR-16 ExecutionRuntime
PR-17 Agent loop
PR-18 Streaming execution events
PR-19 MCP extension
PR-20 Usage/audit
PR-21 distributed event abstraction
PR-22 remove legacy code
```

---

# 97. Quy tắc mỗi PR

Mỗi PR phải có:

```text
1. backward compatible
2. feature flag nếu đổi execution path
3. test mới
4. metric mới nếu cần
5. rollback path
```

Không merge PR nếu:

```text
old path bị xóa
new path chưa có parity test
```

---

# 98. Quy trình cutover an toàn cho mỗi subsystem

Áp dụng công thức:

```text
Implement
  ↓
Unit Test
  ↓
Integration Test
  ↓
Shadow
  ↓
Canary
  ↓
Traffic 25%
  ↓
Traffic 50%
  ↓
Traffic 100%
  ↓
Observe
  ↓
Deprecate old
```

---

# 99. Feature flags đề xuất

```yaml
refactor:
  provider_runtime:
    enabled: true
    percentage: 100

  session_runtime:
    enabled: true

  context_runtime:
    enabled: true

  capability_runtime:
    enabled: true

  execution_runtime:
    enabled: false

  extension_runtime:
    enabled: false

  distributed_event_bus:
    enabled: false
```

---

# 100. Metrics bắt buộc để quyết định cutover

Cho mỗi subsystem:

```text
new_path_requests_total
new_path_errors_total
legacy_path_requests_total
new_path_latency_p95
legacy_path_latency_p95
new_path_fallback_rate
new_path_stream_disconnects
new_path_tool_errors
new_path_context_build_latency
```

Không switch bằng cảm giác.

---

# 101. Rollback strategy

## Provider

```text
feature flag → false
```

## Context

```text
use legacy raw messages
```

## Capability

```text
legacy ToolRegistryFacade
```

## Execution

```text
legacy direct inference path
```

Mỗi phase phải có rollback độc lập.

---

# 102. Migration dependency graph

```text
P0 Baseline
   │
   ▼
P1 Container / DI
   │
   ├─────────────┐
   ▼             ▼
P2 EventBus     Storage/UoW
   │             │
   └──────┬──────┘
          ▼
P3 ProviderRuntime
          │
          ▼
P4 Session + Context
          │
          ▼
P5 Capability/Tool
          │
          ▼
P6 Agent + Execution
          │
          ▼
P7 Extensions/MCP
          │
          ▼
P8 Streaming/Cancellation
          │
          ▼
P9 Usage/Audit
          │
          ▼
P10 Distributed
          │
          ▼
P11 Legacy removal
```

---

# 103. Những file không nên refactor sớm

Để tránh downtime/risk:

```text
transport/gateway/authentication/*
provider/openai/*
provider/google/*
provider/ollama/*
storage/models/sql/*
storage/migrations/*
```

Trong giai đoạn đầu, chỉ thay dependency boundary.

Không thay behavior nếu không cần.

---

# 104. Những file phải xử lý sớm

Ưu tiên cao:

```text
src/main.py
src/kernel/base.py
src/kernel/lifecycle.py
src/kernel/kernel.py
src/infrastructure/event_bus/registry.py
src/infrastructure/event_bus/subscribers.py
src/infrastructure/event_bus/manager.py
src/infrastructure/event_bus/bus.py
src/provider/__init__.py
src/runtimes/provider/runtime.py
src/runtimes/context/runtime.py
src/context/manager.py
src/runtimes/session/runtime.py
src/transport/gateway/http/chat_router.py
src/transport/gateway/router/chat.py
src/transport/gateway/router/admin.py
```

---

# 105. Thứ tự sửa cụ thể trong ngày đầu tiên

Nếu bắt đầu ngay, không sửa Provider logic trước.

## Bước 1

Tạo:

```text
src/application/container.py
src/transport/gateway/dependencies.py
```

## Bước 2

Sửa:

```text
src/kernel/base.py
src/kernel/lifecycle.py
src/kernel/kernel.py
```

## Bước 3

Sửa:

```text
src/main.py
```

để có:

```text
app.state.container
```

nhưng vẫn mirror các state cũ.

## Bước 4

Sửa Event Registry:

```text
event_bus/registry.py
event_bus/subscribers.py
event_bus/manager.py
```

## Bước 5

Sửa Priority Queue:

```text
event_bus/bus.py
```

Chạy toàn bộ tests.

---

# 106. Thứ tự sửa Provider an toàn

Sau khi P1/P2 ổn:

```text
provider/core/provider.py
provider/registry.py
provider/discovery.py
provider/factory.py
provider/executor.py
provider/policies/*
provider/handlers/*
runtimes/provider/runtime.py
provider/__init__.py
```

Không sửa provider adapter cụ thể trước.

---

# 107. Thứ tự sửa Session/Context

```text
storage/core/unit_of_work.py
storage/repositories/chat_data/*
context/manager.py
domain/schemas/context.py
domain/schemas/session.py
runtimes/session/runtime.py
runtimes/context/runtime.py
application/session/*
application/context/*
```

Sau đó migration chat.

---

# 108. Thứ tự sửa Tool/Capability

```text
domain/schemas/tool.py
runtimes/capability/registry.py
runtimes/capability/drivers/base.py
runtimes/capability/runtime.py
tool/base/executor.py
tool/executor.py
tool/registry.py
tool/__init__.py
transport/gateway/router/tool.py
```

Sau đó mới MCP.

---

# 109. Thứ tự sửa Agent/Execution

```text
domain/schemas/execution.py
domain/schemas/agent.py
agent/registry.py
application/agent/*
runtimes/execution/*
runtimes/workflow/runtime.py
transport/gateway/router/agent.py
```

---

# 110. Thứ tự sửa MCP

```text
tool/_mcp/connection.py
tool/_mcp/factory.py
tool/_mcp/executor.py
tool/_mcp/mcp_manager.py
tool/credential.py
```

Sau khi capability authorization đã ổn.

---

# 111. Thứ tự migration chat cuối cùng

### Legacy path

```text
router/chat.py
  ↓
ModelRouter
  ↓
Provider
```

### New path

```text
http/chat_router.py
  ↓
ChatApplicationService
  ↓
ExecutionRuntime
  ↓
ContextRuntime
  ↓
AgentRuntime
  ↓
ProviderRuntime
```

Chỉ sau khi new path pass compatibility mới switch `/v1/chat/completions`.

---

# 112. Điều kiện để xóa ModelRouter

Không xóa:

```text
src/provider/__init__.py
```

cho tới khi tất cả điểm gọi:

```text
grep -R "app.state.router"
grep -R "ModelRouter"
```

không còn execution dependency ngoài compatibility test.

Command kiểm tra:

```bash
grep -RIn "app\.state\.router\|ModelRouter" src tests
```

Expected cuối:

```text
0 production execution references
```

---

# 113. Điều kiện để xóa event trực tiếp provider khỏi transport

Command:

```bash
grep -RIn 'provider\..*execute\|provider\.stream' src/transport
```

Expected:

```text
0
```

Transport chỉ biết:

```text
application service
```

---

# 114. Điều kiện để xóa direct provider access

Command:

```bash
grep -RIn 'router\.providers\|ProviderRegistry\|ProviderExecutor' src/transport
```

Expected:

```text
0
```

ngoại trừ provider admin service interface nếu có.

---

# 115. Điều kiện để xóa direct storage access

Transport cuối cùng chỉ gọi:

```text
Application Service
```

Không:

```python
request.app.state.storage
```

trong business endpoints.

---

# 116. Kiến trúc sau hoàn tất

```text
                 ┌────────────────────────┐
                 │        Client          │
                 └───────────┬────────────┘
                             ▼
                    ┌────────────────┐
                    │ API / Transport│
                    └───────┬────────┘
                            ▼
                   ┌──────────────────┐
                   │ Application Layer│
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │ Execution Kernel │
                   └────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
      Session           Context            Agent
      Runtime           Runtime           Runtime
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                     Execution Runtime
                            │
             ┌──────────────┼───────────────┐
             ▼              ▼               ▼
        Inference       Capability       Workflow
         Runtime          Runtime         Runtime
             │              │
             ▼              ▼
       Provider APIs       Extensions
                           ├── MCP
                           ├── Built-in
                           └── Remote

     SQL / Redis / Vector / Object / Events
```

---

# 117. Kết luận triển khai

Không nên refactor theo thứ tự "file nào nhìn xấu thì sửa trước".

Thứ tự đúng là:

```text
DI / Container
→ Event infrastructure
→ Provider cutover
→ Session/Context
→ Capability/Tool
→ Agent/Execution
→ Extension/MCP
→ Streaming
→ Usage/Audit
→ Distributed
→ Remove legacy
```

Lý do:

- Container giải quyết wiring.
- Event Bus giải quyết orchestration foundation.
- Provider Runtime là abstraction thấp nhất phục vụ toàn bộ execution.
- Session/Context là state dependency của Agent.
- Capability/Tool là dependency của Agent.
- Execution Runtime mới có thể kết hợp tất cả.
- Extension/MCP là cách mở rộng execution.
- Streaming/Cancellation dựa trên Execution ID.
- Usage/Audit cần execution lifecycle ổn định.
- Distributed chỉ nên làm sau khi semantics local đã ổn định.
- Legacy removal là bước cuối cùng.

## Mốc kiến trúc quan trọng nhất

Có 4 “điểm khóa” cần đạt:

```text
Mốc 1:
app.state → ApplicationContainer

Mốc 2:
ModelRouter → ProviderRuntime

Mốc 3:
raw provider events → Execution lifecycle

Mốc 4:
Tool/Capability/MCP → Extension platform
```

Khi đạt Mốc 4, hệ thống mới thực sự trở thành nền tảng:

> Client đăng ký Agent/Extension/Capability một lần, sau đó chỉ gọi Execution API; toàn bộ provider routing, session, context, memory, tool execution, MCP lifecycle, policy, persistence, streaming, retry, fallback và observability được hệ thống xử lý phía server.
