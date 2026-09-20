# System State & Functional Verification Plan — boxs-51/assistant

## 1. Mục đích tài liệu

Tài liệu này định nghĩa cách kiểm thử hệ thống theo hướng **system verification**, thay vì chỉ kiểm tra từng unit test riêng lẻ.

Hai mục tiêu song song:

1. **State / lifecycle verification**: mô phỏng các trạng thái hoạt động, lỗi, mất kết nối, recovery, shutdown và kiểm tra hệ thống chuyển trạng thái đúng.
2. **Functional verification**: kiểm thử các chức năng hiện có ở mức unit → integration → offline E2E → API E2E, bảo đảm patch không làm thay đổi hành vi đã tồn tại.

Phạm vi baseline được đối chiếu từ commit `cedd8c03fe2e5bb95ad33c66056d3a819f33b75b` và patch hardening v3 đã áp dụng trong workspace.

> Lưu ý về bằng chứng: tôi không có quyền thực thi trực tiếp môi trường Python cục bộ của bạn. Vì vậy tài liệu này phân biệt rõ **static evidence**, **existing tests**, và **runtime evidence do người dùng cung cấp**. Không coi static review là bằng chứng test pass.

---

## 2. Mô hình hệ thống hiện tại

```text
                         HTTP / WS / SSE
                               |
                               v
                    +-----------------------+
                    |   API v1 / Transport  |
                    +-----------+-----------+
                                |
                                v
                    +-----------------------+
                    | ApplicationContainer  |
                    +-----------+-----------+
                                |
                                v
                    +-----------------------+
                    |     RuntimeKernel     |
                    +-----------+-----------+
                                |
          +---------------------+-----------------------+
          |                     |                       |
          v                     v                       v
   SessionRuntime      CapabilityRuntime        ProviderRuntime
          |                     |                       |
          |                     v                       v
          |             CapabilityRegistry       ProviderRegistry
          |                     |                       |
          |              +------+-------+         Mock/Live
          |              |              |
          |              v              v
          |          Python Driver   MCP Driver
          |                             |
          |                             v
          |                    GatewayMcpManager
          |                             |
          +-----------------------------+
                        EventBus
                           |
                           v
                    Event Dispatcher
```

### Các boundary chính

- `ApplicationContainer`: dependency graph ứng dụng.
- `RuntimeKernel`: lifecycle của runtimes.
- `CapabilityRegistry`: nguồn canonical cho capability definition + driver + state.
- `CapabilityRuntime`: execution + health aggregation cho capability.
- `GatewayMcpManager`: lifecycle MCP connection/session và discovery cache.
- `EventBus/EventDispatcher`: bất đồng bộ hóa command/event flow.
- `ProviderRuntime`: provider discovery, routing, fallback và provider handlers.
- `StorageEngine`: persistence/cache/vector drivers.

---

## 3. Mô hình state cần xác minh

### 3.1 Runtime state machine

Các state đang có trong `BaseRuntime`:

```text
CREATED
   |
   v
INITIALIZED
   |
   v
STARTED
   |
   v
RUNNING
   |
   +------> STOPPING -> STOPPED -> DISPOSED
   |
   +------> FAILED
```

Recovery **không được** dùng `DISPOSED` làm trạng thái trung gian.

Luồng recovery kỳ vọng:

```text
RUNNING / FAILED
       |
       v
     STOPPED
       |
       v
   INITIALIZED
       |
       v
     RUNNING
```

`DISPOSED` chỉ dành cho shutdown cuối vòng đời.

### 3.2 Capability state machine

Các state hiện có:

```text
DISCOVERED
    |
    v
REGISTERED
    |
    +---------> DISABLED
    |
    v
 ENABLED <------+
    |            |
    +--> DEGRADED
    |
    +--> UNAVAILABLE
    |
    +--> DISABLED
    |
    +--> REMOVED
```

Một capability chỉ có definition nhưng không có driver không được vào `ENABLED` hoặc `DEGRADED`.

`REMOVED` là terminal state.

