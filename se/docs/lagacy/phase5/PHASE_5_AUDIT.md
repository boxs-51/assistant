# Báo Cáo Kiến Trúc Phase 5 — Agent Runtime Execution Platform & Freeze Contracts

> **Baseline Audit:** Commit `32d3445f180d19562015e2971dee32dd61b89fb4`  
> **Mục tiêu:** Định nghĩa toàn bộ hệ thống contract cốt lõi (Phase 5.0) trước khi hiện thực `AgentRuntime` nhằm chống hiện tượng Coupling & God Class.

---

## Kiến trúc tổng thể & Ranh giới trách nhiệm (Authority Boundaries)

```text
                  AgentRuntime (Execution Authority)
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
 ContextBuilder   InferencePort   ToolExecutionPort
        │              │              │
        ▼              ▼              ▼
 ContextRuntime   ProviderRuntime CapabilityRuntime
                                       │
                              ┌────────┴────────┐
                              ▼                 ▼
                         Python Driver      MCP Driver
```

### 4 Ranh giới chính (Architectural Authorities)
- **`MultiAgentCoordinator`** *(Control Plane / Task Plane)*: Điều phối tác vụ, routing, phân công agent và quản lý session state.
- **`AgentRuntime`** *(Execution Plane / Inference Loop)*: Chạy vòng lặp suy luận (`Inference`), quản lý `AgentLoopState`, giới hạn tài nguyên (`Limits`), và điều phối việc gọi tool.
- **`ProviderRuntime`** *(Inference Authority)*: Nhận request trừu tượng (`InferenceRequest`), định tuyến và gọi các LLM Provider (OpenAI, Gemini, Ollama...).
- **`CapabilityRuntime`** *(Tool Execution Authority)*: Thực thi các Tool/Capability (Python local function, MCP Server), quản lý Security/Permission và Sandbox.

---

## 1. Phase 5.0 — Cấu trúc Thư mục Contract Freeze

Tất cả các contract được đóng băng tại thư mục `src/runtimes/agent/contracts/`:

```text
src/runtimes/agent/contracts/
├── __init__.py
├── agent_execution_context.py
├── agent_loop.py
├── inference.py
├── tool_execution.py
├── context.py
├── policy.py
└── events.py
```

---

## 2. Chi tiết hệ thống Contract Specs

### 2.1 Context Spec (`agent_execution_context.py`)

`AgentExecutionContext` chứa toàn bộ state và snapshot tài nguyên của agent trong quá trình thực thi.

```python
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ExecutionUsage(BaseModel):

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_invocations: int = 0
    estimated_cost_usd: float = 0.0


class AgentExecutionContext(BaseModel):

    execution_id: str
    session_id: str
    agent_id: str
    request_id: str
    task_id: Optional[str] = None
    parent_execution_id: Optional[str] = None
    
    # State tracking
    current_iteration: int = 0
    tool_call_count: int = 0
    
    # Deadlines and Cancellation
    deadline: Optional[datetime] = None
    cancelled: bool = False
    
    # Financial & Resource usage
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage)
    
    # Traceability
    correlation_id: str
    causation_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

---

### 2.2 Loop & Persistence Spec (`agent_loop.py`)

Tách biệt hoàn toàn `AgentExecutionState` (persisted execution lifecycle) và `AgentLoopState` (internal runtime mechanics).

```python
from enum import Enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentExecutionState(str, Enum):
    """Persisted coarse-grained lifecycle state."""
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_AGENT = "WAITING_AGENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class AgentLoopState(str, Enum):
    """Internal fine-grained loop state for AgentRuntime."""
    PREPARING = "PREPARING"
    THINKING = "THINKING"
    TOOL_CALLING = "TOOL_CALLING"
    WAITING_TOOL = "WAITING_TOOL"
    FINALIZING = "FINALIZING"


class AgentIteration(BaseModel):

    execution_id: str
    iteration_index: int
    state: AgentLoopState
    started_at: datetime
    completed_at: Optional[datetime] = None
    inference_request_id: Optional[str] = None
    inference_response_id: Optional[str] = None
    tool_call_ids: List[str] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
```

---

### 2.3 Inference Port Spec (`inference.py`)

Loại bỏ sự phụ thuộc trực tiếp vào HTTP/Provider DTOs (`GatewayResponse`). Tất cả thao tác LLM phải đi qua `InferencePort`.

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class InferenceToolCall(BaseModel):

    id: str
    name: str
    arguments: Dict[str, Any]


class InferenceMessage(BaseModel):

    role: str  # "system", "user", "assistant", "tool"
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[InferenceToolCall]] = None


class InferenceRequest(BaseModel):

    execution_id: str
    iteration: int
    messages: List[InferenceMessage]
    tools: Optional[List[Dict[str, Any]]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    model_hint: Optional[str] = None
    timeout_seconds: Optional[float] = None


class InferenceResponse(BaseModel):

    request_id: str
    message: InferenceMessage
    finish_reason: str  # "stop", "tool_calls", "length", "content_filter"
    usage: Dict[str, int] = Field(default_factory=dict)
    provider: str
    model: str
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class InferencePort(ABC):

    @abstractmethod
    async def complete(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        """Executes LLM inference via ProviderRuntime."""
        pass
```

