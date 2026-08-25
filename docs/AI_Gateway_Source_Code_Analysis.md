# Phân tích toàn diện mã nguồn AI Gateway / AI Runtime

## 1. Tóm tắt điều hành

### 1.1. Mục tiêu kiến trúc được suy ra từ source

Mã nguồn đang hướng tới một **AI Gateway + AI Runtime Platform** đứng giữa client và nhiều nhà cung cấp AI/API, thay vì chỉ là một HTTP reverse proxy.

Các mục tiêu đã thể hiện khá rõ:

- Chuẩn hóa giao tiếp giữa client và nhiều AI provider.
- Che giấu khác biệt API giữa OpenAI / Google / Ollama và các provider khác.
- Quản lý identity, user, organization, application, API key và permission.
- Có session / project / message history / attachment.
- Có lớp Context Runtime để dựng context cho agent.
- Có Capability Runtime / Tool Registry.
- Có MCP integration để mở rộng tool từ bên ngoài.
- Có Workflow/Agent abstraction.
- Có Storage abstraction với SQL, Redis và Vector DB.
- Có Event Bus để tách transport khỏi execution.
- Có Provider discovery, capability discovery, routing, fallback, retry và circuit breaker.
- Có rate limiting, observability, tracing, metrics.
- Có ý tưởng để client chỉ cần "register capability" rồi sử dụng, thay vì tự triển khai toàn bộ logic tích hợp.

### 1.2. Đánh giá tổng thể

**Về ý tưởng kiến trúc: 8/10.**

**Về mức độ hoàn thiện implementation hiện tại: khoảng 4.5/10.**

Lý do là source đã có khá nhiều building block đúng hướng, nhưng các building block chưa được hợp nhất thành một execution model duy nhất. Một số phần vẫn là skeleton, một số phần thuộc kiến trúc cũ, một số phần thuộc kiến trúc mới; chúng cùng tồn tại nhưng chưa được nối hoàn chỉnh.

Có thể diễn đạt ngắn gọn:

> Đây đang là một **platform architecture đang trong giai đoạn chuyển tiếp từ “AI provider gateway” sang “AI execution platform”**, chứ chưa phải một runtime platform hoàn chỉnh.

### 1.3. Giá trị lớn nhất của codebase

Giá trị quan trọng nhất không nằm ở endpoint `/v1/chat/completions`, mà nằm ở việc source đã bắt đầu hình thành các abstraction cấp nền tảng:

```text
Client
  ↓
Gateway / Transport
  ↓
Runtime Kernel
  ├── Session Runtime
  ├── Context Runtime
  ├── Workflow Runtime
  ├── Capability Runtime
  ├── Provider Runtime
  ├── Connection Runtime
  └── Event Runtime
        ↓
Provider / Tool / Storage / Event infrastructure
```

Đây là hướng có thể phát triển thành một **AI Integration Platform / AI Middleware / Agent Runtime Gateway**.

---

# 2. Kiến trúc hiện tại nhìn từ toàn hệ thống

## 2.1. Các lớp chính

Source hiện có thể chia thành 8 vùng:

```text
                    ┌───────────────────────────────┐
                    │           CLIENT              │
                    │ Web / Mobile / Backend / SDK  │
                    └──────────────┬────────────────┘
                                   │ HTTP / SSE / WS
                                   ▼
                    ┌───────────────────────────────┐
                    │ TRANSPORT / GATEWAY           │
                    │ - Auth                        │
                    │ - Router                      │
                    │ - Rate Limit                  │
                    │ - Middleware                  │
                    │ - HTTP / WebSocket            │
                    └──────────────┬────────────────┘
                                   │
                                   ▼
                    ┌───────────────────────────────┐
                    │        RUNTIME KERNEL          │
                    │ - Runtime Registry             │
                    │ - Lifecycle Manager            │
                    │ - Health Monitor               │
                    │ - Dependency ordering          │
                    └──────────────┬────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
      ┌──────────────┐     ┌──────────────┐     ┌───────────────┐
      │ Session      │     │ Context      │     │ Workflow      │
      │ Runtime      │     │ Runtime      │     │ Runtime       │
      └──────┬───────┘     └──────┬───────┘     └──────┬────────┘
             │                    │                    │
             └────────────────────┼────────────────────┘
                                  ▼
                        ┌──────────────────┐
                        │ Capability       │
                        │ Runtime          │
                        │ Tools / MCP      │
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ Provider Runtime │
                        │ Routing/Fallback │
                        │ Retry/CB         │
                        └────────┬─────────┘
                                 │
                  ┌──────────────┼───────────────┐
                  ▼              ▼               ▼
              OpenAI          Google          Ollama
              Provider        Provider        Provider
```

Song song với runtime là các hệ thống nền:

```text
Event Bus
Storage Engine
Redis
SQLite
Chroma
Observability
Configuration
Authentication
Rate Limiter
Circuit Breaker
```

---

# 3. Điểm mạnh kiến trúc

## 3.1. Provider abstraction khá tốt

Source có:

- `BaseProvider`
- provider registry
- discovery
- provider factory
- API interfaces
- request/response converter
- model mapper
- capability manager
- routing policy
- retry policy
- load balancer
- circuit breaker
- fallback chain

Đây là thiết kế đúng với một AI Gateway thực sự.

Client không cần biết:

```text
OpenAI request format
Google request format
Ollama request format
```

vì gateway cố gắng chuyển tất cả về một DTO chuẩn.

Ví dụ:

```text
GatewayChatRequest
        ↓
Provider converter
        ↓
Provider-specific request
        ↓
Provider API
        ↓
Provider-specific response
        ↓
GatewayResponse
```

Đây là một trong những phần có giá trị nhất của source.

---

# 4. Provider Runtime: phần mạnh nhất hiện tại

## 4.1. Provider discovery

`ProviderDiscovery` kết hợp với `ProviderRegistry` giúp hệ thống có khả năng tìm các provider được cấu hình.

Ý tưởng:

```text
Configuration
    ↓
ProviderDiscovery
    ↓
ProviderRegistry
    ↓
ProviderRuntime
```

Điều này tốt hơn việc hard-code provider ở router.

## 4.2. Capability discovery

`ModelCapabilityManager` có ý tưởng tốt:

```text
Provider
  ↓
Model discovery
  ↓
Capability analysis
  ↓
Cache
  ↓
Routing decision
```

Có cả:

- memory cache
- disk cache
- TTL
- model capability parsing
- lazy refresh

Điều này giúp routing có thể phân biệt:

