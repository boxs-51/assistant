# Phase 5 — Agent Runtime Execution Platform

## Kiến trúc đích

```text
                         ┌─────────────────────┐
                         │     AgentRuntime    │
                         │                     │
User ──> Execution ─────>│  Agent Execution    │
                         │       Loop          │
                         └──────────┬──────────┘
                                    │
                   ┌────────────────┼────────────────┐
                   │                │                │
                   ▼                ▼                ▼
             ContextRuntime   ProviderRuntime   CapabilityRuntime
                   │                │                │
                   ▼                ▼                ▼
                Context          LLM/API        Tool/MCP
                   │                │                │
                   └────────────────┼────────────────┘
                                    ▼
                             ExecutionResult
```

Trong đó:

- `MultiAgentCoordinator` = control plane / task plane
- `AgentRuntime` = execution plane / inference loop

Đây là ranh giới quan trọng nhất.

---

## 1. Phase 5.0 — Freeze Contract

Trước khi viết `AgentRuntime`, cần khóa các contract.

Hiện tại `AgentDefinition` mới có:
- `name`
- `goal`
- `instruction`
- `tools`
- `workflow_definition`
- `memory_config`

Và `AgentExecutionRequest` có:
- `session_id`
- `agent_id`
- `input`
- `parent_execution_id`
- `limits`

Trong khi `AgentRuntime` sẽ cần thêm policy mà hiện chưa tồn tại rõ ràng.

**Đề xuất cấu trúc:**

```text
AgentDefinition
├── identity
├── instruction
├── model_policy
├── capability_policy
├── memory_policy
├── execution_policy
└── tool_policy
```

Ví dụ:

```python
AgentDefinition(
    name="researcher",
    goal="Research information",
    instruction="...",
    tools=["github:search", "calculator.add"],
    model_policy=...,
    execution_policy=...,
)
```

> **Lưu ý:** Không nên để `AgentRuntime` đọc config provider trực tiếp.

---

## 2. Phase 5.1 — AgentExecutionContext

Đây sẽ là object quan trọng nhất của Agent Runtime.

Hiện `CapabilityExecutionContext` đã tồn tại và có: `identity`, `execution_id`, `invocation_id`, `request_id`, `session_id`, `workflow_id`, `deadline`, `attempt`, `metadata`, `cancellation_event`.

Agent Runtime nên có context cấp cao hơn:

```text
AgentExecutionContext
├── execution
├── agent
├── identity
├── session
├── input
├── context_snapshot
├── iteration
├── tool_call_count
├── token_budget
├── cost_budget
├── deadline
├── cancellation
└── trace
```

**Quan hệ:**

```text
AgentExecutionContext
        │
        └── CapabilityExecutionContext
```

> Agent context không nên thay thế capability context.

---

## 3. Phase 5.2 — Agent State Machine

State machine hiện tại:
`CREATED` → `RUNNING` → `WAITING_AGENT` → `COMPLETED` / `FAILED` / `CANCELLED` / `TIMEOUT` của `AgentExecution` là tốt cho coarse-grained execution, nhưng chưa đủ cho inference loop.

**Đề xuất thêm state nội bộ:**

```text
CREATED
   ↓
PREPARING
   ↓
THINKING
   ↓
TOOL_CALLING
   ↓
WAITING_TOOL
   ↓
THINKING
   ↓
COMPLETED
```

**Failure handling:**

```text
THINKING ─────→ FAILED
TOOL_CALLING ─→ FAILED
WAITING_TOOL ─→ TIMEOUT
ANY           ─→ CANCELLED
```

**Quan trọng:**
- `AgentExecutionState`: state persisted.
- `AgentLoopState`: state runtime.

*(Không trộn hai cái này với nhau).*

---

## 4. Phase 5.3 — Inference Port

Đây là abstraction ưu tiên nhất. `AgentRuntime` không được biết Gemini/OpenAI/Ollama.

**Cấu trúc thư mục:**

```text
src/runtimes/agent/inference/
├── port.py
├── request.py
├── response.py
└── adapters/
```

**Contract:**

```python
class InferencePort(ABC):

    async def complete(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        ...
```

`AgentRuntime` chỉ gọi: `InferencePort.complete()`. `ProviderRuntime` là implementation.

```text
AgentRuntime
    ↓
InferencePort
    ↓
ProviderRuntime
    ↓
OpenAI / Gemini / Ollama
```

Điều này giúp agent hoàn toàn độc lập với provider.

---

## 5. Phase 5.4 — Tool Gateway

Bước kết nối test harness vào production runtime.

Agent nhận:

```json
{
  "tool_calls": [
    {
      "id": "call_001",
      "name": "github:search",
      "arguments": {
        "query": "asyncio"
      }
    }
  ]
}
```

