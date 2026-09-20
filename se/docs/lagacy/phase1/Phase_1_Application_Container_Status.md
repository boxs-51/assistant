# Phase 1 — Application Container / Runtime Kernel

## 1. Mục tiêu

Phase 1 có mục tiêu tạo dependency graph trung tâm và giảm phụ thuộc trực tiếp vào `app.state`.

Roadmap yêu cầu:

```text
ApplicationContainer
        ↓
RuntimeKernel
        ↓
Runtimes
```

Các runtime không nên tự tìm dependency từ FastAPI application state.

---

## 2. Trạng thái tổng quan

| Hạng mục | Trạng thái |
|---|---|
| `src/application/container.py` | Hoàn thành |
| Application dependency graph | Hoàn thành một phần tốt |
| Gateway dependency access | Có triển khai |
| Runtime Kernel | Hoàn thành |
| Runtime lifecycle | Hoàn thành |
| Runtime registry | Hoàn thành |
| Dependency resolver | Hoàn thành |
| Health monitor | Hoàn thành |
| Graceful shutdown | Hoàn thành |
| ProviderRuntime được inject vào container | Hoàn thành |
| Session/Context/Capability/Workflow/Connection runtime được bind | Hoàn thành |
| `app.state` bị loại bỏ khỏi transport | Chưa hoàn thành |
| `app.state` bị loại bỏ khỏi runtime/domain | Chưa hoàn thành hoàn toàn |
| Legacy compatibility state | Vẫn còn và đang được dùng |
| Phase 1 architecture test | Có |
| Phase 1 full execution test trong archive environment | Chưa xác nhận do thiếu `structlog` |

### Kết luận

**Phase 1 đã triển khai ở mức cao nhưng chưa hoàn thành mục tiêu cuối cùng.**

Phần “container + kernel + lifecycle” đã hình thành thực tế. Phần “remove direct app.state dependency” vẫn chưa hoàn thành; thay vào đó source hiện đang dùng mô hình **new container + compatibility `app.state`**.

---

## 3. ApplicationContainer

File:

```text
src/application/container.py
```

Container hiện giữ các dependency/runtime quan trọng, gồm:

```text
config
storage
uow_factory
http_client
eventing_manager
agent_registry
tool_registry
multi_agent_coordinator
security services
runtime_kernel
event_runtime
context_runtime
connection_runtime
session_runtime
workflow_runtime
capability_runtime
provider_runtime
```

Đây là dấu hiệu quan trọng cho thấy dependency graph đã được nâng lên khỏi cách khởi tạo rời rạc trong `main.py`.

---

## 4. Bootstrap hiện tại

File:

```text
src/main.py
```

hiện thực hiện chuỗi:

```text
FastAPI
  ↓
bootstrap_observability()
  ↓
bootstrap_storage()
  ↓
bootstrap_security()
  ↓
bootstrap_runtime_kernel()
  ↓
EventingManager
  ↓
ApplicationContainer
  ↓
RuntimeKernel
  ↓
register Runtime
  ↓
kernel.bootstrap()
```

Sau bootstrap, container được gắn các runtime:

```text
container.runtime_kernel
container.event_runtime
container.context_runtime
container.connection_runtime
container.session_runtime
container.workflow_runtime
container.capability_runtime
container.provider_runtime
```

Đây là implementation thật, không chỉ là skeleton.

---

## 5. RuntimeKernel

Các file chính:

```text
src/kernel/base.py
src/kernel/lifecycle.py
src/kernel/kernel.py
src/kernel/registry.py
```

### Đã triển khai

#### BaseRuntime

Có lifecycle state:

```text
CREATED
INITIALIZED
STARTED
RUNNING
PAUSED
STOPPING
STOPPED
DISPOSED
FAILED
```

#### RuntimeContext

Đưa dependency vào runtime:

```text
kernel
config
logger
event_bus
container
storage
metrics
clock
```

#### RuntimeManifest

Mỗi runtime có:

```text
id
name
version
dependencies
exports
permissions
metadata
```

#### RuntimeRegistry

Quản lý:

```text
runtime instances
service exports
```

#### DependencyResolver

Có topological sort để xác định initialization/start order.

#### LifecycleManager

Có:

```text
initialize_all
start_all
stop_all
```

và stop theo reverse dependency order.

#### HealthMonitor

Có periodic health check và gọi:

```text
kernel.recover_runtime()
```

khi runtime fail.

---

## 6. Điểm mạnh

Phase 1 đã tạo được nền tảng runtime khá rõ:

```text
Runtime
  ├── Manifest
  ├── Dependency
  ├── Lifecycle
  ├── Health
  └── Context
```

Điều này rất quan trọng cho các phase sau vì ProviderRuntime, SessionRuntime, ContextRuntime, CapabilityRuntime và WorkflowRuntime đều có cùng abstraction.

---

## 7. Vấn đề còn tồn tại: `app.state`

Roadmap muốn giảm:

```text
runtime → app.state
```

Nhưng source hiện vẫn có nhiều direct access.

Các file tiêu biểu:

```text
src/transport/gateway/http/chat_router.py
src/transport/gateway/http/embeddings_router.py
src/transport/gateway/http/files_router.py
src/transport/gateway/http/models_router.py
src/transport/gateway/http/health_router.py

src/transport/gateway/router/chat.py
src/transport/gateway/router/embeddings.py
src/transport/gateway/router/files.py
src/transport/gateway/router/models.py
src/transport/gateway/router/auth.py
...
```

Tổng cộng trong archive có nhiều module transport/service còn sử dụng:

```python
request.app.state.*
app.state.*
```

Điều này khác với mục tiêu cuối của Phase 1.

---

## 8. Compatibility bridge hiện tại

Thay vì xóa `app.state`, `main.py` đang duy trì:

```text
app.state.container
app.state.event_bus
app.state.event_manager
app.state.runtime_kernel
app.state.provider_runtime
app.state.router
app.state.agent_registry
app.state.tool_registry
```

Trong đó:

```text
app.state.router = LegacyModelRouterFacade(provider_runtime)
```

Đây là một quyết định hợp lý cho migration không big-bang.

Kiến trúc thực tế hiện tại là:

```text
             +----------------------+
             | ApplicationContainer |
             +----------+-----------+
                        |
                        v
                 RuntimeKernel
                        |
        +---------------+---------------+
        |               |               |
      Runtime        Runtime         Runtime
        |
        +---- compatibility ----> app.state
```

Vì vậy:

- **Container architecture: đã có.**
- **App state eradication: chưa xong.**

---

## 9. Test evidence

File:

```text
tests/architecture/test_phase1.py
```

đã kiểm tra ít nhất:

### Container resolution

```text
ApplicationContainer.require()
ApplicationContainer.get()
```

### Shared event registry

Subscriber registration vào registry chung.

### PriorityQueue ordering

Test xác nhận event có cùng priority vẫn được order bằng sequence number, không cần compare event object.

Đây là test trực tiếp cho các thay đổi của Phase 1/2.

---

## 10. Điểm cần chú ý

### 10.1. RuntimeContext vẫn có kiểu `Any`

Một số runtime đang khai báo:

```python
async def initialize(self, context: Dict[str, Any])
```

thay vì dùng thống nhất:

```text
RuntimeContext
```

Điều này tạo type inconsistency dù runtime thực tế nhận object context.

### 10.2. Container chưa trở thành application-level API duy nhất

Transport vẫn có thể lấy dependency qua:

```text
request.app.state
```

nên dependency direction chưa được ép buộc.

### 10.3. `main.py` vẫn rất nặng

`main.py` đang thực hiện nhiều bootstrap concern:

```text
observability
storage
security
runtime
router registration
legacy compatibility wiring
agent wiring
```

Về lâu dài có thể tách bootstrap composition khỏi FastAPI factory.

---

## 11. Definition of Done thực tế

Phase 1 có thể coi là hoàn chỉnh khi:

```text
ApplicationContainer = single dependency graph
```

và các runtime/applications không cần:

```text
request.app.state
app.state
```

trừ compatibility adapter thực sự được cô lập.

Mục tiêu hợp lý:

```text
Transport
   ↓
Application Service
   ↓
Runtime
   ↓
Infrastructure
```

chứ không:

```text
Transport
   ↓
app.state
   ↓
legacy runtime/provider
```

---

## 12. Kết luận

**Current status: `MOSTLY IMPLEMENTED / NOT FULLY CUT OVER`**

Phần khó của Phase 1 — ApplicationContainer, RuntimeKernel, lifecycle, registry, health monitor — đã tồn tại và được wiring trong bootstrap.

Khoảng cách còn lại là:

```text
direct app.state access
legacy compatibility surface
type consistency của RuntimeContext
main.py bootstrap complexity
```

Phase 1 đủ nền tảng để phục vụ Phase 2–4, nhưng chưa đạt “app.state no longer used by runtime/application architecture”.