```text
model X supports chat
model X supports streaming
model Y supports embeddings
```

thay vì chỉ kiểm tra tên provider.

## 4.3. Routing + fallback

`RoutingPolicy` và `ModelRouter`/`ProviderRuntime` có cơ chế:

```text
requested model
    ↓
routing rules
    ↓
provider priority
    ↓
preferred provider
    ↓
capability filtering
    ↓
circuit-breaker filtering
    ↓
provider attempt
    ↓
failure
    ↓
next provider
```

Đây là hướng đúng cho multi-provider AI gateway.

## 4.4. Retry + Circuit Breaker

Circuit breaker được triển khai dưới dạng state machine:

```text
CLOSED
   │ failure threshold reached
   ▼
OPEN
   │ reset timeout expired
   ▼
HALF_OPEN
   │ success
   ▼
CLOSED
```

Có lock cho:

- state update
- half-open trial

Đây là thiết kế khá tốt cho asyncio.

## 4.5. Streaming

Provider layer đã có `execute_stream()` và normalized `GatewayStreamChunk`.

Đây là nền tảng đúng để hỗ trợ:

```text
Client
  ↓ SSE
Gateway
  ↓ normalized stream
Provider
  ↓ provider stream
AI API
```

---

# 5. Vấn đề lớn nhất: tồn tại hai kiến trúc Provider song song

Hiện source có ít nhất hai đường execution:

## Kiến trúc cũ

```text
Router
  ↓
ModelRouter
  ↓
RoutingPolicy
  ↓
ProviderExecutor
  ↓
Provider
```

Phần này nằm đáng kể trong:

```text
src/provider/__init__.py
```

với class:

```text
ModelRouter
```

## Kiến trúc mới

```text
Transport Event
  ↓
SessionRuntime
  ↓
WorkflowRuntime
  ↓
ContextRuntime
  ↓
ProviderRuntime
  ↓
ChatExecutionHandler
  ↓
ProviderExecutor
  ↓
Provider
```

Vấn đề là gateway cũ vẫn có nhiều nơi gọi:

```python
request.app.state.router
```

trong khi `main.py` không thấy bootstrap tương ứng:

```python
app.state.router = ...
```

Trong khi `ProviderRuntime` lại được bootstrap.

### Kết luận

Codebase đang trong giai đoạn **refactor từ synchronous ModelRouter architecture sang Runtime/Event architecture** nhưng chưa hoàn tất.

Đây nên được coi là **vấn đề P0/P1**, vì nó không đơn thuần là technical debt mà tạo ra ambiguity về “đâu là execution path chính”.

---

# 6. Runtime Kernel

## 6.1. Ý tưởng

`RuntimeKernel` là abstraction rất đáng giá.

Có:

- `RuntimeRegistry`
- `RuntimeManifest`
- dependency declarations
- dependency resolver
- lifecycle states
- health monitor
- startup/shutdown

Mỗi Runtime có:

```text
initialize()
start()
stop()
check_health()
dispose()
```

Đây là một thiết kế gần với:

```text
micro-kernel
plugin runtime
application runtime host
```

## 6.2. Dependency resolver

`DependencyResolver` sử dụng topological sort.

Điều này giúp Runtime có thể khai báo:

```python
dependencies=["event_runtime"]
```

và Kernel tự quyết định thứ tự startup.

Đây là nền tảng rất tốt cho plugin architecture.

## 6.3. Nhưng RuntimeContext đang truyền dữ liệu chưa đúng

`LifecycleManager` tạo `RuntimeContext` với:

```text
config
event_bus
storage=None
metrics=None
clock=None
```

Trong khi `bootstrap_runtime_kernel()` có:

```text
storage_engine
uow_factory
http_client
```

trong `global_config`.

Nhưng `initialize_all()` lại không inject những thành phần này vào:

```text
RuntimeContext.storage
RuntimeContext.metrics
RuntimeContext.clock
```

Hậu quả:

- runtime không thực sự nhận Storage qua context.
- runtime có xu hướng đọc global/app state hoặc tự tạo dependency.
- nguyên tắc dependency injection bị phá vỡ.

Nên RuntimeContext cần trở thành dependency boundary thực sự.

---

# 7. Session Runtime

## 7.1. Ý tưởng

Session model đã có:

```text
session_id
user_id
organization_id
messages
status
metadata
created_at
updated_at
```

và DB model đã có:

```text
sessions
messages
attachments
projects
```

Đây là nền tảng phù hợp.

## 7.2. Nhưng SessionRuntime hiện chưa thật sự quản lý session

Hiện tại:

```python
self._sessions: Dict[str, Dict[str, Any]] = {}
```

nhưng chưa có logic persistence.

`_on_request_received()` chỉ:

```text
load session ...
publish session.event.loaded
```

và thực tế không load session.

`_on_provider_responded()` cũng chỉ log và `pass`.

### Nghĩa là:

Session model có.

Session database có.

Session repository có.

ContextEngine có.

Nhưng SessionRuntime chưa làm vai trò orchestration thực tế.

---

# 8. Context Runtime

Đây là khu vực hiện tại **chưa hoàn thiện rõ ràng nhất**.

`ContextEngine` được viết khá tốt về mặt ý tưởng:

```text
session
  ↓
project
  ↓
messages
  ↓
attachments
  ↓
ContextObject
```

Nó có khả năng:

- load context
- create session
- summarize session

Nhưng:

## 8.1. ContextRuntime không dùng ContextEngine

`ContextRuntime._handle_build_context()` hiện gần như chỉ:

```text
context.command.build
       ↓
context.event.built
```

Nó không thực sự:

```text
load_context()
```

không:

```text
build prompt
```

không:

```text
truncate context
```

không:

```text
summarize memory
```

không:

```text
inject project files
```

## 8.2. ContextEngine có lỗi integration

Trong `ContextEngine`, code sử dụng:

```python
uow.repositories.get(...)
```

nhưng `SqlAlchemyUnitOfWork` hiện cung cấp repository qua:

```python
uow.users
uow.sessions
uow.projects
...
```

không có:

```python
uow.repositories
```

Đây là một bug integration thực tế.

### Kết luận

Concept Context đúng.

Implementation Context Runtime chưa đạt.

---

# 9. Workflow Runtime

Workflow Runtime có event chain:

```text
session loaded
    ↓
context build
    ↓
provider execute
    ↓
capability executed
    ↓
context build again
```

Điều này cho thấy tác giả đang hướng tới execution loop:

```text
Observe
  ↓
Build context
  ↓
LLM
  ↓
Tool
  ↓
Observe result
  ↓
Build context
  ↓
LLM
```

Đó chính là nền tảng của Agent Runtime.

### Nhưng chưa có state machine thật sự

Hiện WorkflowRuntime chưa có:

- workflow state persistence
- step state
- retry policy theo step
- compensation
- timeout
- cancellation
- parallel steps
- condition
- branching
- human approval
- maximum iterations
- loop protection
- execution id

Vì vậy hiện tại nó mới là **event choreography**, chưa phải workflow engine.

---

# 10. Capability Runtime

Đây là khu vực có tiềm năng rất lớn cho mục tiêu "client đăng ký chức năng là dùng được".

## 10.1. Capability Registry

Có:

```text
CapabilityDefinition
BaseCapabilityDriver
CapabilityRegistry
PythonCapabilityDriver
```

Một capability có thể:

- expose name
- description
- parameter schema
- execute(arguments, context)

Điều này rất phù hợp để xây:

```text
AI Tool Platform
```

## 10.2. Permission filtering chưa hoàn thiện

`get_accessible_tools(identity)` hiện mới loop toàn bộ driver và trả về tất cả tool.

Comment đã chỉ rõ:

```text
TODO: Có thể thêm logic RBAC / Permission check
```

Đây là điểm rất quan trọng.

Với một platform cho nhiều client, việc:

```text
tool available
```

khác hoàn toàn:

```text
tool accessible by this identity
```

Cần có authorization tại capability layer.

---

# 11. Tool layer

Source có một tool subsystem độc lập với Capability Runtime.

Có:

```text
ToolRegistry
ToolExecutor
LocalExecutor
McpExecutor
NativeExecutor
WorkflowExecutor
ExecutorRegistry
CredentialManager
```

Điểm này tạo ra một vấn đề kiến trúc:

## Có hai khái niệm gần nhau

```text
Tool
Capability
```

Cả hai đều làm:

```text
name
definition
arguments
execute
```

Nếu không hợp nhất, về lâu dài sẽ xuất hiện:

```text
Tool Registry
Capability Registry
Agent Registry
MCP Registry
Workflow Registry
```

và client không biết nên đăng ký vào cái nào.

### Đề xuất

Nên có abstraction gốc:

```text
Extension
```

và:

```text
ExtensionType:
    TOOL
    CAPABILITY
    WORKFLOW
    CONNECTOR
    AGENT
    MODEL_SERVICE
```

hoặc chia rõ:

```text
Capability = logical feature
Tool = LLM-callable operation
Connector = external integration
Workflow = orchestration
```

---

# 12. MCP integration

MCP subsystem có nhiều ý tưởng tốt:

- connection lifecycle
- reconnect
- exponential backoff
- health check
- tool cache
- stdio transport
- raw session
- MCP executor

Luồng:

```text
Client / Admin registers MCP server
        ↓
MCP Manager
        ↓
Connection lifecycle
        ↓
initialize
        ↓
list_tools
        ↓
cache tools
        ↓
Expose tools to AI
```

Đây chính xác là hướng để client không cần implement integration logic.

## Nhưng cần thêm security boundary

MCP server là code execution / external execution boundary.

Cần:

- allowlist command
- tenant ownership
- environment isolation
- secret isolation
- network policy
- resource limits
- timeout
- process memory limit
- CPU limit
- execution audit
- tool approval policy

Nếu không, MCP registration có thể trở thành remote execution surface cực kỳ nhạy cảm.

---

# 13. Agent abstraction

`AgentDefinition` có:

```text
name
goal
instruction
tools
workflow_definition
memory_config
```

Đây là schema phù hợp với mục tiêu:

```text
client register an agent
```

Client chỉ cần:

```json
{
  "name": "GithubAgent",
  "goal": "...",
  "instruction": "...",
  "tools": ["github.search", "github.issue.create"],
  "memory_config": {...}
}
```

Gateway sẽ quản lý execution.

### Tuy nhiên:

`AgentRegistry` hiện mới chủ yếu registration.

Chưa thấy AgentRuntime hoàn chỉnh.

Do đó:

> Agent hiện là metadata registration, chưa phải first-class executable runtime.

---

# 14. Storage architecture

Storage abstraction là một điểm rất tốt.

Có:

```text
StorageEngine
DriverRegistry
RepositoryRegistry
UnitOfWork
SQL Driver
Redis Driver
Chroma Driver
```

Điều này cho thấy ý đồ:

```text
application
  ↓
storage engine
  ↓
driver abstraction
  ↓
actual storage implementation
```

## 14.1. SQL storage

Có mô hình khá đầy đủ:

```text
users
organizations
members
roles
permissions
applications
api_keys
oauth_accounts
pending_registrations
projects
sessions
messages
attachments
```

Đây là một foundation khá tốt cho multi-tenant system.

## 14.2. Redis

Dùng cho:

- refresh token
- cache
- rate limiter
- event idempotency dự kiến

## 14.3. Chroma

Dùng cho:

- embeddings
- semantic cache

Đây là hướng đúng cho semantic caching/RAG.

---

# 15. Storage có một lỗi abstraction rất rõ

`StorageEngine` đăng ký driver:

```text
redis
sqlite
chroma
```

nhưng `EventDispatcher` lại lấy:

```python
storage.drivers.get("cache")
```

Trong khi không có driver tên `cache`.

Như vậy event idempotency sẽ không hoạt động đúng theo thiết kế.

Nên quy ước driver phải rõ:

```text
redis = implementation
cache = logical service
```

và:

```text
CacheService -> RedisDriver
```

thay vì consumer tự đoán tên driver.

---

# 16. Event Bus

Event Bus là một thành phần rất quan trọng đối với kiến trúc này.

Có:

- EventRegistry
- EventBus
- PriorityQueue
- EventDispatcher
- retry
- DLQ concept
- idempotency
- WebSocket broadcasting

Đây là một nền tảng tốt.

## 16.1. Nhưng có một lỗi registry nghiêm trọng

`subscribers.py` định nghĩa:

```python
registry = EventRegistry()
```

và decorators đăng ký vào registry đó.

Trong `EventingManager`, lại tạo một registry khác:

```python
self.registry = EventRegistry()
```

`register_subscribers()` chỉ import module.

Điều đó không đồng nghĩa các handler trong `subscribers.py.registry` được đăng ký vào `eventing_manager.registry`.

### Hậu quả

Các subscriber có thể không bao giờ được dispatch bởi EventBus thực tế.