---

### 2.4 Tool Execution Spec (`tool_execution.py`)

Định nghĩa interface cho `ToolExecutionPort` để bọc `CapabilityRuntime`, đảm bảo thứ tự kết quả đồng bộ với thứ tự gọi tool.

```python
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ToolExecutionRequest(BaseModel):

    tool_call_id: str
    capability_id: str
    arguments: Dict[str, Any]
    identity_context: Dict[str, Any]
    execution_id: str
    session_id: str
    timeout_seconds: Optional[float] = None


class ToolExecutionResult(BaseModel):

    tool_call_id: str
    capability_id: str
    success: bool
    output: Any
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = False
    duration_ms: float
    executed_at: datetime = Field(default_factory=datetime.utcnow)


class ToolExecutionBatchResult(BaseModel):

    results: List[ToolExecutionResult]
    
    def get_ordered_results(self) -> List[ToolExecutionResult]:
        """Ensures outputs maintain exact order of initial LLM tool calls."""
        return self.results


class ToolExecutionPort(ABC):

    @abstractmethod
    async def execute_tool(
        self,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        """Executes a single capability tool call."""
        pass

    @abstractmethod
    async def execute_batch(
        self,
        requests: List[ToolExecutionRequest],
        max_parallel: int = 4,
    ) -> ToolExecutionBatchResult:
        """Executes multiple tool calls with bounded concurrency."""
        pass
```

---

### 2.5 Agent Context Builder Spec (`context.py`)

Loại bỏ việc `AgentRuntime` thao tác trực tiếp trên DB hoặc `ContextEngine`.

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .inference import InferenceMessage


class AgentContextSnapshot(BaseModel):
    """Immutable snapshot of the context for a single iteration."""
    execution_id: str
    iteration: int
    system_instruction: str
    agent_goal: str
    messages: List[InferenceMessage]
    available_tools: List[Dict[str, Any]]
    token_estimate: int
    created_at: str


class AgentContextRequest(BaseModel):

    execution_id: str
    session_id: str
    agent_id: str
    iteration: int
    latest_tool_results: Optional[List[Any]] = None


class ContextBuilderPort(ABC):

    @abstractmethod
    async def build_snapshot(
        self,
        request: AgentContextRequest,
    ) -> AgentContextSnapshot:
        """Constructs an immutable snapshot for inference consumption."""
        pass
```

---

### 2.6 Policy Spec (`policy.py`)

Quản lý hạn mức thực thi, phân quyền tool và chính sách retry.

```python
from typing import List, Optional
from pydantic import BaseModel, Field


class RetryDecision(BaseModel):

    retry: bool
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    reason: str


class ToolRetryPolicy(BaseModel):

    max_retries: int = 3
    retryable_error_codes: List[str] = Field(
        default_factory=lambda: [
            "CAPABILITY_TIMEOUT",
            "CAPABILITY_NETWORK_ERROR",
            "MCP_UNAVAILABLE",
            "RATE_LIMIT_EXCEEDED",
        ]
    )

    def should_retry(self, error_code: str, current_attempt: int) -> RetryDecision:
        if error_code in self.retryable_error_codes and current_attempt < self.max_retries:
            return RetryDecision(
                retry=True,
                max_attempts=self.max_retries,
                backoff_seconds=2.0 ** current_attempt,
                reason=f"Error {error_code} is retryable.",
            )
        return RetryDecision(retry=False, reason=f"Error {error_code} is non-retryable.")


class AgentCapabilityPolicy(BaseModel):

    allowed_capabilities: List[str] = Field(default_factory=list)
    denied_capabilities: List[str] = Field(default_factory=list)
    require_explicit_authorization: bool = True


class AgentExecutionPolicy(BaseModel):

    max_iterations: int = 8
    max_tool_calls: int = 16
    max_parallel_tools: int = 4
    agent_timeout_seconds: float = 60.0
    iteration_timeout_seconds: float = 20.0
    inference_timeout_seconds: float = 15.0
    tool_timeout_seconds: float = 10.0
```

---

### 2.7 Observability Events Spec (`events.py`)

Domain Events thuộc namespace `agent.*` nhằm tránh nảy sinh phụ thuộc vào Provider/Capability events.

```python
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class AgentBaseEvent(BaseModel):

    event_id: str
    execution_id: str
    session_id: str
    correlation_id: str
    causation_id: Optional[str] = None
    timestamp: str


