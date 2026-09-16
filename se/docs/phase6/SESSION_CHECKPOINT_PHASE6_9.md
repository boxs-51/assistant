# SESSION CHECKPOINT — PHASE 6.9 P0-1A → P0-1G

> Repo: `https://github.com/boxs-51/assistant`  
> Commit mục tiêu hiện tại: `37b3cc99ac069fcf4d1d5c9eae4c18c1ccb3c0fe`  
> Scope hiện tại: **Phase 6.9 only / chưa chuyển sang Phase 6.10**

---

## 1. Mục tiêu tổng thể của phiên hiện tại

Hoàn thiện **Phase 6.9** cho kiến trúc Agent Runtime ↔ Capability Runtime ↔ Remote Client qua **real WebSocket**, đảm bảo:

```text
AgentRuntime
  → ToolExecutionPort
  → CapabilityRuntime
  → CapabilityRoutingPolicy
  → RemoteClientDriver
  → RealtimeMultiplexer
  → real TCP WebSocket
  → ClientRuntime receiver
  → CapabilityDispatcher
  → local TOOL / capability
  → capability.result
  → RealtimeMultiplexer
  → AgentRuntime
  → second inference
  → final answer
```

Mục tiêu cuối của Phase 6.9 là chứng minh luồng trên chạy thực tế end-to-end, đồng thời xử lý đúng disconnect, checkpoint, continuation, reconnect branch và late-result/race.

---

# 2. YÊU CẦU ĐÃ CHỐT CỦA TÔI

## 2.1. Phạm vi commit

Audit sâu và viết patch trên commit:

```text
37b3cc99ac069fcf4d1d5c9eae4c18c1ccb3c0fe
```

Không dựa trên kiến trúc giả định nếu source hiện tại khác.

---

## 2.2. Phase hiện tại

Chỉ làm:

```text
Phase 6.9
P0-1A → P0-1G
```

**Không triển khai Phase 6.10 trong phiên này.**

Phase 6.10 chỉ được bắt đầu khi Phase 6.9 Exit Gate thực sự GREEN.

---

## 2.3. Real WebSocket E2E là bắt buộc

Canonical E2E test phải dùng:

- real FastAPI Gateway
- real `uvicorn`
- real TCP WebSocket
- real `ClientRuntime`
- real `GatewayRealtimeClient.receiver_loop`
- real `CapabilityDispatcher`
- real local capability/tool implementation
- real `capability.result` đi ngược qua WebSocket
- real AgentRuntime tool loop
- second inference thật trong runtime loop

Được phép dùng deterministic/fake inference model để kiểm thử Agent loop.

### Tuyệt đối không dùng trong canonical E2E

```text
FakeSocket
manual realtime.handle_inbound(...)
direct CapabilityDispatcher.execute(...)
manual capability.result injection
```

Fake transport có thể dùng trong unit test nội bộ, nhưng **không được thay thế canonical network E2E**.

---

# 3. SEMANTICS connection_id ĐÃ CHỐT

Một thay đổi kiến trúc quan trọng trong phiên hiện tại:

## 3.1. `connection_id` KHÔNG còn khóa toàn bộ Agent execution

`connection_id` phải được hiểu là:

> **hard affinity cho một remote invocation đã được phát hành**, không phải hard lock cho toàn bộ Agent execution.

Ví dụ:

```text
Invocation I1 được gửi qua connection C1
```

thì:

```text
I1 → C1
```

là immutable.

Không được:

```text
I1 → C1 disconnect
I1 → retry/migrate sang C2
```

vì side effect trên C1 có thể đã xảy ra nhưng result bị mất.

---

## 3.2. Foreign client không được thay thế silent

Nếu Agent trước đó dùng client C1:

```text
C1 disconnect
C2 online
```

thì hệ thống không được tự động lấy implementation trên C2 để hoàn tất invocation đang chạy trên C1.

---

## 3.3. Server fallback chỉ dành cho NEW routing decision

Nếu C1 disconnect:

- invocation cũ đã gửi C1 không được migrate;
- nhưng **routing decision mới** sau đó có thể dùng `SERVER` implementation nếu capability đó có server implementation phù hợp.

Nghĩa là:

```text
old remote invocation
    = hard bound C1

new capability invocation
    = có thể chọn SERVER
      nếu routing policy cho phép
```

---

# 4. DISCONNECT SEMANTICS ĐÃ CHỐT

Khi WebSocket client disconnect:

## 4.1. Gateway phải phát hiện disconnect