Đây là lỗi wiring lớn.

---

# 17. Event Bus có một rủi ro PriorityQueue

Queue chứa tuple:

```text
(priority, event, future)
```

Nếu hai event có cùng priority, Python có thể phải so sánh tiếp:

```text
event1 < event2
```

trong khi `BaseEvent` không được thiết kế như priority-orderable object.

Đây là lý do nên queue theo:

```text
(priority, sequence_number, event, future)
```

Ví dụ:

```python
(priority, monotonic_counter, event, future)
```

Để đảm bảo ordering deterministic và không phụ thuộc khả năng so sánh của Pydantic model.

---

# 18. Event ordering và semantics

Hiện event bus pha trộn:

```text
Command
Event
Response
Transport signal
Failure
```

Ví dụ:

```text
transport.event.request_received
context.command.build
provider.chat.execute
provider.chat.responded
provider.stream.chunk_emitted
provider.stream.completed
provider.failed
```

Đây là cách đặt tên chưa có contract thống nhất.

Nên phân loại:

```text
command.*
event.*
query.*
```

Ví dụ:

```text
command.session.load
event.session.loaded

command.context.build
event.context.built

command.inference.execute
event.inference.completed

command.tool.execute
event.tool.completed
```

Điều này sẽ giảm coupling.

---

# 19. Gateway và Transport

Transport layer có:

- HTTP
- SSE
- WebSocket
- authentication middleware
- middleware stack
- rate limiting
- observability

Đây là đúng vị trí.

## Tuy nhiên có hai router hierarchy

Có:

```text
transport/gateway/http/
```

và:

```text
transport/gateway/router/
```

Nhiều endpoint trùng trách nhiệm:

```text
chat
models
files
embeddings
```

Một nhóm gọi Event Bus.

Một nhóm gọi trực tiếp `app.state.router`.

Đây là dấu hiệu rõ nhất của kiến trúc chuyển tiếp chưa hoàn tất.

### Khuyến nghị

Chỉ giữ một transport API layer.

Ví dụ:

```text
transport
  ↓
application facade / command bus
  ↓
runtime
```

Transport tuyệt đối không gọi trực tiếp provider.

---

# 20. `app.state` đang trở thành service locator

Hiện code truy cập:

```text
app.state.router
app.state.event_bus
app.state.event_manager
app.state.storage
app.state.auth_manager
app.state.oauth
app.state.http_client
app.state.tool_registry
app.state.agent_registry
```

Nhưng startup hiện chưa register đầy đủ tất cả các state này.

Điều này vừa là bug wiring, vừa là dấu hiệu dependency architecture chưa thống nhất.

### Khuyến nghị

Không dùng `app.state` làm service container chính.

Nên có:

```text
ApplicationContainer
```

với:

```text
storage
event_bus
runtime_kernel
provider_runtime
capability_runtime
auth
rate_limiter
```

và FastAPI dependency chỉ resolve từ container.

---

# 21. Authentication / Identity

Auth subsystem có khá nhiều thành phần:

```text
API Key
JWT
OAuth
OTP
Registration
Login
Refresh Token
Permission
Role
Organization
Application
```

Đây là nền tảng multi-tenant tốt.

## 21.1. Identity abstraction hợp lý

Identity có:

```text
auth_type
user_id
organization_id
session_id
plan
roles
permissions
scopes
```

Đây là rất quan trọng cho extension platform.

Mọi capability về sau nên nhận:

```text
ExecutionContext
```

chứ không chỉ nhận raw `user_id`.

---

# 22. Vấn đề security nổi bật

Có một số điểm cần xử lý trước production.

## 22.1. Secret mặc định

Config có:

```text
session_secret_key = "change-this-in-production"
jwt_secret_key = "change-me"
```

Nếu triển khai mà không override thì không an toàn.

## 22.2. Guest token placeholder

JWT authenticator có:

```text
YOUR_GUEST_PASS_JWT_HERE
```

Đây không nên tồn tại trong production code path.

## 22.3. Forwarded IP

Admin IP verification đọc:

```text
X-Forwarded-For
```

trực tiếp.

Nếu deployment phía trước không có trusted proxy boundary thì có nguy cơ spoof IP.

Phải xác định:

```text
trusted_proxy
```

và chỉ chấp nhận forwarded headers từ proxy được tin cậy.

## 22.4. Tool execution

PythonCapabilityDriver cho phép chạy Python callable.

MCP có process execution.

Do đó platform có các execution surface phải sandbox.

---

# 23. Credential management

`CredentialManager` hiện có mapping:

```text
gdrive
github
```

và lấy token từ:

```text
identity.scopes
```

Đây mới là proof-of-concept.

Một credential platform đúng nên có:

```text
CredentialReference
CredentialProvider
SecretStore
TokenResolver
Scope
Tenant isolation
Rotation
Expiration
Audit
```

Client không nên gửi raw credential vào tool arguments.

---

# 24. Tool authorization model nên được thiết kế lại

Hiện:

```text
Agent -> tools
```

và:

```text
Identity -> permissions
```

nhưng giữa chúng chưa có authorization engine đầy đủ.

Nên có:

```text
Identity
   +
Tenant
   +
Agent
   +
Capability
   +
Tool
   +
Operation
   ↓
Policy Engine
   ↓
ALLOW / DENY / APPROVAL
```

Ví dụ:

```text
github.issue.create
```

có thể yêu cầu:

```text
permission = github:issue:create
scope = repository
environment = production
approval = true
```

---

# 25. Semantic cache

Có:

```text
EmbeddingService
SemanticCacheService
ChromaVectorDriver
```

Điểm này rất phù hợp cho AI Gateway.

Có thể hình dung:

```text
Request
  ↓
normalize
  ↓
semantic fingerprint
  ↓
vector similarity
  ↓
cache hit?
  ├── yes → cached response
  └── no  → provider
```

## Nhưng cần cẩn thận

Semantic cache phải key theo security/semantic boundary.

Không được chỉ cache theo nội dung câu hỏi.

Nên bao gồm:

```text
tenant_id
user_scope
agent_id
model
provider policy
system prompt hash
tool set hash
retrieval context hash
generation parameters
policy version
```

Nếu không, có thể trả response của tenant/user khác.

---

# 26. Rate limiting

Rate limiter có:

- token bucket
- sliding window
- Redis
- Lua scripts
- fail-open/fail-closed

Đây là phần tốt.

Nhưng nên thiết kế quota hierarchy:

```text
Global
  ↓
Organization
  ↓
Application
  ↓
API Key
  ↓
User
  ↓
Agent
  ↓
Provider
  ↓
Model
  ↓
Endpoint
```

Vì gateway AI thường không chỉ giới hạn request/s mà còn cần:

```text
tokens/minute
tokens/day
cost/day
requests/minute
concurrent generations
```

---

# 27. Cost / Usage chưa trở thành first-class domain

Source đã có:

```text
GatewayUsage
Pricing
```

nhưng cần nâng lên thành một hệ thống usage ledger thực sự.

Nên lưu:

```text
request_id
tenant_id
application_id
user_id
agent_id
session_id
provider
model
input_tokens
output_tokens
cached_tokens
latency
cost
currency
status
timestamp
```

Và nên có:

```text
UsageEvent
CostCalculator
BudgetPolicy
QuotaPolicy
BillingAggregator
```

Đây là phần rất quan trọng nếu platform trở thành dịch vụ multi-tenant.

---

# 28. Observability

Có:

```text
logging
metrics
tracing
FastAPI instrumentation
OpenTelemetry
```

Đây là nền tốt.

Nhưng trace context phải xuyên suốt:

```text
client request
 → gateway
 → session
 → context build
 → provider routing
 → provider attempt
 → tool call
 → MCP call
 → storage
```

Nên chuẩn hóa ID:

```text
request_id
trace_id
span_id
execution_id
session_id
agent_run_id
tool_call_id
provider_attempt_id
```

Đây sẽ giúp debug agent execution rất nhiều.

---

# 29. Session, Context, Execution nên tách làm ba concept

Hiện source có xu hướng gom nhiều khái niệm.

Nên phân biệt:

## Session

Long-lived conversation state:

```text
session_id
user
messages
metadata
```

## Context

Một snapshot phục vụ một inference step:

```text
ContextSnapshot
```

Ví dụ:

```text
system instructions
recent messages
summary
retrieved memory
files
tool results
user state
```

## Execution

Một lần chạy AI:

```text
Execution
  execution_id
  session_id
  agent_id
  model
  provider
  input
  output
  tool_calls
  usage
  status
```

Ba khái niệm này càng tách rõ thì platform càng dễ scale.

---

# 30. Agent execution model được đề xuất

Đối với mục tiêu ban đầu, nên xây execution engine theo:

```text
AgentRun
   ↓
Load Session
   ↓
Load Agent Definition
   ↓
Resolve Capabilities
   ↓
Build Context Snapshot
   ↓
Select Model
   ↓
Execute Provider
   ↓
Inspect response
   ↓
Tool call?
   ├── no → Finalize
   │
   └── yes
        ↓
      Policy Check
        ↓
      Tool Execution
        ↓
      Persist Tool Result
        ↓
      Build new Context
        ↓
      Execute Provider
        ↓
      ...
```

Có guard:

```text
max_iterations
max_tool_calls
max_execution_time
max_cost
max_tokens
```

---

# 31. “Client đăng ký là dùng được” nên được thiết kế thành Extension API

Mục tiêu này hoàn toàn khả thi.

Client nên có một contract duy nhất:

```http
POST /v1/extensions
```

Ví dụ:

```json
{
  "name": "github",
  "type": "connector",
  "version": "1.0.0",
  "capabilities": [
    "github.search",
    "github.issue.create"
  ]
}
```

Gateway trả:

```json
{
  "extension_id": "...",
  "status": "registered",
  "capabilities": [...]
}
```

Sau đó Agent chỉ cần:

```json
{
  "tools": [
    "github.search",
    "github.issue.create"
  ]
}
```

Client không cần biết:

```text
MCP protocol
OAuth token
connection lifecycle
reconnect
tool schema
credential mapping
```

---

# 32. Extension lifecycle nên được chuẩn hóa

Mỗi extension nên có:

```text
REGISTERED
  ↓
VALIDATING
  ↓
READY
  ↓
ACTIVE
  ↓
DEGRADED
  ↓
DISABLED
  ↓
REVOKED
```

Metadata:

```text
extension_id
tenant_id
owner
version
type
config
credentials_ref
capabilities
health
permissions
status
```

---

# 33. Nên có Manifest cho extension

Ví dụ:

```yaml
name: github
version: 1.0.0
type: connector

capabilities:
  - name: github.search
    input_schema: ...
    permission: github:read

  - name: github.issue.create
    input_schema: ...
    permission: github:write

credentials:
  - github_oauth

runtime:
  kind: mcp
```

Sau đó runtime có thể tự:

```text
validate
install/register
connect
discover
authorize
publish capabilities
```

Đây chính là bước để biến hệ thống thành platform.

---

# 34. Không nên để client tự gửi Python code

Nếu mục tiêu là "client chỉ đăng ký chức năng", có hai loại:

## Trusted extension

Server-side code/plugin:

```text
PythonCapabilityDriver
```

chỉ cho operator/admin.

## Untrusted extension

Client chỉ khai báo:

```text
manifest
endpoint
mcp server
remote execution
```

Không nhận Python code tùy ý.

---

# 35. Provider abstraction nên nâng thành “Inference Service”

Provider là implementation detail.

Domain nên nói:

```text
InferenceService
```

hoặc:

```text
ModelExecutionService
```

Ví dụ:

```text
InferenceRequest
InferenceResponse
InferenceStream
```

Provider adapter mới làm:

```text
InferenceRequest
    ↓
OpenAI adapter
Google adapter
Ollama adapter
Anthropic adapter
...
```

Điều này cho phép:

```text
one logical model
→ many physical providers
```

---

# 36. Model identity cũng nên tách

Hiện request sử dụng:

```text
model: "gpt-4o"
```

Nhưng platform nên phân biệt:

```text
logical_model
physical_model
provider
deployment
```

Ví dụ:

```text
logical_model = "fast-chat"
provider = openai
physical_model = gpt-4o-mini
```

hoặc:

```text
logical_model = "premium-chat"
providers = [
    openai/gpt-5,
    google/gemini-pro
]
```

Routing policy có thể chọn provider theo:

```text
latency
price
health
capability
region
tenant policy
```

---

# 37. Provider routing nên trở thành policy engine

Hiện RoutingPolicy chủ yếu dựa trên:

```text
priority
rules
provider list
```

Tương lai nên thêm:

```text
cost policy
latency policy
region policy
data residency
tenant policy
model capability
provider health
rate limits
provider quotas
```

Ví dụ:

```text
if tenant.plan == free:
    choose cheap provider

if data_classification == confidential:
    only private providers

if latency_slo < 500ms:
    skip slow providers
```

Đây sẽ biến Gateway thành một router thực thụ.

---

# 38. Data residency / privacy

Đối với một gateway nhiều provider, đây là một concern bắt buộc.

Context nên có classification:

```text
PUBLIC
INTERNAL
CONFIDENTIAL
RESTRICTED
```

Provider policy:

```text
allowed_data_classifications
allowed_regions
allowed_tenants
```

Ví dụ:

```text
tenant A
  ↓ confidential
  ↓
do not route to external provider
```

---

# 39. Failure handling nên chuẩn hóa

Hiện có nhiều dạng:

```text
ProviderError
NoAvailableProviderError
HTTPException
ValidationError
CircuitBreakerOpenError
```

Nên có domain error taxonomy:

```text
GatewayError
 ├── AuthenticationError
 ├── AuthorizationError
 ├── ValidationError
 ├── RoutingError
 ├── ProviderError
 │    ├── ProviderUnavailable
 │    ├── ProviderRateLimited
 │    ├── ProviderTimeout
 │    ├── ProviderBadResponse
 │    └── ProviderCapabilityMismatch
 ├── ToolError
 ├── WorkflowError
 ├── ContextError
 └── StorageError
```

Sau đó transport map thành HTTP/SSE/WebSocket error.

---

# 40. Streaming architecture cần execution-level event

Hiện stream đang làm:

```text
provider.stream.chunk_emitted
```

và transport subscribe trực tiếp.

Ở scale lớn hơn, nên có:

```text
ExecutionStream
```

với:

```text
execution.started
execution.delta
execution.tool_call
execution.tool_result
execution.completed
execution.failed
execution.cancelled
```

SSE/WebSocket chỉ là transport adapter.

Như vậy một execution có thể được quan sát bằng cả:

```text
SSE
WebSocket
polling
webhook
event consumer
```

---

# 41. Cancellation là chức năng cần bổ sung

Streaming client có thể disconnect.

Gateway phải truyền:

```text
cancel(execution_id)
```

từ transport xuống runtime.

Execution context cần:

```text
CancellationToken
```

để:

```text
cancel provider
cancel tool
cancel workflow
cancel MCP call
```

Nếu không, client disconnect nhưng backend vẫn tiếp tục tiêu tốn token/cost.

---

# 42. Idempotency

Nên có:

```text
Idempotency-Key
```

cho các command:

```text
tool execution
workflow execution
agent run
file upload
registration
```

Không chỉ event bus.

---

# 43. Persistence model nên thêm Execution tables

Hiện schema có:

```text
sessions
messages
attachments
```

Nên bổ sung:

```text
agent_runs
executions
tool_calls
tool_results
provider_attempts
usage_records
events
```

Ví dụ:

```text
agent_runs
------------
id
tenant_id
user_id
session_id
agent_id
status
started_at
ended_at
cost
tokens
```

```text
tool_calls
-----------
id
execution_id
tool_name
arguments
result
status
latency
created_at
```

---

# 44. Event log vs Event Bus

Event Bus hiện là in-memory asyncio queue.

Điều này tốt cho:

```text
single process
low latency
internal orchestration
```

nhưng không đủ cho:

```text
multi-instance deployment
```

Nếu chạy:

```text
gateway instance A
gateway instance B
gateway instance C
```

thì event A không tự sang B.

### Giai đoạn production scale

Có thể thay EventBus interface bằng:

```text
InMemoryEventBus
RedisEventBus
KafkaEventBus
NATSJetStreamEventBus
```

Runtime không cần biết backend.

---

# 45. Event Bus nên có delivery semantics

Cần xác định:

```text
at-most-once
at-least-once
exactly-once illusion
```

Với AI workload, thực tế nên thiết kế:

```text
at-least-once + idempotent handler
```

và mọi command mutating đều có:

```text
execution_id / idempotency_key
```

---

# 46. Distributed runtime

Runtime hiện đang process-local.

Khi scale horizontally:

```text
ConnectionRuntime
WebSocket manager
SessionRuntime
CircuitBreaker
CapabilityRegistry
AgentRegistry
```

không thể giữ state chỉ trong memory.

Nên phân biệt:

```text
Local Runtime State
Durable State
Distributed State
```

Ví dụ:

```text
WebSocket connection → local
session → database
rate limit → Redis
circuit breaker → Redis/shared state hoặc local policy tùy yêu cầu
agent definition → database
tool registry → database/control plane
```

---

# 47. Control plane và Data plane

Đây là kiến trúc nên cân nhắc mạnh.

## Control Plane

Quản lý:

```text
users
organizations
applications
API keys
providers
models
agents
extensions
tools
policies
permissions
quotas
```

## Data Plane

Thực thi:

```text
chat
embedding
agent run
tool execution
workflow execution
streaming
```

Tách như vậy sẽ làm platform sạch hơn.

---

# 48. Đề xuất kiến trúc mục tiêu

```text
                        CONTROL PLANE
┌─────────────────────────────────────────────────────┐
│ Tenant / User / App / API Key                      │
│ Agent Registry                                      │
│ Extension Registry                                  │
│ Provider Registry                                   │
│ Policy / Permission / Quota                         │
│ Model Catalog                                       │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │ Execution Kernel │
                  └────────┬─────────┘
                           │
           ┌───────────────┼────────────────┐
           ▼               ▼                ▼
       Session         Context          Policy
       Runtime         Runtime          Engine
           │               │                │
           └───────────────┼────────────────┘
                           ▼
                     Agent Runtime
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
          Capability Runtime     Workflow Runtime
                 │                   │
                 └─────────┬─────────┘
                           ▼
                    Inference Runtime
                           │
                  ┌────────┼────────┐
                  ▼        ▼        ▼
               OpenAI   Google   Ollama
```

Infrastructure:

```text
SQL / Postgres
Redis
Object Storage
Vector DB
Event Bus
Observability
Secret Manager
```

---

# 49. API contract đề xuất cho client

Client không nên phải hiểu nội bộ runtime.

Các API cấp platform:

```text
POST   /v1/sessions
GET    /v1/sessions/{id}
POST   /v1/sessions/{id}/messages

POST   /v1/agents
GET    /v1/agents
DELETE /v1/agents/{id}

POST   /v1/extensions
GET    /v1/extensions
POST   /v1/extensions/{id}/enable
POST   /v1/extensions/{id}/disable

GET    /v1/capabilities
GET    /v1/tools

POST   /v1/executions
GET    /v1/executions/{id}
POST   /v1/executions/{id}/cancel
GET    /v1/executions/{id}/events

POST   /v1/chat/completions
```