`DISABLED` không được health polling tự động bật lại.

### 3.3 MCP connection state

```text
DISCONNECTED
      |
      v
 CONNECTING
      |
      +---- failure ----> DISCONNECTED
      |
      v
 CONNECTED
      |
      +---- health failure ----> FAULTED
                                     |
                                     v
                                 reconnect
                                     |
                                     v
                                 CONNECTING
```

`GatewayMcpManager` chịu trách nhiệm connection/session lifecycle; `CapabilityRuntime` chỉ phản ánh availability của capability.

---

## 4. Nguyên tắc kiểm thử trạng thái

Không kiểm thử riêng lẻ kiểu:

```text
"health() trả True"
```

mà kiểm thử transition:

```text
Before state
   -> Action / fault injection
   -> Health evaluation
   -> After state
   -> Executability
   -> API/consumer-visible behavior
```

Mỗi state test phải trả lời 5 câu hỏi:

1. State trước hành động là gì?
2. Fault hoặc action nào được inject?
3. State sau hành động là gì?
4. Capability/runtime còn executable không?
5. Hệ thống có recovery đúng và giữ nguyên dependency ownership không?

---

## 5. State simulation matrix

| ID | Thành phần | Before | Fault / Action | Expected After | Expected functional effect |
|---|---|---|---|---|---|
| S01 | Runtime | CREATED | initialize | INITIALIZED | context attached |
| S02 | Runtime | INITIALIZED | start | RUNNING | accepts work |
| S03 | Runtime | RUNNING | stop | STOPPED | no new work; handlers removed |
| S04 | Runtime | RUNNING | health failure | recovery path | runtime returns RUNNING if recoverable |
| S05 | Runtime | RUNNING | recovery | RUNNING | event subscriptions restored once |
| S06 | Runtime | RUNNING | final shutdown | DISPOSED | resource release |
| S07 | Capability | DISCOVERED | attach no driver | DISCOVERED/REGISTERED | not executable |
| S08 | Capability | REGISTERED | enable with driver | ENABLED | executable |
| S09 | Capability | ENABLED | health degradation | DEGRADED | policy-defined execution behavior |
| S10 | Capability | ENABLED | MCP unavailable | UNAVAILABLE | `get_driver()` returns none |
| S11 | Capability | UNAVAILABLE | MCP recovers | ENABLED | execution available again |
| S12 | Capability | DISABLED | healthy poll | DISABLED | must remain disabled |
| S13 | Capability | REMOVED | any health event | REMOVED | never executable |
| S14 | MCP | DISCONNECTED | connect | CONNECTING | asynchronous connection attempt |
| S15 | MCP | CONNECTING | success | CONNECTED | tool discovery available |
| S16 | MCP | CONNECTED | list_tools failure | FAULTED | reconnect path active |
| S17 | Provider | no provider | config discovery | error or fallback | no silent live provider creation |
| S18 | Provider | MOCK | normal chat | MOCK response | no network access |
| S19 | Provider | MOCK | fail_next=1 | first fail, second pass | exact fault cardinality |
| S20 | EventBus | queued | dispatch | handled | future resolves / error propagates |

---

## 6. Functional verification matrix

### 6.1 Capability layer

| ID | Function | Verification |
|---|---|---|
| C01 | `register_capability()` | driver registered + ENABLED |
| C02 | `register_definition()` | metadata registered but not executable |
| C03 | `get_driver()` | blocked for unavailable/disabled/removed |
| C04 | `get_all_drivers()` | returns executable capabilities only |
| C05 | authorization | required scopes enforced |
| C06 | normalized result | raw driver output becomes `CapabilityResult` |
| C07 | timeout | `CAPABILITY_TIMEOUT` |
| C08 | cancellation | `CAPABILITY_CANCELLED` |
| C09 | legacy Tool API | `execute_tool()` preserves output contract |
| C10 | state validation | invalid transitions rejected |

### 6.2 MCP

