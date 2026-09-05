# Báo cáo Đánh giá Trạng thái & Lộ trình Triển khai Phase 5: Agent Execution System

> **Canonical current status:** Phase 5.6 closure is governed by
> [`PHASE5_6_EXIT_GATE.md`](./PHASE5_6_EXIT_GATE.md) and
> `tests/architecture/test_phase5_6_exit_gate.py`.
>
> The matrix and narrative below are historical architecture-roadmap notes.
> They must not be interpreted as the current Phase 5.6 release status.

> **Current Phase 5.7 note:** the canonical tool boundary now has an explicit
> `ToolArgumentValidator`, fail-closed capability-schema validation,
> agent-scoped existence/visibility checks, canonical downstream error
> normalization, and provider-side malformed-argument errors. The historical
> Phase 5.7 retry/timeout/cancellation sections in this document are superseded
> for the current implementation; those concerns remain outside this patch.

> **Current Phase 5.7 note:** the repository now contains the explicit
> `ToolArgumentValidator` boundary, fail-closed capability-schema validation,
> canonical tool error normalization, and provider argument parse errors. The
> historical Phase 5.7 retry/timeout wording in this document is superseded;
> those concerns remain outside this Phase 5.7 patch.

## 1. Kết luận Tổng thể

Trạng thái hiện tại của Phase 5 được đánh giá như sau:

| Thành phần / Hạng mục | Trạng thái |
| :--- | :---: |
| Phase 5 Contract Freeze | ✅ |
| Agent execution contracts | ✅ |
| Agent tool-loop harness | ✅ |
| Capability execution foundation | ✅ |
| Provider Runtime foundation | ✅ |
| MCP capability foundation | ✅ |
| Actual AgentRuntime | ❌ |
| InferencePort → Provider adapter | ❌ |
| ToolExecutionPort → Capability | ❌ |
| ContextBuilderPort → ContextRuntime | ❌ |
| Agent policy enforcement | ❌ |
| Execution-wide cancellation | ❌ |
| Execution-wide retry/timeout | ❌ |
| Bounded tool concurrency | ❌ |
| Iteration persistence | ❌ |
| Tool-call/result persistence | ❌ |
| Agent event publication | ❌ |
| Single-agent production E2E | ❌ |

**Kết luận:** Phase 5 hiện tại **mới chỉ đạt mức Contract + Harness + Foundation**, chưa phải một **Complete Agent Execution System** hoàn chỉnh.

**Điểm P0 lớn nhất (Architecture Violation):**
Hệ thống vẫn cấu hình callback trong `main.py`, sau đó callback gọi trực tiếp:

```python
MultiAgentCoordinator.execute_registered_agent_task()
```

dẫn đến:

```python
container.provider_runtime.chat_handler.execute_with_fallback(...)
```

Tức là luồng execution hiện tại vẫn **bypass hoàn toàn AgentRuntime + InferencePort**, vi phạm trực tiếp ranh giới (boundary) đã đóng băng.

---

## 2. Ma trận Implementation Phase 5.1 → 5.14