`/v1/chat/completions` có thể giữ để compatibility với OpenAI-style client.

---

# 50. Một request hoàn chỉnh nên có execution envelope

Ví dụ:

```json
{
  "session_id": "sess_123",
  "agent_id": "agent_github",
  "model": "fast-chat",
  "input": {
    "messages": [
      {
        "role": "user",
        "content": "Find my open issues"
      }
    ]
  },
  "execution": {
    "max_iterations": 8,
    "max_tool_calls": 10,
    "timeout_ms": 60000
  },
  "metadata": {
    "trace_id": "..."
  }
}
```

Gateway trả:

```json
{
  "execution_id": "exec_123",
  "session_id": "sess_123",
  "status": "running"
}
```

SSE:

```text
event: execution.started

event: inference.started

event: tool.call

event: tool.result

event: inference.delta

event: execution.completed
```

Đây là API contract có thể dùng cho cả synchronous và asynchronous mode.

---

# 51. Quy trình registration mở rộng

Ví dụ client muốn bật GitHub.

### Bước 1

```http
POST /v1/extensions
```

### Bước 2

Gateway:

```text
validate manifest
  ↓
store extension
  ↓
resolve credentials
  ↓
create MCP connection
  ↓
discover tools
  ↓
build capability catalog
  ↓
run health check
  ↓
mark READY
```

### Bước 3

Agent đăng ký:

```json
{
  "name": "GithubAgent",
  "tools": [
    "github.search",
    "github.issue.create"
  ]
}
```

### Bước 4

Agent execution:

```text
Agent
 ↓
Context
 ↓
LLM
 ↓
github.search
 ↓
MCP
 ↓
result
 ↓
LLM
 ↓
final
```

Client không cần hiểu:

```text
MCP session
OAuth token
tool schema
reconnect
provider format
```

Đây chính là mục tiêu ban đầu của hệ thống.

---

# 52. Danh sách vấn đề theo mức độ

## P0 — cần xử lý ngay

### P0.1. Hai execution architecture cùng tồn tại

```text
ModelRouter
vs
ProviderRuntime
```

Cần chọn một source of truth.

### P0.2. Missing app.state wiring

Các component dùng:

```text
app.state.router
app.state.event_manager
app.state.agent_registry
app.state.tool_registry
```

nhưng startup hiện chưa đăng ký đầy đủ.

### P0.3. Event subscriber registry bị tách đôi

`subscribers.registry` khác `EventingManager.registry`.

### P0.4. Session/Context orchestration chưa thực sự execute

Runtime chain hiện mới phát event, chưa thực hiện đầy đủ persistence/context building/tool loop.

---

# 53. P1 — rất quan trọng

### P1.1. ContextEngine dùng `uow.repositories` không tồn tại

Cần sửa API UoW hoặc ContextEngine.

### P1.2. EventBus idempotency dùng driver `"cache"` nhưng StorageEngine có `"redis"`

### P1.3. RuntimeContext không inject Storage/UoW/http client theo đúng thiết kế

### P1.4. Tool Registry và Capability Registry chưa hợp nhất

### P1.5. Agent Runtime chưa hoàn thiện

### P1.6. Authorization cho tools/capabilities chưa thực sự enforce

### P1.7. Streaming chưa có cancellation/execution lifecycle

### P1.8. In-memory runtime state chưa phù hợp multi-instance

---

# 54. P2 — nên xử lý sớm

### P2.1.

Chuẩn hóa event naming.

### P2.2.

Chuẩn hóa exception taxonomy.

### P2.3.

Thêm execution/usage/tool-call persistence.

### P2.4.

Đưa secrets ra Secret Manager.

### P2.5.

Thêm provider/model quota.

### P2.6.

Thêm data classification / provider policy.

### P2.7.

Thêm tenant-aware semantic cache.

---

# 55. Refactoring roadmap đề xuất

## Phase 1 — Stabilize current architecture

Mục tiêu:

```text
single source of truth
```

Làm:

1. Xóa hoặc deprecate `ModelRouter`.
2. Đưa `ProviderRuntime` thành provider execution authority.
3. Tạo `ApplicationContainer`.
4. Đăng ký đầy đủ app dependencies.
5. Hợp nhất EventRegistry.
6. Fix EventBus priority ordering.
7. Fix UoW/ContextEngine integration.
8. Fix Storage cache naming.
9. Bổ sung integration tests.

Kết quả:

```text
Request → Event → Runtime → Provider
```

chạy chắc chắn.

---

# 56. Phase 2 — Complete Session + Context

Triển khai:

```text
SessionRuntime
ContextRuntime
ContextEngine
```

thật sự.

Cần:

```text
load session
create session
append message
load project
load attachments
memory policy
conversation window
summary memory
token budget
```

Output:

```text
ContextSnapshot
```

---

# 57. Phase 3 — Complete Tool/Capability Runtime

Hợp nhất:

```text
ToolRegistry
CapabilityRegistry
MCP
WorkflowExecutor
CredentialManager
```

thành một extension platform.

Thêm:

```text
authorization
timeouts
concurrency limit
retry
audit
credential isolation
```

---

# 58. Phase 4 — Agent Runtime

Thêm:

```text
AgentDefinition
AgentRuntime
ExecutionStateMachine
ToolLoop
Memory
Policy
Budget
Cancellation
```

Execution state:

```text
CREATED
PLANNING
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

# 59. Phase 5 — Distributed runtime

Thay:

```text
in-memory event bus
```

bằng abstraction có backend.

Thêm:

```text
Redis/NATS/Kafka
```

và:

```text
distributed locks
distributed idempotency
distributed execution state
```

---

# 60. Phase 6 — Productize as AI Integration Platform

Khi các phase trên hoàn tất, hệ thống có thể cung cấp:

```text
AI Gateway
+
Agent Runtime
+
Tool Marketplace
+
MCP Runtime
+
Provider Router
+
Session Memory
+
Vector Memory
+
Usage/Billing
+
Policy Engine
```

Lúc này giá trị của platform vượt xa “AI API proxy”.

---

# 61. Cấu trúc module nên hướng tới

Đề xuất:

```text
src/
  core/
    domain/
    execution/
    events/
    policies/

  application/
    sessions/
    agents/
    executions/
    extensions/
    providers/

  runtimes/
    kernel/
    session/
    context/
    agent/
    capability/
    workflow/
    inference/
    connection/

  providers/
    openai/
    google/
    ollama/
    ...

  extensions/
    builtin/
    mcp/
    remote/

  infrastructure/
    persistence/
    cache/
    vector/
    messaging/
    observability/
    secrets/

  transport/
    http/
    websocket/
    sse/