class AgentExecutionStarted(AgentBaseEvent):

    agent_id: str


class AgentIterationStarted(AgentBaseEvent):

    iteration: int


class InferenceRequested(AgentBaseEvent):

    iteration: int
    model_hint: Optional[str] = None


class InferenceCompleted(AgentBaseEvent):

    iteration: int
    provider: str
    model: str
    finish_reason: str


class ToolRequested(AgentBaseEvent):

    tool_call_id: str
    capability_id: str


class ToolStarted(AgentBaseEvent):

    tool_call_id: str
    capability_id: str


class ToolCompleted(AgentBaseEvent):

    tool_call_id: str
    capability_id: str
    duration_ms: float


class ToolFailed(AgentBaseEvent):

    tool_call_id: str
    capability_id: str
    error_code: str
    error_message: str


class AgentExecutionCompleted(AgentBaseEvent):

    status: str = "COMPLETED"


class AgentExecutionFailed(AgentBaseEvent):

    error_code: str
    error_message: str
```

---

## 3. Checklist Freeze & Đánh Giá Mức Độ Ưu Tiên

| Contract | Priority | Trạng thái | Ghi chú |
|---|---|---|---|
| `AgentExecutionContext` | **P0** | Frozen | Cung cấp state snapshot cho runtime |
| `AgentLoopState` vs `AgentExecutionState` | **P0** | Frozen | Tách bạch Persistence vs Loop Mechanics |
| `InferencePort` & Request/Response DTOs | **P0** | Frozen | Che giấu `ProviderRuntime` & OpenAI DTOs |
| `ToolExecutionPort` | **P0** | Frozen | Interface trung gian gọi `CapabilityRuntime` |
| `ContextBuilderPort` & Snapshot | **P0** | Frozen | Tạo Immutable snapshot từng iteration |
| `AgentCapabilityPolicy` | **P0** | Frozen | Lớp security phân quyền visibility tool |
| `AgentExecutionPolicy` | **P0** | Frozen | Quản lý Hạn mức và Hierarchy Deadlines |
| Event Schema (`agent.*`) | **P0** | Frozen | Phục vụ Observability & Tracing độc lập |
| Correlation & Causation Chain | **P0** | Frozen | Gắn kết `execution` -> `iteration` -> `tool_call` |
| `ToolRetryPolicy` | **P1** | Frozen | Phân định Retryable vs Non-retryable Errors |
| Parallel Concurrency Contract | **P1** | Frozen | Đảm bảo `ordered_results == input_calls` |
| Cancellation Propagation | **P1** | Frozen | Propagation từ Http Request đến MCP Session |

---

## 4. Lộ Trình Triển Khai Thực Thi (Adjusted Phase 5+ Roadmap)

```text
Phase 5.0  [FREEZE CONTRACTS] (Completed Spec)
    │
    ▼
Phase 5.1  AgentExecutionContext + Loop State Machine implementation
    │
    ▼
Phase 5.2  InferencePort Adapter (Wrap ProviderRuntime)
    │
    ▼
Phase 5.3  ContextBuilderPort Adapter (Wrap ContextEngine)
    │
    ▼
Phase 5.4  ToolExecutionPort Adapter (Wrap CapabilityRuntime)
    │
    ▼
Phase 5.5  AgentRuntime Core Loop (Fake LLM & Mock Tools)
    │
    ▼
Phase 5.6  Validation & Authorization Enforcement
    │
    ▼
Phase 5.7  Retry, Timeout Hierarchy & Cancellation Event Linkage
    │
    ▼
Phase 5.8  Bounded Concurrency Parallel Tool Execution
    │
    ▼
Phase 5.9  Persistence (Iteration & Tool Call DB Schemas)
    │
    ▼
Phase 5.10 Real E2E Integration (Gemini / OpenAI + Local & MCP Tools)
    │
    ▼
Phase 6    Multi-Agent Execution
    │
    ▼
Phase 7    Supervisor Runtime
    │
    ▼
Phase 8    Distributed Scheduler & Workers
```

---

## 5. Kết luận Kiến trúc

1. **Hoàn toàn độc lập Transport Layer**: `AgentRuntime` làm việc trên `InferenceResponse`, loại bỏ `GatewayResponse` hay bất cứ HTTP DTO nào.
2. **Loại bỏ Direct Coupling**: Không inject `ProviderRuntime`, `CapabilityRegistry` hay `ContextEngine` trực tiếp vào runtime. Mọi tương tác bắt buộc đi qua Port (`InferencePort`, `ToolExecutionPort`, `ContextBuilderPort`).
3. **EventBus dùng đúng mục đích**: EventBus chỉ còn đảm nhận vai trò **Observability & Trace Domain Events** (`agent.*`), các tương tác suy luận và gọi tool chuyển sang **Async Typed Ports (RPC-style await)** để bảo tồn Trace, Error propagation và Cancellation context.