| Phase | Hạng mục | Hiện trạng Repo | Gap chính | Priority | Kết quả cần đạt |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **5.1** | AgentExecutionContext | Có contract | Context chưa phải authoritative execution state đầy đủ | **P0** | Context chứa execution, identity, input, budget, deadline, cancellation, correlation, runtime state |
| **5.2** | Agent Loop State Machine | Có `AgentLoopState` + transition | Chỉ là contract, chưa được `AgentRuntime` sở hữu | **P0** | Loop engine thật, invariant transition và terminal semantics |
| **5.3** | InferencePort | Có Protocol + DTO | Không có ProviderRuntime adapter | **P0** | `AgentRuntime` → `InferencePort` → `ProviderRuntime` |
| **5.4** | ToolExecution Gateway | Có Protocol | Không có production adapter/coordinator | **P0** | `AgentRuntime` → `ToolExecutionPort` → `CapabilityRuntime` |
| **5.5** | AgentRuntime Core Loop | Chưa có | Đây là missing core hoàn toàn | **P0** | Single-agent inference/tool loop thực tế |
| **5.6** | Policy / Authorization | Có policy protocol; Capability auth có | Chưa có concrete policy enforcement ở tầng Agent | **P0** | Enforce thực sự visibility + authorization + execution limits |
| **5.7** | Retry / Timeout / Cancellation | Capability/harness/coordinator có rải rác | Chưa có hierarchy xuyên suốt execution | **P0** | Chain propagation: execution deadline → inference/tool/MCP + cancellation |
| **5.8** | Bounded Parallel Tools | Harness dùng `asyncio.gather` | Chưa bounded theo contract; chưa canonicalize ordering | **P0** | Bounded concurrency + stable result ordering |
| **5.9** | Persistence / Resume | Có execution/task/session persistence | Thiếu iteration/tool-call/result schema, chưa resume được | **P0** | Durable execution + resumable loop |
| **5.10**| Agent Events / Trace | Contract envelope + correlation có | Thiếu `AgentRuntime` publisher/wiring | **P0** | Publish lifecycle events kèm correlation/causation |
| **5.11**| Context Integration | `ContextRuntime` + `ContextEngine` có | Chưa implement `ContextBuilderPort`; còn legacy flow | **P0** | Immutable per-iteration context snapshot |
| **5.12**| MCP Integration | `CapabilityRuntime` + `McpCapabilityDriver` có | Agent chưa đi qua `ToolExecutionPort` → `CapabilityRuntime` | **P0** | Agent tool-call thực thi được MCP thực |
| **5.13**| Provider Integration | `ProviderRuntime` hỗ trợ Gemini/Routing | Chưa expose provider-neutral `InferencePort` | **P0** | Fake + real provider E2E |
| **5.14**| Acceptance / E2E | Harness + contract tests có | Chưa test `AgentRuntime` production flow | **P0** | A01–A25 chạy qua runtime thật |

---

## 3. Phân tích Chi tiết từng Phase

### Phase 5.1 — AgentExecutionContext (P0)
Current `AgentExecutionContext` đã có cấu trúc cơ bản:
* `execution_id`, `agent_id`, `session_id`, `correlation_id`
* `identity`, `limits`, `request_id`, `parent_execution_id`
* `workflow_id`, `agent`, `iteration`, `tool_calls_used`
* `deadline`, `cancellation_event`

**Gaps chính:** Thiếu một execution-level authoritative model cho:
* `input`
* `current context snapshot`
* `usage/token accounting`
* `cost budget`
* `causation` & `trace`
* `execution status`

*Lưu ý:* `limits` hiện còn dựa vào `AgentExecutionLimits` của domain schema thay vì Agent Runtime policy hoàn chỉnh.

**Yêu cầu P0:** Xác định rõ mô hình authoritative context:

```text
AgentExecutionContext
    ├── immutable identity
    ├── execution metadata
    ├── execution input
    ├── current iteration
    ├── budget/usage
    ├── deadline
    ├── cancellation
    └── correlation context
```

Context phải là nguồn sự thật duy nhất (single source of truth) trong suốt một execution.

---

### Phase 5.2 — Loop State Machine (P0)
Contract state machine hiện có:

```text
PREPARING ──> THINKING ──> TOOL_CALLING ──> WAITING_TOOL ──> THINKING ──> FINALIZING ──> COMPLETED
```

Các trạng thái kết thúc (Terminal states): `FAILED`, `CANCELLED`, `TIMEOUT`.

**Gaps chính:**
1. Chưa có runtime thực tế để điều khiển state machine này.
2. Tồn tại 2 state machine song song trong repo:
   * Domain: `AgentExecutionState`
   * Runtime: `runtimes.agent.contracts.AgentLoopState`

**Yêu cầu P0:**
* Cần explicit mapping rõ ràng giữa hai tầng:

  ```text
  AgentExecutionState.RUNNING ↕ AgentLoopState.THINKING / TOOL_CALLING / ...
  ```

* `AgentRuntime` phải là owner duy nhất quản lý cả `execution lifecycle` lẫn `loop lifecycle`.

---

### Phase 5.3 — InferencePort (P0)
Contract hiện tại đã đáp ứng tiêu chuẩn provider-neutral:

```python
async def complete(
    request: InferenceRequest
) -> InferenceResponse:
    ...
```

**Gaps chính:**
Chưa có `ProviderRuntimeAdapter` triển khai thực tế. `main.py` vẫn gọi trực tiếp qua `MultiAgentCoordinator` → `provider_runtime.chat_handler`, vi phạm nghiêm trọng kiến trúc.