Connection phải được đánh dấu inactive trong:

```text
ConnectionRegistry
```

## 4.2. Pending remote invocation phải fail deterministic

Ví dụ:

```text
invocation_id = inv_123
connection_id = C1
```

C1 disconnect thì pending future phải hoàn tất bằng lỗi kiểu:

```python
RemoteConnectionLost(
    connection_id="C1",
    invocation_id="inv_123",
)
```

Không được treo future.

## 4.3. Late result không được resurrect invocation

Race bắt buộc phải test:

```text
1. invoke capability qua C1
2. C1 disconnect
3. pending invocation fail
4. capability.result từ C1 tới muộn
5. late result phải bị reject/ignore
6. invocation cũ không được resurrect
7. result không được chuyển sang C2
```

---

# 5. AGENT CHECKPOINT SEMANTICS

Khi disconnect xảy ra, Agent không mặc định terminal-fail toàn bộ execution.

Agent phải tạo checkpoint.

Checkpoint cần lưu tối thiểu:

```text
checkpoint_id
execution_id
session_id
reason
continuation_state
parent_checkpoint_id
old connection_id
pending invocation_id
pending tool_call_id
pending capability_id
iteration
transcript
metadata
created_at
```

Có thể persist trong JSON field hiện có như:

```text
context_state
```

nếu model SQL hiện tại hỗ trợ mà không cần migration DB.

---

# 6. HAI TRƯỜNG HỢP SAU DISCONNECT

## 6.1. Agent có thể tiếp tục server-side

Nếu Agent không còn phụ thuộc vào remote invocation đang mất connection, hoặc có một **new routing decision** có thể chạy server-side:

```text
disconnect
→ checkpoint
→ continue Agent automatically
```

**Không cần user approval.**

## 6.2. Agent vẫn phụ thuộc remote connection

Nếu execution đang cần capability remote và không thể tiếp tục an toàn:

```text
disconnect
→ checkpoint
→ WAITING_FOR_CONNECTION
```

Không được tự động retry remote invocation cũ.

---

# 7. CONTINUATION STATE ĐỀ XUẤT

Không nên làm bẩn `AgentLoopState` bằng lifecycle reconnect.

Dùng một state riêng:

```python
class ContinuationState(str, Enum):
    RUNNING = "RUNNING"
    WAITING_FOR_CONNECTION = "WAITING_FOR_CONNECTION"
    READY_TO_MERGE = "READY_TO_MERGE"
```

Checkpoint reason:

```python
class CheckpointReason(str, Enum):
    ITERATION = "ITERATION"
    CONNECTION_DISCONNECTED = "CONNECTION_DISCONNECTED"
    WAITING_FOR_CONNECTION = "WAITING_FOR_CONNECTION"
    RECONNECTED = "RECONNECTED"
    READY_TO_MERGE = "READY_TO_MERGE"
```

---

# 8. EXECUTION CHECKPOINT ĐỀ XUẤT

```python
@dataclass(frozen=True)
class ExecutionCheckpoint:
    checkpoint_id: str
    execution_id: str
    session_id: str
    reason: CheckpointReason
    state: ContinuationState

    parent_checkpoint_id: str | None = None

    connection_id: str | None = None

    pending_invocation_id: str | None = None
    pending_tool_call_id: str | None = None
    pending_capability_id: str | None = None

    iteration: int = 0

    transcript: tuple[dict[str, Any], ...] = ()

    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: datetime = ...
```

---

# 9. ORIGIN CONNECTION VS CURRENT CONNECTION

Cần tránh conflation giữa:

```text
origin connection
```

và:

```text
current active connection
```

Đề xuất:

```text
origin_connection_id
current_connection_id
```

Semantics:

```text
origin_connection_id
    = connection lịch sử của execution/checkpoint

current_connection_id
    = connection hiện tại có thể dùng cho routing mới
```

Sau disconnect:

```text
origin_connection_id = C1
current_connection_id = None
```

Khi reconnect bằng C2:

```text
origin_connection_id = C1
current_connection_id = C2
```

Không được giả vờ C2 chính là C1.

---

# 10. RECONNECT SEMANTICS

Reconnect phải tạo **connection identity mới**.

Ví dụ:

```text
old: C1
new: C2
```

Không reuse C1.

## 10.1. Khi nào tạo continuation branch

Không phải reconnect nào cũng tạo branch.

Chỉ khi execution có persisted checkpoint:

```text
WAITING_FOR_CONNECTION
```

và user/session tiếp tục execution bằng connection mới.

