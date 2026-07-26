# AI Runtime Backend: Architecture Transition Document
**Version:** 1.0

---

## 1. Mục tiêu

### Hiện trạng
Gateway hiện tại là một API Gateway mở rộng, đang chịu trách nhiệm cho các thành phần:
* HTTP API[cite: 2]
* Authentication[cite: 1, 2]
* Context[cite: 1, 2]
* Tool[cite: 1, 2]
* Provider[cite: 1, 2]
* Event[cite: 1, 2]
* Storage[cite: 1, 2]
* Runtime (một phần)[cite: 1, 2]

Theo quá trình phát triển, Gateway đang dần trở thành nơi chứa toàn bộ business logic. Điều này dẫn tới các vấn đề:
* Router điều phối Tool
* Router điều phối Provider
* Runtime chưa được sử dụng
* Các module phụ thuộc trực tiếp lẫn nhau
* `app.state` trở thành Service Locator

### Mục tiêu
Chuyển đổi **Gateway** thành **AI Runtime Backend**:
* **Gateway:** Chỉ còn đóng vai trò là **Transport Layer**.
* **Runtime:** Nhận toàn bộ business logic chuyển sang.

---

## 2. Kiến trúc hiện tại (As-Is)

                    +-------------------+
                    |     FastAPI       |
                    +---------+---------+
                              |
                     HTTP Request
                              |
                              v
                  +----------------------+
                  |      Router          |
                  +----------------------+
                      |      |       |
                      |      |       |
                      v      v       v
                ToolManager Context Provider
                      |              |
                      |              |
                      +-------> LLM  |
                             |       |
                             +-------+

**Đặc điểm:**
* Router giữ vai trò điều phối.
* `ToolManager` hoạt động độc lập.
* `Context Engine` hoạt động độc lập.
* `Provider` hoạt động độc lập.
* `Runtime` chưa tham gia vào luồng xử lý chính.

---

## 3. Kiến trúc mục tiêu (To-Be)

                    Client
                        |
                        |
                Connection Runtime
                        |
                        |
                Session Runtime
                        |
                        |
                Workflow Runtime
              /         |          \
             /          |           \
            /           |            \
    Context Runtime  Provider   Capability
                        |
                    Event Runtime
                        |
                    Storage

> **Lưu ý:** Gateway lúc này chỉ còn đóng vai trò là **HTTP Adapter**.

---

## 4. Design Principles

### 4.1 Runtime First
Không sử dụng luồng:
$$\text{Gateway} \longrightarrow \text{Business Logic}$$

Thay bằng luồng:
$$\text{Gateway} \longrightarrow \text{Runtime} \longrightarrow \text{Business Logic}$$

### 4.2 Event Driven
Các Runtime không gọi trực tiếp lẫn nhau.

* ❌ **SAI:**
  $$\text{ContextRuntime} \longrightarrow \text{ProviderRuntime}$$
* ✅ **ĐÚNG:**
  $$\text{ContextBuilt Event} \longrightarrow \text{ProviderRuntime}$$

### 4.3 Capability First
Không tồn tại khái niệm `Tool Runtime`, `Skill Runtime`, hay `Plugin Runtime` riêng biệt. Chỉ tồn tại duy nhất **Capability Runtime**.

### 4.4 Session Centric
Mọi hoạt động đều thuộc về Session.

* ❌ **SAI:**
  $$\text{Chat} \longrightarrow \text{Tool} \longrightarrow \text{Workflow}$$
* ✅ **ĐÚNG:**
  $$\text{Session} \longrightarrow \text{Execution}$$

### 4.5 Provider Agnostic
Runtime hoàn toàn trung lập, không phụ thuộc cụ thể vào OpenAI, Gemini, hay Claude mà chỉ tương tác thông qua **Provider Runtime**.

---

## 5. Runtime Boundary

### Connection Runtime
* **Ownership:** Connection, Transport, Heartbeat, Reconnect, Client Registry, Routing.
* **Không quản lý:** Tool, Context, Provider.

### Session Runtime
* **Ownership:** Conversation, Memory, Snapshot, Artifact, Execution State, Checkpoint.

### Context Runtime
* **Ownership:** Planner, Retriever, Budget, Composer, Optimizer.
* **Không quản lý:** Tool, Provider.

### Capability Runtime
* **Ownership:** Capability Registry, Capability Session, Capability Dispatcher, Capability Driver.
* **Driver Types:** Tool, Workflow, Plugin, Agent, Skill.

### Provider Runtime
* **Ownership:** Provider Registry, Executor, Retry, Fallback, Streaming, Cost, Metrics.