Agent Runtime không gọi MCP trực tiếp. Flow xử lý:

```text
tool_call
   ↓
ToolExecutionCoordinator
   ↓
CapabilityRuntime.execute_capability()
   ↓
CapabilityResult
```

Tức là:

```text
AgentRuntime
      ↓
ToolExecutionCoordinator
      ↓
CapabilityRuntime
```

Đây chính là nơi kết quả từ Phase Agent Tool Test Harness trở thành production contract.

---

## 6. Phase 5.5 — Tool Loop

Đây là MVP thực sự của Agent Runtime.

**Pseudo-flow:**

```python
while True:

    check_limits()

    context = await context_runtime.build(...)

    response = await inference.complete(...)

    if not response.tool_calls:
        return complete(response)

    tool_results = await tool_executor.execute_many(
        response.tool_calls
    )

    append_tool_results(tool_results)
```

**Kiến trúc:**

```text
                ┌───────────────┐
                │ Agent Runtime │
                └───────┬───────┘
                        │
                        ▼
                 Build Context
                        │
                        ▼
                 Inference Call
                        │
               ┌────────┴────────┐
               │                 │
          final answer        tool calls
               │                 │
               ▼                 ▼
           COMPLETE       Tool Execution
                                 │
                                 ▼
                            Capability
                                 │
                                 ▼
                            Tool Results
                                 │
                                 └───────→ next iteration
```

---

## 7. Phase 5.6 — Tool Execution Coordinator

Component đáng tách riêng:

```text
src/runtimes/agent/tool_execution/
├── coordinator.py
├── policy.py
├── validator.py
├── formatter.py
└── result.py
```

**Trách nhiệm:**
- Tool call parsing
- Tool lookup
- Authorization
- Argument validation
- Execution
- Timeout
- Retry
- Parallelization
- Result normalization

*(Không để tất cả nằm trong AgentRuntime).*

---

## 8. Phase 5.7 — Argument Validation

Tool definition đã có `CapabilityDefinition.input_schema`, nhưng `CapabilityRuntime.execute_capability()` hiện chủ yếu đưa arguments xuống driver.

**Flow xử lý tại Agent Runtime:**

```text
LLM arguments
   ↓
JSON parse
   ↓
Schema validation
   ↓
Authorization
   ↓
Capability execution
```

**Error contract:**
- `CAPABILITY_NOT_FOUND`
- `CAPABILITY_UNAUTHORIZED`
- `CAPABILITY_INVALID_ARGUMENT`
- `CAPABILITY_TIMEOUT`
- `CAPABILITY_CANCELLED`
- `CAPABILITY_EXECUTION_FAILED`

Repo hiện đã có `CapabilityError`, nên tiếp tục dùng contract đó thay vì tạo exception hierarchy mới.

---

## 9. Phase 5.8 — Retry Policy cho Tool

Không phải tool failure nào cũng retry.

Phân loại:

| Lỗi | Retry? |
|---|---|
| Timeout | **YES** |
| Network error | **YES** |
| MCP unavailable | **YES** |
| Rate limit | **YES** |
| Validation error | **NO** |
| Permission denied | **NO** |
| Tool not found | **NO** |
| Business error | Policy quy định |

Agent runtime không tự quyết định. Dùng `ToolRetryPolicy` và lấy `CapabilityError.retryable` làm input.

---

## 10. Phase 5.9 — Parallel Tool Calls

Nếu LLM trả về nhiều tool call (ví dụ: `github.search`, `github.search`, `calculator.add`), coordinator phải dùng `await asyncio.gather(...)` với **bounded concurrency** (`max_parallel_tool_calls`). Không dùng unbounded gather.

**Flow:**

```text
ToolCall[]
    ↓
validation
    ↓
authorization
    ↓
partition
    ↓
bounded parallel execution
    ↓
ToolResult[]
```

> **Lưu ý:** Thứ tự result nên giữ theo `tool_call.id`, không theo completion order.

---

## 11. Phase 5.10 — Tool Loop Limits

`AgentExecutionLimits` cần trở thành policy thực sự với các tham số:

- `max_iterations = 8`
- `max_tool_calls = 16`
- `max_parallel_tools = 4`
- `timeout_seconds = 60`

**Cần enforce:**
1. Before inference
2. Before tool execution
3. After tool execution

*(Không chỉ kiểm tra một lần ở đầu).*

---

## 12. Phase 5.11 — Cancellation

Propagation flow khi cancellation xảy ra:

```text
HTTP cancel
      ↓
AgentExecutionContext.cancel()
      ↓
Inference cancellation
      ↓
Tool cancellation
      ↓
MCP cancellation
```

Đặc biệt: Passing `CapabilityExecutionContext.cancellation_event` để tool driver/MCP biết execution đã bị hủy.