## 10.2. Continuation Branch

Đề xuất:

```python
@dataclass(frozen=True)
class ContinuationBranch:
    branch_id: str
    execution_id: str
    base_checkpoint_id: str
    connection_id: str
    state: ContinuationState
    metadata: dict[str, Any]
```

Branch phải:

```text
reference disconnect checkpoint
use new connection C2
không mutate main execution ngay
```

---

# 11. USER-CONFIRMED MERGE

Continuation branch không được tự merge vào main execution.

Cần API/service semantic dạng:

```python
confirm_merge(
    execution_id: str,
    branch_id: str,
    user_id: str,
)
```

Merge phải kiểm tra:

```text
branch.base_checkpoint_id
==
current main execution checkpoint
```

Nếu không:

```text
409 / stale continuation branch
```

## 11.1. Merge requirements

- explicit user confirmation
- user identity required
- branch isolation trước merge
- optimistic concurrency
- stale branch không được overwrite main
- merge idempotent
- successful merge tạo checkpoint mới

---

# 12. ROUTING POLICY CẦN SỬA

Current routing semantics trước đây quá hard:

```text
context.connection_id tồn tại
→ chỉ CLIENT đúng connection
→ SERVER/MCP bị loại
```

Semantics mới phải là:

### Nếu exact client còn active

Ưu tiên:

```text
CLIENT implementation trên exact connection
```

### Foreign client

Luôn exclude.

### Nếu exact client đã unavailable

Một **new routing decision** có thể dùng:

```text
SERVER
```

nếu có implementation phù hợp.

Nhưng:

```text
already-issued remote invocation
```

vẫn hard bound vào old connection.

---

# 13. BUG ĐÃ PHÁT HIỆN CẦN FIX

Trong:

```text
CapabilityToolExecutionAdapter.new_request()
```

request helper đang thiếu propagation:

```python
connection_id=context.connection_id
```

Cần sửa thành:

```python
return ToolExecutionRequest(
    execution_id=context.execution_id,
    iteration=iteration,
    invocation_id=f"inv_{uuid.uuid4().hex}",
    tool_call_id=tool_call_id,
    capability_id=capability_id,
    connection_id=context.connection_id,
    arguments=dict(arguments),
    metadata=dict(metadata or {}),
)
```

---

# 14. ERROR CONTRACT CHO DISCONNECT

Đề xuất:

```python
class RemoteConnectionLost(ConnectionError):
    def __init__(
        self,
        connection_id: str,
        invocation_id: str,
    ) -> None:
        self.connection_id = connection_id
        self.invocation_id = invocation_id

        super().__init__(
            f"Remote connection '{connection_id}' lost "
            f"while invocation '{invocation_id}' was pending."
        )
```

`ConnectionMultiplexer.fail_connection()` phải fail từng invocation với identity tương ứng.

---

# 15. CURRENT AUDIT STATUS

Audit hiện tại cho commit `37b3cc99` cho thấy Phase 6.9 **chưa complete**.

Trạng thái hiện tại:

```text
Phase 6.9 = RED
```

Các phần đã có tương đối tốt:

- `AgentRuntime` tool loop cơ bản
- `CapabilityRuntime`
- `CapabilityRoutingPolicy`
- `RemoteClientDriver`
- `RealtimeMultiplexer`
- connection registry
- WebSocket transport
- Client receiver loop
- Client capability dispatcher
- capability result correlation

Nhưng vẫn thiếu P0 quan trọng:

- canonical real TCP WebSocket E2E
- disconnect checkpoint
- server continuation semantics
- `WAITING_FOR_CONNECTION`
- reconnect continuation branch
- explicit user merge
- deterministic race/disconnect/late-result coverage
- Exit Gate cập nhật theo semantics mới

---

# 16. P0-1A → P0-1G KẾ HOẠCH HIỆN TẠI

## P0-1A — REAL TCP WEBSOCKET E2E

Viết canonical E2E:

```text
uvicorn
→ FastAPI Gateway
→ real TCP socket
→ ClientRuntime
→ receiver loop
→ capability.register
→ AgentRuntime
→ RemoteClientDriver
→ WebSocket
→ CapabilityDispatcher
→ real local capability
→ capability.result
→ AgentRuntime
→ second inference
→ final completion
```

Assertions:

- client tool thực sự được gọi
- same `execution_id`
- same `invocation_id`
- same `tool_call_id`
- same `capability_id`
- correct `connection_id`
- Agent chạy second inference
- final answer success

---