```

Đặc biệt:

```text
domain
```

không nên import:

```text
FastAPI
Redis
SQLAlchemy
httpx
```

---

# 62. Dependency direction nên là

```text
Transport
    ↓
Application
    ↓
Domain

Infrastructure → implements ports/interfaces
Runtime         → orchestrates application/domain
Provider        → adapter
```

Không nên có:

```text
ContextEngine → FastAPI app.state
Runtime → arbitrary global state
Domain → infrastructure concrete implementation
```

---

# 63. Test strategy

Hiện source không cho thấy một test suite đầy đủ trong archive.

Đây là thiếu sót lớn.

Nên có ít nhất:

## Unit tests

- RoutingPolicy
- CircuitBreaker
- RetryPolicy
- ModelCapabilityManager
- Context builder
- Tool authorization
- Workflow resolver

## Integration tests

- OpenAI adapter
- Google adapter
- Ollama adapter
- Redis
- SQL
- Chroma
- EventBus
- MCP

## End-to-end

```text
HTTP
 ↓
Auth
 ↓
Session
 ↓
Context
 ↓
Provider
 ↓
Tool
 ↓
Storage
 ↓
SSE
```

---

# 64. Contract tests cho provider

Mỗi provider adapter nên phải pass cùng bộ contract:

```text
list_models
get_model
chat
chat_stream
embeddings
file operations
capability detection
error mapping
```

Ví dụ:

```python
class ProviderContract:
    async def test_chat(...)
    async def test_stream(...)
    async def test_error(...)
    async def test_capability(...)
```

Như vậy thêm provider mới sẽ không làm architecture bị drift.

---

# 65. Acceptance criteria cho mục tiêu ban đầu

Để nói rằng platform thật sự đạt mục tiêu ban đầu, cần pass các tình huống:

### Case 1 — Multi-provider

Client chỉ gọi:

```text
/v1/chat/completions
```

Gateway có thể:

```text
OpenAI fail
→ Google success
```

không đổi client code.

### Case 2 — Session

Client gửi:

```text
session_id
```

và request sau tự động lấy history.

### Case 3 — Context

Context tự động bao gồm:

```text
messages
summary
attachments
project data
memory
```

### Case 4 — Tool registration

Client đăng ký:

```text
github.search
```

xong Agent có thể gọi tool mà client không phải tự chạy MCP.

### Case 5 — Tool authorization

User A có thể dùng tool.

User B không có permission → DENY.

### Case 6 — Agent registration

Client đăng ký Agent bằng definition.

Sau đó chỉ cần:

```text
agent_id
+ input
```

để chạy.

### Case 7 — Streaming

Client nhận:

```text
token delta
tool call
tool result
final
```

trong cùng execution stream.

### Case 8 — Fault tolerance

Provider chính chết:

```text
fallback
```

vẫn hoạt động.

### Case 9 — Restart

Gateway restart nhưng:

```text
session
agent
tool definitions
usage
execution metadata
```

không mất.

### Case 10 — Horizontal scale

Chạy:

```text
3 gateway instances
```

vẫn đảm bảo:

```text
auth
session
event
execution
rate limit
idempotency
```

hoạt động đúng.

---

# 66. Kết luận cuối cùng

## Source hiện tại có phù hợp với mục tiêu đề ra không?

**Có, về mặt kiến trúc nền tảng.**

Đặc biệt các thành phần sau đã đi đúng hướng:

```text
Provider abstraction
Provider discovery
Capability discovery
Routing
Fallback
Retry
Circuit breaker
Storage abstraction
Session schema
Context schema
Agent definition
Capability runtime
MCP
Workflow
Event bus
Authentication
Rate limiting
Observability
Runtime Kernel
```

## Nhưng đã đạt mục tiêu chưa?

**Chưa.**

Khoảng cách lớn nhất không nằm ở việc thiếu thêm provider.

Khoảng cách nằm ở việc cần hoàn thiện:

```text
Execution Model
Session Persistence
Context Assembly
Agent Loop
Tool Authorization
Extension Lifecycle
Distributed Events
Unified Runtime Wiring
```

## Nhận định quan trọng nhất

Không nên tiếp tục phát triển thêm nhiều tính năng mới trên cấu trúc hiện tại trước khi giải quyết:

```text
ModelRouter vs ProviderRuntime
          +
EventRegistry duplication
          +
app.state service locator
          +
Session/Context skeleton
          +
Tool/Capability duplication
```

Nếu bỏ qua, hệ thống sẽ nhanh chóng trở thành một codebase có rất nhiều abstraction nhưng khó dự đoán execution path.

Ngược lại, nếu refactor đúng, source này có tiềm năng trở thành:

> **Một AI Execution Gateway / Agent Integration Platform, nơi client chỉ cần authenticate + đăng ký extension/agent + gửi execution request, còn gateway chịu trách nhiệm provider routing, context, memory, tool, MCP, workflow, security, persistence, usage và resilience.**

---

# 67. Kiến trúc mục tiêu cô đọng

```text
                    CLIENT / SDK
                         │
                         ▼
                 ┌───────────────┐
                 │ API Gateway   │
                 │ Auth / Quota  │
                 └───────┬───────┘
                         │
                         ▼
                ┌──────────────────┐
                │ Execution Kernel  │
                └────────┬─────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
    Session          Agent/Context      Policy
    Runtime            Runtime          Engine
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                ┌──────────────────┐
                │ Execution Engine │
                └────────┬─────────┘
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Inference    Tool       Workflow
          Runtime      Runtime     Runtime
             │           │
      ┌──────┼──────┐    ├─────────────┐
      ▼      ▼      ▼    ▼             ▼
   OpenAI Google Ollama MCP        Built-in tools

                         │
             ┌───────────┼───────────────┐
             ▼           ▼               ▼
            SQL        Redis          Vector DB
             │           │               │
             └───────────┼───────────────┘
                         ▼
                 Usage / Audit / Events
```

Đây là hướng phù hợp nhất với mục tiêu ban đầu: **client không cần biết hệ thống bên trong phức tạp thế nào; client chỉ thao tác trên một contract thống nhất, còn gateway/runtime đảm nhiệm phần còn lại.**