**Yêu cầu P0:** Triển khai luồng chuẩn:

```text
AgentRuntime ──> InferencePort ──> ProviderRuntime ──> ChatExecutionHandler ──> Provider (Gemini/OpenAI/Ollama)
```

---

### Phase 5.4 — ToolExecutionPort (P0)
Contract hiện tại gồm:
* `execute()`
* `execute_many()`

**Gaps chính:**
Chưa có production adapter. Toàn bộ logic (parse arguments, lookup capability, retry, execute, format result, gather) đang nằm trong test harness, tạo thành một mini tool-coordinator thứ hai.

**Yêu cầu P0:** Chuyển toàn bộ logic orchestration vào production component:

```text
AgentRuntime ──> ToolExecutionPort ──> AgentToolExecutionCoordinator ──> CapabilityRuntime
```

*(Harness chỉ được phép mock/fake Port, không tự chứa business orchestration).*

---

### Phase 5.5 — AgentRuntime Core Loop (P0 BLOCKER)
Đây là thiếu sót lớn nhất của hệ thống. File `src/runtimes/agent/runtime.py` hoặc implementation tương đương chưa tồn tại.

**Luồng thực thi mục tiêu:**

```text
AgentExecutionRequest
        │
        ▼
AgentExecutionContext
        │
        ▼
ContextBuilderPort
        │
        ▼
  InferencePort
        │
   Has tool calls?
   ┌────┴─────┐
  NO         YES
   │          │
   ▼          ▼
 FINAL   ToolExecutionPort ──> Tool results ──> Next iteration
```

---

### Phase 5.6 — Policy / Authorization (P0)
Repo đã có `AgentToolPolicy`, `AgentExecutionPolicy`, `AuthorizationService`, `CapabilityState`.

**Gaps chính:** Thiếu tầng thực thi policy tập trung. Hiện tại `CapabilityRuntime.get_available_capabilities(identity)` chỉ lọc theo quyền của identity chứ chưa áp dụng sự kết hợp giữa `AgentDefinition.tools` và `AgentToolPolicy`.

**Yêu cầu P0:** Enforce luồng kiểm tra quyền trước khi expose cho LLM:

```text
Agent requested tool ──> AgentToolPolicy ──> Visible? ──> Authorized? ──> Executable? ──> Expose to LLM
```

---

### Phase 5.7 — Retry / Timeout / Cancellation (P0)
**Gaps chính:** Các cơ chế quản lý vòng đời đang bị xé lẻ (Capability Runtime có timeout/cancel, Coordinator có timeout, Harness có retry). Chưa có chuỗi lan truyền (propagation chain) xuyên suốt.

**Yêu cầu P0:** Thiết lập Hierarchy Execution Control:

```text
Agent Deadline
  ├── ContextBuilder Deadline
  ├── Inference Deadline
  └── Tool Deadline ──> MCP Deadline

Execution.cancel()
  ├── Inference Cancellation
  └── Tool Cancellation ──> CapabilityExecutionContext.cancel() ──> MCP Cancellation
```

---

### Phase 5.8 — Bounded Parallel Tool Execution (P0)
**Gaps chính:** Test harness hiện dùng `asyncio.gather(...)`, mới chỉ chứng minh concurrency nhưng chưa chứng minh **bounded concurrency** và chưa đảm bảo thứ tự kết quả truyền lại.

**Yêu cầu P0:**
* Enforce tham số `max_parallel_tools` bằng `asyncio.Semaphore`.
* Đảm bảo thứ tự kết quả trả về khớp chính xác với mảng đầu vào: `results[i]` tương ứng với `tool_calls[i]`.

---

### Phase 5.9 — Persistence / Resume (P0)
Canonical gate: `docs/phase5/phase5_9/PHASE5_9_EXIT_GATE.md`.

Control Plane persistence is available for `agent_sessions`, `agent_tasks`, and `agent_executions`.
Execution Plane persistence is implemented for `agent_iterations`, `agent_tool_calls`, and
`agent_tool_results`, with `load_execution()`, `load_iteration()`, and `resume_execution()`
available through `DurableAgentStore`.