## P0-1B — IDENTITY / CORRELATION

Bảo đảm propagation:

```text
AgentExecutionContext.connection_id
→ ToolExecutionRequest.connection_id
→ CapabilityExecutionContext.connection_id
→ selected implementation.connection_id
→ WebSocket envelope.connection_id
```

Với remote invocation đã phát hành:

```text
connection_id immutable
```

---

## P0-1C — REMOTE INVOCATION AFFINITY

Sửa routing semantics:

- same client preferred nếu active
- foreign clients excluded
- issued invocation không migrate
- disconnect invocation → terminal/unknown
- late result không resurrect
- SERVER fallback chỉ cho new routing decision

---

## P0-1D — DISCONNECT CHECKPOINT

Khi disconnect:

```text
connection inactive
→ fail pending invocations
→ create execution checkpoint
```

Checkpoint lưu:

- transcript
- iteration
- connection_id
- pending invocation
- capability
- tool_call
- state/reason

---

## P0-1E — SERVER CONTINUATION / WAITING

Nếu Agent tiếp tục server-side an toàn:

```text
continue automatically
```

Nếu vẫn cần remote connection:

```text
WAITING_FOR_CONNECTION
```

Không retry/migrate invocation cũ.

---

## P0-1F — RECONNECT BRANCH

Reconnect:

```text
C1 disconnected
→ C2 connected
```

Nếu execution đang WAITING:

```text
disconnect checkpoint
→ continuation branch
→ branch uses C2
→ main remains unchanged
```

---

## P0-1G — USER MERGE

Triển khai:

```text
confirm_merge(...)
```

với:

- user identity
- base checkpoint validation
- stale protection
- idempotency
- new checkpoint after successful merge

---

# 17. TEST MATRIX BẮT BUỘC

## Real WebSocket E2E

```text
test_phase6_9_real_websocket_agent_client_loop
```

Bắt buộc:

- uvicorn
- real TCP
- ClientRuntime
- actual receiver loop
- dispatcher
- local tool
- capability.result over socket
- second inference

## Disconnect while invocation pending

```text
test_disconnect_fails_pending_remote_invocation
```

Expected:

```text
RemoteConnectionLost
```

## Late result race

```text
test_late_result_after_disconnect_cannot_resurrect_invocation
```

Sequence:

```text
invoke C1
disconnect C1
pending fails
late result C1
result ignored/rejected
```

## Foreign client safety

```text
test_foreign_client_never_executes_old_invocation
```

## Server continuation

```text
test_agent_continues_server_side_after_client_disconnect
```

No user confirmation required.

## WAITING_FOR_CONNECTION

```text
test_agent_enters_waiting_for_connection_when_remote_required
```

## Reconnect branch

```text
test_reconnect_creates_isolated_continuation_branch
```

## Merge

```text
test_merge_requires_explicit_user_confirmation
test_stale_branch_cannot_overwrite_main
test_merge_is_idempotent
```

---

# 18. FILES / MODULES ĐANG LÀ TRỌNG TÂM

Server:

```text
se/src/runtimes/agent/contracts/context.py
se/src/runtimes/agent/contracts/loop.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/agent/adapters/tool.py

se/src/runtimes/capability/policy.py
se/src/runtimes/capability/runtime.py
se/src/runtimes/capability/drivers/remote_client_driver.py
se/src/runtimes/capability/contracts/definition.py

se/src/runtimes/connection/multiplexer.py
se/src/runtimes/connection/realtime.py
se/src/runtimes/connection/runtime.py

se/src/infrastructure/storage/models/sql/agent/execution.py

se/src/transport/gateway/api/v1/events_router.py

se/src/main.py
```

Client:

```text
cl/src/core/realtime_client.py
```

Tests:

```text
se/tests/e2e/test_phase6_9_agent_client_loop.py
se/tests/integration/test_phase6_9_client_tool_flow.py
se/tests/architecture/test_phase6_9_connection_invariant.py
se/tests/architecture/test_phase6_9_routing.py
```

Docs:

```text
se/docs/phase6/PHASE6_9.md
se/docs/phase6/PHASE6_9_EXIT_GATE.md
```

---

# 19. CURRENT IMPLEMENTATION OBSERVATIONS

## AgentRuntime

Agent loop cơ bản đã có đúng shape:

```text
inference #1
→ tool call
→ ToolExecutionRequest
→ tool execution
→ append tool result
→ inference #2
→ final result
```

## Capability Runtime

Bootstrap đã có wiring dạng:

```text
CapabilityRoutingPolicy(
    connection_availability=connection_runtime.registry
)
```

và inject vào `CapabilityRuntime`.

## Realtime Multiplexer

Đã có:

- pending invocation correlation
- connection_id correlation
- fail_connection
- disconnect cleanup foundation

Nhưng cần nâng semantics race/disconnect lên P0 deterministic.

## ClientRuntime

Đã có receiver loop thực tế.

Canonical E2E hiện tại chưa đi xuyên qua receiver loop bằng real network.

---

# 20. KHÔNG ĐƯỢC LÀM

Trong Phase 6.9 hiện tại:

- không chuyển sang Phase 6.10
- không FakeSocket cho canonical E2E
- không manual `handle_inbound`
- không direct dispatcher invocation trong canonical E2E
- không silent foreign-client fallback
- không auto migrate remote invocation
- không auto retry invocation có side-effect unknown
- không auto merge reconnect branch
- không reuse old connection identity

---

# 21. EXIT GATE PHASE 6.9

`PHASE6_9_EXIT_GATE.md` cần được cập nhật thành:

```markdown
# Phase 6.9 Exit Gate

Phase 6.9 MUST NOT be marked complete until every P0 item below passes.

## P0-A — Real WebSocket
- [ ] Real uvicorn Gateway is used.
- [ ] Real TCP WebSocket is used.
- [ ] ClientRuntime is used.
- [ ] GatewayRealtimeClient receiver loop is used.
- [ ] No FakeSocket in canonical E2E.
- [ ] No manual handle_inbound() in canonical E2E.
- [ ] No direct CapabilityDispatcher invocation in canonical E2E.
- [ ] Client capability executes through real dispatcher.
- [ ] capability.result travels through real WebSocket.
- [ ] Agent performs second inference.
- [ ] Agent completes successfully.

## P0-B — Identity / Correlation
- [ ] AgentExecutionContext.connection_id is explicit.
- [ ] ToolExecutionRequest.connection_id is explicit.
- [ ] CapabilityExecutionContext.connection_id is explicit.
- [ ] Remote implementation.connection_id matches request connection.
- [ ] WebSocket envelope.connection_id matches transport connection.
- [ ] execution_id remains stable.
- [ ] invocation_id remains stable.
- [ ] tool_call_id remains stable.
- [ ] Foreign connection can never execute a bound invocation.

## P0-C — Remote Invocation Affinity
- [ ] Active bound client implementation is preferred.
- [ ] Foreign client implementations are never selected.
- [ ] An already-issued remote invocation can never migrate.
- [ ] A disconnected invocation becomes terminal/unknown.
- [ ] Late result cannot resurrect a completed/failed invocation.
- [ ] Server fallback is allowed only for a NEW routing decision.
- [ ] Server fallback never steals an already-issued remote invocation.

## P0-D — Disconnect Checkpoint
- [ ] Gateway detects abnormal WebSocket disconnect.
- [ ] ConnectionRegistry becomes inactive.
- [ ] Pending invocations for the connection fail deterministically.
- [ ] Agent checkpoint is persisted.
- [ ] Checkpoint contains transcript.
- [ ] Checkpoint contains iteration.
- [ ] Checkpoint contains pending invocation identity.
- [ ] Checkpoint contains old connection identity.
- [ ] Checkpoint is immutable by logical identity.

## P0-E — Server Continuation
- [ ] Disconnect does not automatically terminate Agent execution.
- [ ] Server-only continuation may proceed without user approval.
- [ ] Remote-required execution enters WAITING_FOR_CONNECTION.
- [ ] No foreign client is silently substituted.
- [ ] No unsafe remote invocation retry occurs automatically.

## P0-F — Reconnect Branch
- [ ] Reconnect creates a new connection identity.
- [ ] Old connection identity is never reused.
- [ ] Reconnect produces a continuation checkpoint.
- [ ] Continuation is represented as a branch.
- [ ] Branch references the disconnect checkpoint.
- [ ] Branch uses the new connection.
- [ ] Main execution is not automatically mutated.

## P0-G — User Merge
- [ ] Branch remains isolated until explicit confirmation.
- [ ] Merge requires user identity.
- [ ] Merge verifies branch base checkpoint.
- [ ] Stale branches cannot overwrite newer execution state.
- [ ] Successful merge produces a new execution checkpoint.
- [ ] Merge is idempotent.

## Regression
- [ ] Phase 6.6 compatibility tests pass.
- [ ] Phase 6.8 resilience tests pass.
- [ ] Existing AgentRuntime tests pass.
- [ ] Existing capability routing tests pass.
- [ ] Existing connection correlation tests pass.

## Phase 6.9 completion rule
Phase 6.9 remains RED if any P0 item is unchecked.
Phase 6.10 MUST NOT start until this gate is GREEN.
```