| ID | Function | Verification |
|---|---|---|
| M01 | registration | server connection task created |
| M02 | eager connection | success returns connected |
| M03 | eager connection timeout | explicit timeout error |
| M04 | discovery | descriptors mapped to capabilities |
| M05 | cache | discovery served from MCP cache |
| M06 | execute | correct remote tool invoked |
| M07 | unavailable | capability becomes UNAVAILABLE |
| M08 | recover | capability returns ENABLED |
| M09 | credential boundary | credentials are not silently generated/injected |
| M10 | transport abstraction | capability layer depends on MCP port, not concrete manager |

### 6.3 Runtime lifecycle

| ID | Function | Verification |
|---|---|---|
| R01 | kernel bootstrap | shared `RuntimeContext` created once |
| R02 | dependency order | topological order respected |
| R03 | initialize | all runtimes await async initialization |
| R04 | start | only INITIALIZED runtimes start |
| R05 | stop | reverse dependency order |
| R06 | recovery | initialize is awaited |
| R07 | recovery | dispose is not called |
| R08 | recovery | subscriptions restored exactly once |
| R09 | provider stop | shared `httpx.AsyncClient` remains owned by app |
| R10 | final shutdown | kernel + MCP + HTTP + storage clean up |

### 6.4 Provider/offline

Repository already contains offline/mock tests covering:

- provider registration/discovery;
- deterministic chat;
- deterministic embeddings;
- file upload/download/delete/reset;
- network guard;
- rate-limit fault;
- fail-next exact cardinality;
- streaming failure after N chunks;
- handler path chat/embedding/model/file;
- offline v1 provider APIs;
- streaming chat;
- auth API;
- agents/tools/admin/health/metrics;
- WebSocket event subscribe/unsubscribe.

Các test này nên được coi là regression suite của chức năng hiện hữu, không phải thay thế state simulation.

---

## 7. Event flow cần mô phỏng

### 7.1 Normal chat flow

```text
HTTP request
   |
   v
transport.event.request_received
   |
   v
SessionRuntime
   |
   v
session.event.loaded
   |
   v
ContextRuntime
   |
   v
context.event.built
   |
   v
provider.chat.execute
   |
   v
ProviderRuntime
   |
   v
provider.chat.responded
   |
   v
SessionRuntime persists assistant message
   |
   v
Connection/transport response path
```

### 7.2 Capability execution flow

```text
capability.command.execute
        |
        v
CapabilityRuntime.execute_capability()
        |
        +--> authorization
        |
        +--> registry.get_driver()
        |
        +--> ExecutionContext
        |
        +--> driver.execute()
        |
        v
CapabilityResult
        |
        v
capability.event.executed
```

Failure:

```text
invalid / unauthorized / unavailable / timeout / driver error
                  |
                  v
         CapabilityError
                  |
                  v
       capability.event.failed
```

---

## 8. Failure injection scenarios

### F01 — MCP server down

Setup:

```text
Capability = MCP
State = ENABLED
Session = available
```

Fault:

```text
get_raw_session(server) -> None
```

Expected:

```text
Capability -> UNAVAILABLE
CapabilityRuntime -> DEGRADED
Capability no longer executable
Other capabilities continue working
```

Không kỳ vọng:

```text
MCP outage -> CapabilityRuntime FAILED -> full runtime restart
```

### F02 — MCP recovers

```text
UNAVAILABLE
    |
    +-- session available again --> ENABLED
```

Phải giữ nguyên `CapabilityDefinition`.

### F03 — Disabled capability

```text
DISABLED + health OK -> DISABLED
```

Không được tự động chuyển sang ENABLED.

### F04 — Definition-only capability

```text
DISCOVERED / REGISTERED
    |
    +-- set ENABLED --> ValueError
```

Thông báo exact wording không phải contract chính; semantic rejection mới là contract.

### F05 — Runtime recovery

```text
RUNNING
   |
   +-- health failure
   v
STOPPED
   |
   +-- await initialize
   v
INITIALIZED
   |
   +-- start
   v
RUNNING
```

Phải xác minh:

- initialize được await;
- không có `RuntimeWarning: coroutine was never awaited`;
- handlers không bị duplicate;
- dependency object vẫn là object cũ nếu ownership quy định như vậy;
- shared HTTP client không bị đóng.

### F06 — Provider failure

Dùng MockProvider fault injection:

```text
fail_next=1
```

Expected:

```text
call #1 -> deterministic provider fault
call #2 -> success
```

### F07 — Streaming partial failure

```text
fail_after_chunks=1
```

Expected:

```text
1 chunk emitted
then ProviderUnavailableError
```

---

## 9. Regression đối với test hiện có

### Đã có bằng chứng tốt

`tests/architecture/test_phase3.py` đã kiểm tra:

- authorization/scope;
- capability execution;
- ToolRegistry → CapabilityRegistry metadata delegation;
- transport-neutral capability definition;
- definition-only non-executability;
- invocation-scoped execution context;
- machine-readable capability error;
- normalized `CapabilityResult`;
- injected registry/authorization;
- MCP unavailable → state update → recovery.

`tests/providers/*` đã có coverage cho mock provider, fault semantics, runtime handler path và capability matrix.

`tests/e2e/test_v1_offline.py` đã có coverage cho provider APIs, streaming, auth, tools, agents, multi-agent, admin, health, metrics và WebSocket.

### Failure đã được quan sát sau v3 apply

Test:

```text
`test_capability_state_transitions_are_validated`
```

thất bại không phải vì transition được cho phép, mà vì exception message khác regex mong đợi.

Expected by test:

```text
cannot enter ENABLED
```

Actual:

```text
Invalid capability state transition: DISCOVERED -> ENABLED for 'discovered.only'.
```

Kết luận:

```text
State-machine rejection = đúng
Test message contract = quá chặt
```

Nên assert semantic `ValueError`, hoặc regex vào phần stable của message.

---

## 10. Bộ test chuẩn đề xuất

### Layer A — Static architecture checks

```bash
pytest -q tests/architecture
```

Mục tiêu:

- import contract;
- DI contract;
- lifecycle contract;
- state transition contract;
- dependency direction.

### Layer B — Provider/offline regression

```bash
pytest -q tests/providers
```

Mục tiêu:

- deterministic behavior;
- no-network guarantee;
- fault injection;
- handler routing;
- model capability matrix.

### Layer C — Infrastructure

```bash
pytest -q tests/infrastructure
```

Mục tiêu:

- storage drivers;
- redis/in-memory behavior;
- cache semantics;
- event infrastructure.

### Layer D — Offline API E2E

```bash
pytest -q tests/e2e/test_v1_offline.py
```

Mục tiêu:

- endpoint contract;
- auth;
- provider APIs;
- tools/agents/multi-agent;
- health/metrics;
- websocket.

### Layer E — Full suite

```bash
pytest -q
```

Điều kiện đạt:

```text
0 failed
0 error
0 unexpected warning related to async lifecycle
```

---

## 11. State observability nên xác minh qua log/metrics

Mỗi transition quan trọng cần quan sát được:

```text
component
entity_id
old_state
new_state
reason
correlation_id / request_id
invocation_id (nếu execution)
timestamp
```

Ví dụ:

```json
{
  "component": "capability",
  "capability_id": "github:search",
  "old_state": "ENABLED",
  "new_state": "UNAVAILABLE",
  "reason": "mcp_session_unavailable"
}
```

Đây không phải feature mới; đây là tiêu chí verification để phân biệt “thực sự recovery” với “test chỉ thấy một boolean”.

---

## 12. Tiêu chí pass của hệ thống

### Gate 1 — Compile/import

```text
PASS:
- toàn bộ source import được
- không unresolved import
- không coroutine-not-awaited warning
```

### Gate 2 — State correctness

```text
PASS:
- mọi invalid transition bị reject
- terminal REMOVED không quay lại executable
- DISABLED không tự bật
- definition-only không executable
- MCP outage chỉ ảnh hưởng capability tương ứng
```

### Gate 3 — Recovery correctness