---

## 13. Phase 5.12 — Persistence

Agent Runtime nên persist các entity:
- `AgentExecution`
- `AgentIteration`
- `AgentToolCall`
- `AgentToolResult`

**Đề xuất bảng DB:**
- `agent_executions`
- `agent_iterations`
- `agent_tool_calls`
- `agent_tool_results`

**Mô hình phân tầng một Execution:**

```text
exec_001
 ├── iteration_001
 │    ├── inference request
 │    └── tool call
 │
 ├── iteration_002
 │    ├── tool result
 │    └── inference request
 │
 └── iteration_003
      └── final response
```

---

## 14. Phase 5.13 — Event Model

Event vocabulary riêng:

- `agent.execution.created` / `agent.execution.started`
- `agent.iteration.started` / `agent.iteration.completed`
- `agent.inference.requested` / `agent.inference.completed`
- `agent.tool.requested` / `agent.tool.started` / `agent.tool.completed` / `agent.tool.failed`
- `agent.execution.completed` / `agent.execution.failed` / `agent.execution.cancelled` / `agent.execution.timeout`

**Correlation Chain:**

```text
request_id
  ↓
execution_id
  ↓
iteration_id
  ↓
tool_call_id
  ↓
invocation_id
```

---

## 15. Phase 5.14 — Context Integration

Agent Runtime không nên tự build prompt.

```text
AgentRuntime
      ↓
ContextRuntime.build()
      ↓
ContextSnapshot
```

`ContextSnapshot` chứa:
- System instruction & Agent instruction
- Conversation & Memory
- Retrieved data
- Tool definitions & Tool results
- Runtime metadata

Sau đó: `ContextSnapshot` → `InferenceRequest`.

---

## 16. Phase 5.15 — Tool Visibility Policy

Cần phân biệt rõ ràng các trạng thái của Tool trong Production:
- **Registered**
- **Enabled**
- **Authorized**
- **Visible**
- **Executable**

**Cấu trúc Policy (`AgentToolPolicy`):**

```text
Agent
 ├── requested tools
 ├── allowed tools
 ├── denied tools
 └── dynamically available tools
```

> LLM chỉ được thấy các tool thỏa mãn: `allowed + executable`. Không public toàn bộ `CapabilityRegistry`.

---

## 17. Phase 5.16 — Agent × MCP

Nối MCP sau local Python tool:

```text
Agent
  ↓
github:search
  ↓
CapabilityRuntime
  ↓
McpCapabilityDriver
  ↓
MCP session
```

Tái sử dụng `McpCapabilityDriver`, `CapabilityExecutionContext` và `McpSessionProvider` đã có trong repo.

---

## 18. Phase 5.17 — Agent × Provider

Tích hợp E2E hoàn chỉnh:

```text
AgentRuntime
      ↓
InferencePort
      ↓
ProviderRuntime
      ↓
Gemini / OpenAI
      ↓
tool call
      ↓
CapabilityRuntime
      ↓
MCP / Python
      ↓
tool result
      ↓
ProviderRuntime
      ↓
final answer
```

---

## 19. Test Pyramid cho Phase 5

- **Level 1 — Unit:**
  - `test_agent_state_machine`
  - `test_tool_call_parser`
  - `test_tool_result_formatter`
  - `test_execution_limits`
  - `test_tool_policy`
  - `test_argument_validation`
- **Level 2 — Runtime integration:** `AgentRuntime` + `FakeLLM` + `CapabilityRuntime` + `FakeTool`
- **Level 3 — MCP integration:** `AgentRuntime` + `FakeLLM` + `CapabilityRuntime` + `McpCapabilityDriver` + `FakeMCP`
- **Level 4 — Provider integration:** `AgentRuntime` + `MockProvider` + `CapabilityRuntime`
- **Level 5 — Live:** `AgentRuntime` + `Gemini` + real MCP

---

## 20. Scenario Test bắt buộc (Acceptance Suite)

- **A01** single final answer
- **A02** one tool
- **A03** multiple sequential tools
- **A04** parallel tools
- **A05** tool validation failure
- **A06** tool permission denied
- **A07** tool not found
- **A08** tool timeout
- **A09** retry then success
- **A10** retry exhausted
- **A11** MCP unavailable
- **A12** max iterations
- **A13** max tool calls
- **A14** execution timeout
- **A15** cancellation
- **A16** final answer after tool result
- **A17** malformed LLM tool call
- **A18** duplicate tool call
- **A19** tool result ordering
- **A20** provider failure
- **A21** provider fallback
- **A22** context rebuild after tool
- **A23** persisted execution resume
- **A24** concurrent agents
- **A25** multi-agent delegation

---

## 21. Triển khai Supervisor sau Single Agent