---

# 22. TRẠNG THÁI KIỂM THỬ

Trong phiên trước chưa thể chạy local pytest trực tiếp trên repo vì môi trường container không clone được GitHub do DNS/network.

Do đó:

```text
KHÔNG được tuyên bố test pass
KHÔNG được đánh dấu Exit Gate GREEN
```

cho đến khi patch được apply và chạy thực tế trong repo/local CI.

---

# 23. MỤC TIÊU CUỐI CÙNG CỦA PHIÊN HIỆN TẠI

Kết quả cuối cần đạt:

```text
Phase 6.9 Complete
```

với các bằng chứng:

1. Real TCP WebSocket E2E thực sự pass.
2. ClientRuntime thực thi capability thật qua receiver loop.
3. Agent nhận tool result từ WebSocket.
4. Agent thực hiện second inference.
5. Disconnect pending invocation fail deterministic.
6. Late result không resurrect invocation.
7. Old remote invocation không migrate sang connection mới.
8. Server continuation chạy tự động nếu độc lập với remote client.
9. Remote-dependent Agent chuyển sang `WAITING_FOR_CONNECTION`.
10. Reconnect tạo continuation branch với connection mới.
11. Branch không mutate main execution.
12. Merge chỉ xảy ra khi user explicit confirm.
13. Stale branch bị reject.
14. Exit Gate Phase 6.9 được cập nhật.
15. Regression tests hiện có vẫn pass.
16. Chưa bắt đầu Phase 6.10 trước khi toàn bộ P0 GREEN.

---

# 24. PROMPT ĐỂ TIẾP TỤC Ở PHIÊN MỚI

```text
Tiếp tục từ SESSION CHECKPOINT Phase 6.9 trên repo
https://github.com/boxs-51/assistant
commit 37b3cc99ac069fcf4d1d5c9eae4c18c1ccb3c0fe.

Hãy audit lại source hiện tại trước khi viết code, sau đó triển khai patch hoàn chỉnh
Phase 6.9 P0-1A → P0-1G theo checkpoint:

1. Canonical real TCP WebSocket E2E dùng uvicorn + ClientRuntime,
   tuyệt đối không FakeSocket/manual handle_inbound/direct dispatcher.
2. Sửa connection_id semantics thành hard affinity chỉ cho already-issued remote invocation,
   không khóa toàn bộ Agent execution.
3. Disconnect phải fail pending invocation deterministic và tạo checkpoint.
4. Late result không được resurrect invocation.
5. Nếu Agent có thể tiếp tục server-side thì tiếp tục tự động không cần user approval.
6. Nếu vẫn phụ thuộc remote client thì vào WAITING_FOR_CONNECTION.
7. Reconnect tạo new connection_id và continuation branch từ disconnect checkpoint.
8. Main execution không được mutate tự động.
9. Merge branch chỉ khi user explicit confirm; có stale-base protection + idempotency.
10. Viết đầy đủ race/disconnect/late-result/reconnect/merge tests.
11. Cập nhật PHASE6_9_EXIT_GATE.md.
12. Không chuyển sang Phase 6.10 cho tới khi Phase 6.9 thực sự GREEN.

Bám chính xác API/call-site hiện tại của repo.
Nếu có divergence giữa checkpoint và source thực tế, ưu tiên source thực tế nhưng giữ đúng invariant kiến trúc.
Không được tuyên bố test pass nếu chưa chạy được.
```

---

# 25. GHI CHÚ CHO PHIÊN TIẾP THEO

Ưu tiên audit lại exact source trước khi viết unified diff, đặc biệt:

```text
CapabilityRoutingPolicy.select()
CapabilityToolExecutionAdapter.new_request()
RemoteClientDriver.execute()
ConnectionMultiplexer.fail_connection()
RealtimeMultiplexer inbound/result handling
AgentRuntime error/recovery path
DurableAgentStore checkpoint/resume path
ClientRuntime constructor / connection_id lifecycle
Gateway WebSocket route
existing Phase 6.9 tests
```

Mục tiêu là tạo patch **apply được trực tiếp** trên commit hiện tại, không chỉ dừng ở thiết kế.

---

## END OF SESSION CHECKPOINT