```text
PASS:
- Runtime recovery không dispose
- initialize async được await
- subscriptions không duplicate
- shared dependencies không bị đóng ngoài owner
```

### Gate 4 — Functional regression

```text
PASS:
- mock provider deterministic
- fault semantics chính xác
- tool/capability compatibility không đổi
- v1 offline APIs pass
- session/context/provider flow pass
```

### Gate 5 — End-to-end behavior

```text
PASS:
request -> session -> context -> provider/capability -> response
```

và các failure path tương ứng phải trả về lỗi đúng contract mà không làm chết runtime khác.

---

## 13. Những gì chưa được coi là đã chứng minh

Tại thời điểm lập tài liệu:

1. Chưa có bằng chứng runtime trực tiếp từ workspace của người dùng cho toàn bộ suite sau patch v3.
2. Chưa có evidence rằng mọi API endpoint trong repo đều được exercise ở E2E; hiện chỉ có offline E2E coverage cho một tập endpoint chính.
3. MCP transport thật chưa được coi là pass chỉ dựa trên fake manager test.
4. Recovery của toàn hệ thống qua `HealthMonitor` cần regression test riêng, không chỉ test trực tiếp `check_health()`.
5. Container/Docker cần smoke test thực tế bằng compose khi xác nhận deployment readiness.

---

## 14. Khuyến nghị quy trình verification tiếp theo

### Bước 1
Sửa duy nhất assertion của test state transition để không phụ thuộc wording.

### Bước 2
Chạy:

```bash
pytest -q tests/architecture
pytest -q tests/providers
pytest -q tests/infrastructure
pytest -q tests/e2e/test_v1_offline.py
pytest -q
```

### Bước 3
Thu thập warning:

```bash
pytest -q -W error::RuntimeWarning
```

### Bước 4
Chạy state simulation riêng cho:

```text
Runtime recovery
Capability MCP outage/recovery
Disabled capability
Removed capability
Provider fault injection
Event dispatch failure/retry
```

### Bước 5
Chỉ khi tất cả gate pass mới chuyển sang phase feature tiếp theo.

---

## 15. Kết luận kiến trúc

Hướng kiểm thử mới nên coi hệ thống là một **stateful runtime platform**, không phải tập hợp endpoint độc lập.

Ba invariant quan trọng nhất cần giữ:

```text
INVARIANT A
Một remote capability hỏng không được làm chết cả CapabilityRuntime.

INVARIANT B
Một runtime recovery không được phá dependency do container sở hữu.

INVARIANT C
CapabilityRegistry là nguồn trạng thái canonical; Tool compatibility chỉ là lớp tương thích.
```

Khi ba invariant này được chứng minh bằng state simulation + regression + offline E2E, hệ thống mới có bằng chứng đủ mạnh để tiếp tục mở rộng MCP/Capability mà không tích lũy thêm hidden lifecycle bugs.

---

## Nguồn đối chiếu chính

- `src/kernel/base.py`
- `src/kernel/kernel.py`
- `src/kernel/lifecycle.py`
- `src/kernel/registry.py`
- `src/application/container.py`
- `src/runtimes/capability/registry.py`
- `src/runtimes/capability/runtime.py`
- `src/runtimes/capability/drivers/mcp_driver.py`
- `src/infrastructure/mcp/mcp_manager.py`
- `src/infrastructure/event_bus/bus.py`
- `src/infrastructure/event_bus/registry.py`
- `src/infrastructure/event_bus/manager.py`
- `src/main.py`
- `tests/architecture/test_phase1.py`
- `tests/architecture/test_phase2.py`
- `tests/architecture/test_phase3.py`
- `tests/architecture/test_phase4_execution.py`
- `tests/architecture/test_phase4_multi_agent.py`
- `tests/providers/test_mock_provider.py`
- `tests/providers/test_mock_faults.py`
- `tests/providers/test_mock_runtime_flow.py`
- `tests/providers/test_mock_capabilities.py`
- `tests/e2e/test_v1_offline.py`