### Workflow Runtime
* **Ownership:** Workflow Definition, Execution Graph, Scheduler, Parallel, Loop, Retry, Condition.

### Agent Runtime
* **Ownership:** Agent Definition, Role, Policy, Instruction, Planner, Memory View.

### Event Runtime
* **Ownership:** Event Bus, Store, Replay, Subscription, Metrics, Scheduler.

---

## 6. Dependency Rules

Chỉ cho phép:
$$\text{Runtime} \longrightarrow \text{Publish Event}$$
hoặc
$$\text{Runtime} \longrightarrow \text{Kernel}$$

**Tuyện đối cấm:**
$$\text{Runtime A} \longrightarrow \text{Runtime B}$$

* ❌ **SAI:** `context_runtime.call_provider()`
* ✅ **ĐÚNG:** `ContextBuilt` $\longrightarrow$ `Provider Runtime`

---

## 7. Mapping Hiện tại vs Runtime mới

| Thành phần hiện tại | Runtime mới tương ứng |
| :--- | :--- |
| Gateway Router | Connection Runtime Adapter |
| Gateway | Connection Runtime |
| ToolManager | Capability Runtime |
| Tool Registry | Capability Registry |
| Executor Registry | Capability Driver Registry |
| Tool Schema | Capability Manifest |
| Context Engine | Context Runtime |
| ModelRouter | Provider Runtime |
| EventingManager | Event Runtime |
| StorageEngine | Infrastructure |
| Runtime | Runtime Kernel |
| SessionActor | Session Runtime |
| Event Store | Event Runtime |

---

## 8. Các Module giữ nguyên

Các phần giữ nguyên gần như trọn vẹn:
* Storage[cite: 1, 2]
* Schemas[cite: 1, 2]
* Provider[cite: 1, 2]
* Repository[cite: 1, 2]
* UnitOfWork[cite: 1, 2]
* EventBus[cite: 1, 2]
* Registry Pattern[cite: 1, 2]

---

## 9. Các Module cần Refactor

* **Router** $\longrightarrow$ `Connection Adapter`
* **ToolManager** $\longrightarrow$ `Capability Runtime`
* **ExecutorRegistry** $\longrightarrow$ `Capability Driver`
* **Tool Schema** $\longrightarrow$ `Capability Manifest`

---

## 10. Các Module viết mới

* Connection Runtime
* Capability Session
* Runtime Kernel
* Workflow Runtime
* Runtime Registry
* Lifecycle Manager

---

## 11. Luồng Chat mới

POST /chat
│
▼
Connection Runtime
│
▼
Session Runtime
│
▼
Workflow Runtime
│
▼
Context Runtime
│
▼
Provider Runtime
│
▼
Need Capability? ──(Có)──> Capability Runtime ──> Connection Runtime ──> Desktop
│                                                                      │
(Không)                                                           Capability Result
│                                                                      │
│ <────────────────────────────────────────────────────────────────────┘
▼
Provider Runtime
│
▼
Session Runtime
│
▼
Response

---

## 12. Migration Plan

* **Phase 1:** Không sửa logic hiện tại. Tiến hành khởi tạo `Runtime Kernel`, `Runtime Registry`, và `Runtime Lifecycle`.
* **Phase 2:** Tích hợp `Capability Runtime`. Chuyển đổi `ToolManager` thành Adapter.
* **Phase 3:** Di chuyển toàn bộ `Provider`, `Context`, và `Session` vào cấu trúc Runtime.
* **Phase 4:** Đưa `Workflow Runtime` vào điều phối hệ thống. Loại bỏ hoàn toàn vai trò điều phối của Router.
* **Phase 5:** Thu gọn Gateway, chỉ giữ lại vai trò **Transport Layer**.

---

## 13. Quy tắc phát triển

Mọi module mới phải trả lời được **4 câu hỏi bắt buộc** trước khi thêm vào hệ thống:

1. **Nó thuộc Runtime nào?** Nếu không thuộc Runtime nào thì tuyệt đối không được thêm.
2. **Nó sở hữu (owns) dữ liệu hoặc vòng đời gì?** Không được phép để hai Runtime cùng sở hữu một trạng thái.
3. **Nó giao tiếp bằng Event hay gọi trực tiếp?** Ưu tiên giao tiếp qua Event; việc gọi trực tiếp chỉ được chấp nhận thông qua Kernel đối với các dịch vụ hạ tầng.
4. **Nó có thể thay thế (replaceable) không?** Các thành phần như Provider, Capability Driver, hay Transport đều phải đảm bảo tính cắm-rút, có thể thay thế mà không ảnh hưởng tới các Runtime khác.