The Phase 5.9 gate verifies durable checkpoint lookup, pending tool-call reconstruction,
execution-scoped idempotency, runtime checkpoint writes, CI, and canonical documentation.
Full context/transcript rehydration remains a Phase 5.9 follow-up unless explicitly covered by
the runtime resume integration tests.

---

### Phase 5.10 — Event / Correlation (P0)
Contract schema đã có đầy đủ `AgentEventEnvelope`, `CorrelationContext`, `AgentEventName`.

**Gaps chính:** `AgentRuntime` chưa thực sự publish các lifecycle events ra hệ thống.

**Yêu cầu P0:** Biến Event publication thành side-effect có kiểm soát thuộc trách nhiệm trực tiếp của `AgentRuntime`:
* `agent.execution.started` / `completed` / `failed` / `cancelled` / `timeout`
* `agent.iteration.started` / `completed`
* `agent.inference.requested` / `completed`
* `agent.tool.requested` / `started` / `completed` / `failed`

---

### Phase 5.11 — Context Integration (P0)
`ContextRuntime` hiện xây dựng context thông qua `ContextEngine` → `ContextObject` → event `context.command.build`.

**Gaps chính:** Chưa có adapter kết nối `ContextRuntime` với `ContextBuilderPort` để trả về `AgentContextSnapshot` bất biến (immutable) theo từng iteration.

**Yêu cầu P0:** Xây dựng Adapter chuẩn hóa boundary:

```text
AgentRuntime ──> ContextBuilderPort ──> ContextRuntime ──> AgentContextSnapshot
```

---

### Phase 5.12 — MCP Integration (P0)
Cơ sở hạ tầng MCP hiện đã tốt (`CapabilityRuntime` → `McpCapabilityDriver`), nhưng luồng Agent chính thức chưa đi qua chuỗi này.

**Yêu cầu P0:** Nối hoàn chỉnh luồng thực thi:

```text
LLM Tool Call ──> ToolExecutionPort ──> CapabilityRuntime ──> McpCapabilityDriver ──> MCP Server
```

---

### Phase 5.13 — Provider Integration (P0)
`ProviderRuntime` đã hoàn thiện hệ thống routing, executor và handler cho Gemini, OpenAI, Ollama.

**Yêu cầu P0:** Tạo cầu nối canonical bridge `InferencePort` → `ProviderRuntime` để `AgentRuntime` hoàn toàn độc lập với các chi tiết provider bên dưới.

---

### Phase 5.14 — Acceptance / E2E (P0)
Test harness hiện tại đang chạy mock trực tiếp từ `FakeLLM` → `AgentToolLoopHarness` → `CapabilityRuntime`.

**Yêu cầu P0:** Chuyển toàn bộ 25 bài test acceptance (A01–A25) sang chạy trên nền tảng **Production Execution Plane**:

```text
FakeInferencePort ──> AgentRuntime ──> ContextBuilderPort / ToolExecutionPort ──> CapabilityRuntime
```

---

## 4. Tổng hợp 8 P0 Architecture Violations (Blockers)

1. **P0-01:** Phân quyền Agent Execution Authority bị thiếu (Chưa có `AgentRuntime`).
2. **P0-02:** `MultiAgentCoordinator` bypass `AgentRuntime` gọi trực tiếp `ProviderRuntime`.
3. **P0-03:** Thiếu adapter cho `InferencePort` ↔ `ProviderRuntime`.
4. **P0-04:** Thiếu adapter cho `ToolExecutionPort` ↔ `CapabilityRuntime`.
5. **P0-05:** Thiếu adapter cho `ContextBuilderPort` ↔ `ContextRuntime`.
6. **P0-06:** Policy hiển thị và thực thi của Agent chưa do `AgentRuntime` làm authority.
7. **P0-07:** Chưa lưu vết chi tiết `iteration / tool_call / result` để resume execution.
8. **P0-08:** Bộ test E2E mới chỉ test trên Harness, chưa test trên Runtime thực tế.

---

## 5. Danh sách P1 Follow-ups