Lộ trình khuyến nghị:

```text
                 Phase 5
                   │
          Single Agent Runtime
                   │
          ┌────────┴────────┐
          │                 │
       Tool loop         Persistence
          │
          ▼
      MCP / Tools
          │
          ▼
   Multi-step execution
          │
          ▼
      Phase 6
   Multi-Agent Runtime
          │
     ┌────┼─────┐
     │    │     │
 planner worker reviewer
     │    │     │
     └────┼─────┘
          ▼
      Phase 7
     Supervisor
```

---

## 22. Phase 6 — Multi-Agent Execution

Kết nối `MultiAgentCoordinator` vào `AgentRuntime`:

```text
MultiAgentCoordinator
        ↓
AgentExecutionRequest
        ↓
AgentRuntime
        ↓
Agent task
```

**Delegation Flow:**

```text
Planner
  ↓
create_task(worker)
  ↓
worker AgentRuntime
  ↓
result
  ↓
Planner
```

> **Nguyên tắc:** Không cho coordinator tự chạy inference.

---

## 23. Phase 7 — Supervisor Runtime

```text
SupervisorRuntime
├── SequentialStrategy
├── ParallelStrategy
├── DelegationStrategy
├── ReviewAndRetryStrategy
└── ConsensusStrategy
```

Mỗi strategy chỉ orchestrate `AgentExecution`, không chứa provider/tool implementation.

---

## 24. Phase 8 — Durable Scheduler

```text
Agent Runtime
      ↓
Execution Scheduler
      ↓
Queue
      ↓
Worker
```

Giải quyết các bài toán: multi-instance, distributed workers, durable queue, retry after crash, resume execution.

---

## 25. Phạn vi Chốt Phase 5 MVP

**Phase 5 MVP = Single Agent Execution Loop**

```text
AgentDefinition
       ↓
AgentExecutionRequest
       ↓
AgentRuntime
       ↓
ContextRuntime
       ↓
InferencePort
       ↓
ProviderRuntime
       ↓
ToolCall?
   ┌───┴───┐
   NO      YES
   │        │
   ▼        ▼
FINAL   CapabilityRuntime
           ↓
        ToolResult
           ↓
       next iteration
```

**Checklist Acceptance:**
- [x] Deterministic FakeLLM
- [x] Real CapabilityRuntime
- [x] Python capability
- [x] MCP capability
- [x] Permission
- [x] Timeout
- [x] Cancellation
- [x] Retry
- [x] Parallel tool calls
- [x] Max iterations
- [x] Max tool calls
- [x] Execution persistence
- [x] Event trace
- [x] Provider abstraction
- [x] Context rebuild

---

## Roadmap tổng thể

```text
Phase 4          CONTROL PLANE
────────────────────────────────────
AgentRegistry
Session
Message
Task
Execution envelope
Multi-agent coordination
        │
        ▼
Phase 4.1        TEST FOUNDATION
────────────────────────────────────
FakeLLM
FakeTool
FakeMCP
ExecutionTrace
Tool-loop tests
        │
        ▼
Phase 5.0        CONTRACT FREEZE
────────────────────────────────────
AgentExecutionContext
InferencePort
ToolExecution contract
Policy contracts
        │
        ▼
Phase 5.1        SINGLE AGENT RUNTIME
────────────────────────────────────
State machine
Context
Inference
Tool loop
Limits
Cancellation
        │
        ▼
Phase 5.2        TOOL PLATFORM
────────────────────────────────────
Validation
Retry
Parallel tools
MCP
Authorization
Observability
        │
        ▼
Phase 5.3        DURABLE EXECUTION
────────────────────────────────────
Iterations
Tool calls
Tool results
Resume
        │
        ▼
Phase 5.4        REAL E2E
────────────────────────────────────
Agent
  ↓
Gemini/OpenAI/Ollama
  ↓
Capability
  ↓
MCP/Python
  ↓
final answer
        │
        ▼
Phase 6          MULTI-AGENT RUNTIME
────────────────────────────────────
Delegation
Parallel agents
Fan-in/out
        │
        ▼
Phase 7          SUPERVISOR
────────────────────────────────────
Planner
Reviewer
Consensus
Retry-and-review
        │
        ▼
Phase 8          DISTRIBUTED AGENT PLATFORM
────────────────────────────────────
Scheduler
Queue
Workers
Resume
Multi-instance
```

---

> **Quyết định kiến trúc cốt lõi:**
> - `AgentRuntime` = Execution authority của agent
> - `MultiAgentCoordinator` = Control/Orchestration authority
> - `ProviderRuntime` = Inference authority
> - `CapabilityRuntime` = Tool execution authority
>
> **Bốn authority này tuyệt đối không được gộp chung thành một runtime monolith.**