| ID | Hạng mục | Ưu tiên |
| :--- | :--- | :---: |
| **P1-01** | Usage / token / cost accounting | P1 |
| **P1-02** | Structured error taxonomy cho Agent Runtime | P1 |
| **P1-03** | Idempotency cho tool call | P1 |
| **P1-04** | Duplicate tool-call detection | P1 |
| **P1-05** | Chuẩn hóa Tool argument schema validation | P1 |
| **P1-06** | Typed Event payload schemas (thay vì `Dict[str, Any]`) | P1 |
| **P1-07** | Xử lý các edge cases khi Resume / Crash recovery | P1 |
| **P1-08** | Execution-level metrics | P1 |
| **P1-09** | Provider fallback semantics thông qua `InferencePort` | P1 |
| **P1-10** | Abstraction cho Streaming response | P1 |
| **P1-11** | Canonical Agent Execution HTTP API | P1 |
| **P1-12** | Cleanup legacy coordinator compatibility code | P1 |

---

## 6. Danh sách P2 (Out of Scope Phase 5)

* Multi-agent orchestration nâng cao
* Supervisor strategies
* Distributed worker / Multi-node scheduler
* Durable queue
* Consensus strategy
* Long-running background execution

---

## 7. Lộ trình Triển khai Theo Dependency Graph

```text
5.1 AgentExecutionContext
           │
           ▼
5.2 Loop State Machine
           │
   ┌───────┴───────┐
   ▼               ▼
5.3 Inference   5.4 ToolExecution
   │               │
   └───────┬───────┘
           ▼
    5.5 AgentRuntime Core Loop
           │
           ▼
    5.6 Policy Enforcement
           │
           ▼
    5.7 Timeout / Retry / Cancellation
           │
           ▼
    5.8 Bounded Parallel Concurrency
           │
           ▼
    5.9 Persistence & Resume
           │
           ▼
    5.10 Events & Tracing
           │
           ▼
    5.11 Context Integration
           │
   ┌───────┴───────┐
   ▼               ▼
5.12 MCP        5.13 Provider
   │               │
   └───────┬───────┘
           ▼
    5.14 Full Acceptance Suite
```

---

## 8. Kiến trúc Đích (Target Architecture)

```text
                      Client
                        │
                        ▼
                  Execution API
                        │
                        ▼
              AgentExecutionService
                        │
                        ▼
                  AgentRuntime
                        │
      ┌─────────────────┼─────────────────┐
      ▼                 ▼                 ▼
ContextBuilder       Inference       ToolExecution
    Port              Port              Port
      │                 │                 │
      ▼                 ▼                 ▼
ContextRuntime    ProviderRuntime   CapabilityRuntime
                                          │
                                 ┌────────┴────────┐
                                 ▼                 ▼
                              Python              MCP
```

**Cơ chế Orthogonal Concerns quản lý bởi AgentRuntime:**
* Policy Enforcement
* Limits & Bounded Concurrency
* Cancellation & Timeout Chain
* Retry Strategy
* Execution Persistence
* Event Publication

---

## 9. Definition of Done (DoD)

### Luồng Thực thi Đạt chuẩn End-to-End:

```text
POST /agent execution
        │
        ▼
   AgentRuntime ──> ContextBuilderPort ──> InferencePort
                                                 │
                                        ┌────────┴────────┐
                                        ▼                 ▼
                                      FINAL           Tool Call
                                        │                 │
                                        ▼                 ▼
                                      DONE        ToolExecutionPort
                                                          │
                                                          ▼
                                                  CapabilityRuntime
                                                          │
                                                  (Python / MCP)
                                                          │
                                                          ▼
                                                     ToolResult
                                                          │
                                                          ▼
                                                   Context rebuild
                                                          │
                                                          ▼
                                                    Next Inference
```

### Điều kiện Tiên quyết nghiệm thu:
1. Toàn bộ 25 bài test acceptance (**A01–A25**) phải chạy qua `AgentRuntime` thực tế.
2. Bài test **A23** (Persisted execution resume), **A24** (Concurrent agents), và **A25** (Multi-agent delegation) chỉ được tính là PASS khi chạy trên Execution Plane thật. **Không chấp nhận nghiệm thu thông qua coordinator callback.**

---

> **Chốt trạng thái:**
> * **Phase 5.0 Contract Freeze:** ✅ COMPLETED
> * **Phase 5 Execution Platform:** ❌ NOT COMPLETE
> * **Tổng số blockers:** 8 P0s | 12 P1s | 6 P2s