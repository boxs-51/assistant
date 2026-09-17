# ASSISTANT RUNTIME — MASTER IMPLEMENTATION ROADMAP

**Repository:** `boxs-51/assistant`  
**Architecture baseline:** `8ae425571b7e37b1b4a7ef4a5c7ff6bfcae18768`  
**Status:** Architecture / Implementation Roadmap  
**Current objective:** Hoàn thiện Conversation Runtime, Chat Runtime, Capability Runtime và Agent Execution Runtime thống nhất.

---

# 1. MỤC TIÊU TỔNG THỂ

Roadmap này hướng tới một runtime có thể xử lý thống nhất:

```text
Conversation
Chat
Direct AI
Agent
Tool
Skill
Capability
Remote Client
MCP
Provider
```

với các yêu cầu chính:

```text
1. Conversation có temporal contract rõ ràng.
2. Chat có thể bật/tắt Agent.
3. Khi Agent tắt, AI vẫn có thể dùng read-only Tool/Skill.
4. Context cuối cùng gửi AI luôn có thời gian hiện tại.
5. TOOL / SKILL / AGENT cùng nằm trong Capability architecture.
6. Capability execution có invocation lifecycle thống nhất.
7. Retry / timeout / cancel / reconnect / late-result được kiểm soát.
8. Server, Client và MCP dùng cùng execution model.
```

Target architecture:

```text
                       Chat API
                          │
                          ▼
                  ChatExecutionMode
                          │
             ┌────────────┴────────────┐
             │                         │
           DIRECT                    AGENT
             │                         │
             ▼                         ▼
     DirectChatRuntime             AgentRuntime
             │                         │
     READ_ONLY Policy             Agent Policy
             │                         │
             └────────────┬────────────┘
                          │
                          ▼
                  CapabilityRuntime
                          │
                          ▼
             CapabilityInvocationService
                          │
                          ▼
                CapabilityRoutingPolicy
                          │
                          ▼
               CapabilityImplementation
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
      SERVER            CLIENT             MCP
```

---

# 2. ARCHITECTURAL DEFINITIONS

Ba khái niệm sau phải được tách biệt:

```text
CapabilityDefinition
        ≠
CapabilityImplementation
        ≠
CapabilityInvocation
```

## CapabilityDefinition

Mô tả capability là gì.

Ví dụ:

```text
filesystem.read
web.search
repository.analyze
coding-agent
```

---

## CapabilityImplementation

Mô tả capability chạy ở đâu và bằng driver nào.

Ví dụ:

```text
filesystem.read

├── server-python
├── client-laptop
└── mcp-filesystem
```

---

## CapabilityInvocation

Một lần chạy cụ thể.

Ví dụ:

```text
invocation_id = inv_123
capability_id = filesystem.read
implementation = client-laptop
```

---

# 3. ROADMAP TỔNG

```text
PHASE 1
Conversation Temporal Contract
        ↓

PHASE 1.5
Chat Execution Profiles
+ Direct Read-only Capability Loop
+ Temporal AI Context
        ↓

PHASE 2
Canonical Invocation Identity
        ↓

PHASE 3
Implementation-bound Driver Resolution
        ↓

PHASE 4
Unified Server Capability Execution
        ↓

PHASE 5
Capability Invocation State Machine
        ↓

PHASE 6
Invocation Persistence / Attempts / Events
        ↓

PHASE 7
Unified Production E2E
        ↓

PHASE 8
Legacy Execution Cleanup
```

---

# PHASE 1 — CONVERSATION TEMPORAL CONTRACT

## Mục tiêu

Chuẩn hóa identity, thứ tự và thời gian của conversation.

Không sử dụng timestamp làm ordering authority.

---

## GatewayMessage target

```text
GatewayMessage

role
content
tool_calls
tool_results

turn_id
sequence
created_at
completed_at

metadata
```

---

# 1.1 `turn_id`

Một user request tạo một logical turn.

```text
TURN T1

USER
    turn_id=T1

ASSISTANT TOOL CALL
    turn_id=T1

TOOL RESULT
    turn_id=T1

ASSISTANT FINAL
    turn_id=T1
```

`turn_id` phải được propagate:

```text
HTTP
 ↓
SessionRuntime
 ↓
ChatRuntime / AgentRuntime
 ↓
Provider
 ↓
Capability
 ↓
Tool Result
 ↓
Assistant Response
```

---

# 1.2 `sequence`

Ordering authority của Session.

```text
Session S1

sequence=1 USER
sequence=2 ASSISTANT TOOL CALL
sequence=3 TOOL RESULT
sequence=4 ASSISTANT
sequence=5 USER
sequence=6 ASSISTANT
```

Rules:

```text
scope     = session
authority = server
monotonic = true
unique    = true
```

Database:

```text
UNIQUE(session_id, sequence)
```

History:

```text
ORDER BY sequence ASC
```

---

# 1.3 `created_at`

UTC timezone-aware.

```text
USER:
created_at = lúc server accept/persist message

ASSISTANT STREAM:
created_at = lúc assistant bắt đầu output

ASSISTANT NON-STREAM:
created_at = lúc response được tạo
```

---

# 1.4 `completed_at`

Streaming:

```text
created_at   = T1
completed_at = NULL
```

Completion:

```text
completed_at = T2
```

---

# 1.5 Stream correlation

Không dùng:

```text
_stream_buffers[session_id]
```

Target:

```text
_stream_buffers[(session_id, turn_id)]
```

hoặc:

```text
request_id / stream_id
```

độc lập.

---

# PHASE 1 EXIT GATE

Phải chứng minh:

```text
Turn A + Turn B
```

cùng session chạy đồng thời nhưng không trộn stream.

Database phải deterministic bằng:

```text
sequence
```

API session phải trả:

```text
turn_id
sequence
created_at
completed_at
```

---

# PHASE 1.5 — CHAT EXECUTION PROFILES

# Mục tiêu

API Chat hiện tại phải hỗ trợ hai execution mode:

```text
DIRECT
AGENT
```

nhưng:

```text
DIRECT != NO TOOLS
```

Direct Chat vẫn được sử dụng capability an toàn.

---

# 4.1 API convenience field

Transport có thể expose:

```text
agent_enabled: bool
```

Ví dụ:

```json
{
  "agent_enabled": false,
  "messages": [
    {
      "role": "user",
      "content": "Đọc repository và giải thích kiến trúc này"
    }
  ]
}
```

Nhưng internal runtime không dùng boolean.

Normalize:

```text
agent_enabled=false
        ↓
ChatExecutionMode.DIRECT

agent_enabled=true
        ↓
ChatExecutionMode.AGENT
```

---

# 4.2 ChatExecutionMode

Canonical:

```text
ChatExecutionMode

DIRECT
AGENT
```

Transport chỉ quyết định mode.

Transport không chứa Agent logic.

---

# 4.3 DIRECT MODE

Direct Chat không chạy:

```text
AgentRuntime
Agent planner
Agent continuation
Agent delegation
Sub-agent
Long-running agent workflow
```

Nhưng vẫn có thể sử dụng:

```text
✓ Context-only Skill
✓ Read-only Tool
✓ Read-only executable Skill
✓ Provider tool calling
```

---

# 4.4 DIRECT READ-ONLY CAPABILITY POLICY

Tạo:

```text
CapabilityAccessProfile

NONE
DIRECT_READ_ONLY
AGENT_POLICY
```

Mapping:

```text
DIRECT
    ↓
DIRECT_READ_ONLY

AGENT
    ↓
AGENT_POLICY
```

---

# 4.5 Capability Effect Contract

Không xác định read-only bằng tên tool.

Không dùng:

```text
tool.name.startswith("read_")
```

Target:

```text
CapabilityEffect

READ
WRITE
EXECUTE
EXTERNAL_SIDE_EFFECT
PRIVILEGED
```

Ví dụ:

```text
filesystem.read

effects:
    READ
```

```text
database.select

effects:
    READ
```

```text
filesystem.write

effects:
    WRITE
```

```text
terminal.execute

effects:
    EXECUTE
    EXTERNAL_SIDE_EFFECT
```

```text
email.send

effects:
    EXTERNAL_SIDE_EFFECT
```

---

# 4.6 DIRECT capability filter

Direct Chat chỉ expose capability nếu:

```text
authorized
AND
effect subset {READ}
AND
execution mode allowed
AND
kind allowed
```

AI không nên nhìn thấy capability bị cấm.

Không:

```text
AI sees filesystem.write
        ↓
AI calls it
        ↓
server rejects
```

Target:

```text
AI never receives filesystem.write definition
```

trong DIRECT mode.

---

# 4.7 Skill semantics

Skill phải tách:

```text
SKILL

├── CONTEXT_ONLY
└── EXECUTABLE
```

---

## CONTEXT_ONLY Skill

Ví dụ:

```text
coding guidelines
AGENT.md
SQL expert instructions
repository analysis instructions
```

Flow:

```text
SkillResolver
      ↓
ContextAssembler
      ↓
AI Context
```

Không tạo CapabilityInvocation.

DIRECT và AGENT đều sử dụng được.

---

## EXECUTABLE Skill

Ví dụ:

```text
analyze_repository
summarize_project
search_knowledge
```

Nếu:

```text
effects = READ
```

DIRECT được phép thực thi.

Nếu:

```text
effects = WRITE
```

DIRECT không được phép.

---

# 4.8 DirectChatRuntime

DIRECT mode vẫn cần bounded tool loop.

Target:

```text
DirectChatRuntime
```

Flow:

```text
User
 ↓
ContextBuilder
 ↓
Provider
 ↓
Tool Call?
 │
 ├── NO → Final Answer
 │
 └── YES
       ↓
 CapabilityRuntime
       ↓
 DIRECT_READ_ONLY policy
       ↓
 Tool Result
       ↓
 Context rebuild
       ↓
 Provider
       ↓
 Final Answer
```

---

# 4.9 DirectChatRuntime không được trở thành AgentRuntime thứ hai

Cần hard limits.

Ví dụ:

```text
DirectChatPolicy

max_tool_rounds = 2
max_tool_calls = 4

allow_parallel_tools = true

allow_agent_capabilities = false
allow_long_running = false

allowed_effects = {READ}
```

---

# 4.10 DIRECT execution example

```text
User:
"Kiểm tra repository và giải thích lỗi."

        ↓

Inference #1

        ↓

AI calls:
filesystem.read
repository.search

        ↓

CapabilityRuntime

        ↓

Tool Results

        ↓

Inference #2

        ↓

Final Answer
```

Không cần AgentRuntime.

---

# 4.11 AGENT MODE

Flow:

```text
Chat API
   ↓
ChatExecutionMode.AGENT
   ↓
Agent Resolution
   ↓
AgentContextAssembler
   ↓
AgentRuntime
   ↓
Inference
   ↓
Capability calls
   ↓
Agent continuation
   ↓
Final response
```

Agent sử dụng policy:

```text
Identity authorization
+
Agent allowlist
+
Capability policy
+
Implementation availability
```

---

# 4.12 Agent capability selection

Có thể hỗ trợ:

```text
agent_id
```

Ví dụ:

```json
{
  "agent_enabled": true,
  "agent_id": "coding-agent"
}
```

Nếu không có `agent_id`:

```text
default Agent
```

chỉ được sử dụng nếu Gateway đã cấu hình rõ default Agent.

Không random selection.

---

# PHASE 1.5B — TEMPORAL AI CONTEXT

## Mục tiêu

Mỗi inference phải biết:

```text
current date
current time
timezone
UTC offset
```

để hiểu chính xác:

```text
hôm nay
ngày mai
hôm qua
tuần này
3 ngày nữa
bây giờ
```

---

# TemporalContext

```text
TemporalContext

current_time
current_date
timezone
utc_offset
```

Ví dụ:

```text
current_time = 2026-09-17T16:41:00+07:00
current_date = 2026-09-17
timezone     = Asia/Ho_Chi_Minh
utc_offset   = +07:00
```

---

# Source of truth

Server tạo time tại thời điểm build context.

```text
ContextBuilder
       ↓
TemporalContextProvider
       ↓
server clock
       +
session/user timezone
```

Không để AI tự suy luận ngày hiện tại.

---

# Không sửa User Message

Không:

```text
USER:
[Current time = ...]
Hôm nay có gì mới?
```

Temporal information phải nằm trong runtime/system context.

---

# Context target — DIRECT

```text
System Prompt
      +
Temporal Context
      +
Selected Context-only Skills
      +
Conversation
      +
READ_ONLY capability definitions
      ↓
InferenceRequest
```

---

# Context target — AGENT

```text
Agent System Prompt
      +
Agent Instructions
      +
Skill Instructions
      +
Temporal Context
      +
Conversation
      +
Agent Capability Definitions
      +
Constraints
      ↓
InferenceRequest
```

---

# Temporal context refresh

Không chỉ tạo một lần cho toàn request.

Mỗi inference nên refresh:

```text
Inference #1
TemporalContext = T1

Tool execution

Inference #2
TemporalContext = T2
```

với:

```text
T2 >= T1
```

---

# Conversation time và Current time khác nhau

Ví dụ:

```text
Message.created_at
    = 15:00

Current inference time
    = 16:41
```

Đây là hai concepts riêng.

---

# ContextBuilder target

```text
ContextBuilder
│
├── SystemPromptProvider
├── TemporalContextProvider
├── ConversationProvider
├── SkillResolver
├── CapabilityResolver
└── ConstraintProvider
        │
        ▼
Canonical Context
        │
        ▼
InferenceRequest
```

Provider adapter không được tự thêm thời gian.

---

# PHASE 1.5 EXIT GATE

DIRECT:

```text
agent_enabled=false
```

AI được phép:

```text
✓ web.search
✓ filesystem.read
✓ repository.search
✓ database readonly query
✓ context-only skill
✓ executable READ skill
```

AI không được phép:

```text
✗ filesystem.write
✗ delete
✗ shell execution
✗ email/send-message
✗ privileged operation
✗ invoke Agent
```

AGENT:

```text
agent_enabled=true
```

phải chạy:

```text
AgentRuntime
→ Capability
→ Tool
→ Result
→ Second inference
```

Cả DIRECT và AGENT đều phải nhận TemporalContext.

---

# PHASE 2 — CANONICAL INVOCATION IDENTITY

## Mục tiêu

Một logical execution chỉ có một:

```text
invocation_id
```

xuyên toàn hệ thống.

---

# Vấn đề cần loại bỏ

Không được:

```text
Agent:
inv_ABC

CapabilityRuntime:
capinv_XYZ
```

cho cùng một operation.

---

# Target

```text
ToolExecutionRequest
 invocation_id=INV1
        ↓
CapabilityRuntime
 invocation_id=INV1
        ↓
Driver
 invocation_id=INV1
        ↓
Remote Client / MCP / Server
        ↓
CapabilityResult
 invocation_id=INV1
```

---

# Rule

Nếu caller có invocation ID:

```text
reuse it
```

Nếu không:

```text
CapabilityRuntime generates it
```

Sau đó:

```text
invocation_id immutable
```

Retry:

```text
same invocation_id
```

Reconnect:

```text
same invocation_id
```

Fallback:

```text
same invocation_id
```

---

# PHASE 2 EXIT GATE

Một invocation ID phải giống nhau trong:

```text
AgentRuntime
ToolExecutionRequest
CapabilityRuntime
RemoteClientDriver
WebSocket protocol
CapabilityResult
Persistence
```

---

# PHASE 3 — IMPLEMENTATION-BOUND DRIVER RESOLUTION

## Mục tiêu

Driver phải được resolve từ:

```text
implementation_id
```

không phải chỉ:

```text
capability_id
```

---

# Hiện tại cần tiến hóa từ

```text
CapabilityRegistry

capability_id
    ↓
driver
```

sang:

```text
CapabilityDriverRegistry

implementation_id
    ↓
driver
```

---

# Target

```text
CapabilityRuntime
        ↓
CapabilityCatalog
        ↓
CapabilityRoutingPolicy
        ↓
CapabilityImplementation
        ↓
implementation_id
        ↓
CapabilityDriverRegistry
        ↓
BaseCapabilityDriver
```

---

# Ví dụ

```text
filesystem.read

├── server:filesystem.read
│      PythonCapabilityDriver
│
├── client:laptop:filesystem.read
│      RemoteClientDriver
│
└── mcp:filesystem.read
       McpCapabilityDriver
```

Routing chọn implementation nào thì driver tương ứng phải thực thi.

---

# PHASE 3 EXIT GATE

Một capability có thể có:

```text
SERVER
CLIENT
MCP
```

đồng thời.

Test phải chứng minh driver selection đúng implementation.

---

# PHASE 4 — UNIFIED SERVER CAPABILITY EXECUTION

## Mục tiêu

Hợp nhất:

```text
TOOL
SKILL
AGENT
```

vào CapabilityRuntime.

---

# CapabilityDefinition target

```text
CapabilityDefinition

capability_id
kind

execution_mode
effects

input_schema
output_schema

metadata
```

---

# CapabilityKind

```text
TOOL
SKILL
AGENT
```

---

# CapabilityExecutionMode

```text
CONTEXT_ONLY
ONE_SHOT
STREAMING
LONG_RUNNING
```

Typical mapping:

```text
TOOL
    ONE_SHOT

Prompt Skill
    CONTEXT_ONLY

Executable Skill
    ONE_SHOT / LONG_RUNNING

AGENT
    LONG_RUNNING
```

---

# TOOL

Target:

```text
CapabilityDefinition
       ↓
CapabilityImplementation
       ↓
ServerToolDriver
       ↓
Tool
```

---

# SKILL

Context-only:

```text
SkillResolver
     ↓
ContextAssembler
```

Executable:

```text
CapabilityRuntime
     ↓
SkillCapabilityDriver
     ↓
SkillRuntime
     ↓
CapabilityResult
```

---

# AGENT

Registration:

```text
CapabilityDefinition
kind=AGENT
execution_mode=LONG_RUNNING
```

Implementation:

```text
location=SERVER
driver_kind=AGENT_RUNTIME
```

Execution:

```text
CapabilityRuntime
       ↓
AgentCapabilityDriver
       ↓
AgentRuntime
```

---

# PHASE 4 EXIT GATE

Catalog phải chứa:

```text
TOOL server
SKILL context-only
SKILL executable
AGENT server
```

Executable capability phải đi qua:

```text
CapabilityRuntime
```

---

# PHASE 5 — CAPABILITY INVOCATION STATE MACHINE

## Mục tiêu

Mọi capability execution đều có lifecycle chuẩn.

---

# Canonical state machine

```text
CREATED
   │
   ▼
DISPATCHING
   │
   ▼
RUNNING
   │
   ├──────────► COMPLETED
   │
   ├──────────► FAILED
   │
   ├──────────► CANCELLED
   │
   ├──────────► TIMED_OUT
   │
   ├──────────► RETRYING
   │                 │
   │                 └──► DISPATCHING
   │
   └──────────► WAITING
                     │
                     ├──► DISPATCHING
                     ├──► CANCELLED
                     ├──► TIMED_OUT
                     └──► FAILED
```

Terminal:

```text
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

---

# WAITING

Không tạo state riêng:

```text
WAITING_FOR_CONNECTION
WAITING_FOR_HUMAN
...
```

Target:

```text
state = WAITING
wait_reason = CONNECTION
```

Possible reasons:

```text
CONNECTION
HUMAN_APPROVAL
DEPENDENCY
RETRY_BACKOFF
RESOURCE
```

---

# CapabilityInvocation

```text
CapabilityInvocation

invocation_id

capability_id
kind
execution_mode

implementation_id
driver_kind

state
wait_reason

session_id
turn_id
execution_id
workflow_id
tool_call_id

connection_id

attempt
max_attempts

arguments
output
error

created_at
started_at
updated_at
completed_at
deadline_at

correlation_id
trace_id

revision
```

---

# PHASE 5 EXIT GATE

Invalid transitions phải bị reject.

Không cho:

```text
COMPLETED → RUNNING
FAILED → RUNNING
CANCELLED → COMPLETED
```

Terminal states immutable.

---

# PHASE 6 — INVOCATION ATTEMPTS / PERSISTENCE / EVENTS

## Mục tiêu

Tách logical Invocation khỏi physical Attempt.

---

# Invocation vs Attempt

```text
Invocation INV1

├── Attempt A1
│      CLIENT
│      connection lost
│
└── Attempt A2
       SERVER
       completed
```

Final:

```text
INV1 = COMPLETED
```

---

# Attempt

```text
CapabilityInvocationAttempt

attempt_id
invocation_id

attempt_number

implementation_id
driver_kind
connection_id

state

started_at
completed_at

error
metadata
```

---

# Persistence

Tạo:

```text
capability_invocations
capability_invocation_attempts
```

Không biến:

```text
agent_tool_calls
agent_tool_results
```

thành global execution authority.

Agent record chỉ reference:

```text
invocation_id
```

---

# Optimistic concurrency

Invocation có:

```text
revision
```

Transition:

```text
revision=7
   ↓
CAS transition
   ↓
revision=8
```

Dùng để xử lý:

```text
cancel vs result
timeout vs result
disconnect vs result
retry vs late result
old connection vs reconnect
```

---

# Canonical events

```text
capability.invocation.created
capability.invocation.dispatched
capability.invocation.started
capability.invocation.waiting
capability.invocation.retrying
capability.invocation.completed
capability.invocation.failed
capability.invocation.cancelled
capability.invocation.timed_out
```

Event:

```text
event_id

invocation_id
attempt_id

capability_id
implementation_id

session_id
turn_id
execution_id

previous_state
state

created_at

correlation_id
causation_id
trace_id
```

---

# PHASE 6 EXIT GATE

Test:

```text
retry
timeout
cancel
disconnect
late result
foreign result
double result
server fallback
connection replacement
```

Một Invocation chỉ có một terminal result.

---

# PHASE 7 — UNIFIED PRODUCTION E2E

## Mục tiêu

Chứng minh toàn architecture chạy thực tế.

---

# E2E 1 — Direct Read-only Chat

```text
Chat
agent_enabled=false
 ↓
DirectChatRuntime
 ↓
Provider
 ↓
READ Tool Call
 ↓
CapabilityRuntime
 ↓
Result
 ↓
Second inference
 ↓
Final Answer
```

---

# E2E 2 — Direct Context Skill

```text
Chat
 ↓
Context-only Skill
 ↓
ContextBuilder
 ↓
Provider
 ↓
Final Answer
```

---

# E2E 3 — Server Tool

```text
Agent
 ↓
CapabilityRuntime
 ↓
Invocation
 ↓
Server Tool
 ↓
Result
 ↓
Second inference
```

---

# E2E 4 — Executable Skill

```text
CapabilityRuntime
 ↓
SkillCapabilityDriver
 ↓
SkillRuntime
 ↓
Result
```

---

# E2E 5 — Server Agent Capability

```text
Capability API
 ↓
Invocation
 ↓
AgentCapabilityDriver
 ↓
AgentRuntime
 ↓
Inference
 ↓
Tool
 ↓
Inference
 ↓
Result
```

---

# E2E 6 — Remote Client Tool

```text
AgentRuntime
 ↓
CapabilityRuntime
 ↓
INV1
 ↓
RemoteClientDriver
 ↓
real WebSocket
 ↓
Client Tool
 ↓
Result INV1
 ↓
AgentRuntime
 ↓
Second inference
```

---

# E2E 7 — Disconnect + Fallback

```text
INV1
 ↓
Attempt 1 CLIENT
 ↓
disconnect
 ↓
WAITING / RETRYING
 ↓
Routing
 ↓
Attempt 2 SERVER
 ↓
COMPLETED
```

Same:

```text
invocation_id = INV1
```

---

# PHASE 7 EXIT GATE

Không sử dụng:

```text
FakeSocket
manual handle_inbound
manual result injection
```

Exit-gate tests phải chạy real:

```text
TCP
WebSocket
Gateway
DirectChatRuntime
AgentRuntime
CapabilityRuntime
ClientRuntime
Provider Runtime
```

---

# PHASE 8 — LEGACY EXECUTION CLEANUP

## Mục tiêu

Sau khi canonical runtime đã pass E2E mới cleanup legacy.

Đánh dấu:

```text
KEEP
COMPATIBILITY
MIGRATE
DELETE
```

cho:

```text
ToolRegistry
AgentRegistry
legacy execute_tool
legacy Agent execution
old tool endpoints
duplicate registration
duplicate status models
```

---

# Compatibility endpoints

Có thể giữ:

```text
/v1/tools
/v1/agents
/v1/capabilities/{id}/execute
```

nhưng bên trong delegate về canonical runtime.

Ví dụ:

```text
legacy API
   ↓
CapabilityRuntime
   ↓
CapabilityInvocationService
```

---

# 5. UPDATED P0 IMPLEMENTATION ORDER

```text
P0-1
Conversation turn_id

P0-2
Session sequence

P0-3
created_at / completed_at

P0-4
stream correlation by turn

P0-5
ChatExecutionMode
    DIRECT
    AGENT

P0-6
agent_enabled transport contract

P0-7
CapabilityAccessProfile
    DIRECT_READ_ONLY
    AGENT_POLICY

P0-8
CapabilityEffect
    READ
    WRITE
    EXECUTE
    EXTERNAL_SIDE_EFFECT
    PRIVILEGED

P0-9
DirectChatRuntime bounded tool loop

P0-10
Context-only Skill support in DIRECT

P0-11
Executable READ Skill support in DIRECT

P0-12
Hide non-read capabilities from DIRECT model context

P0-13
TemporalContextProvider

P0-14
Refresh TemporalContext every inference

P0-15
Canonical invocation_id

P0-16
Driver binding by implementation_id

P0-17
Server Tool capability

P0-18
Executable Skill capability

P0-19
Server Agent capability

P0-20
Capability Invocation State Machine

P0-21
Invocation Attempt model

P0-22
Persistence + CAS revision

P0-23
Retry / cancel / timeout / reconnect

P0-24
Unified production E2E
```

---

# 6. ARCHITECTURAL INVARIANTS

## Invariant 1

```text
timestamp != ordering
```

Ordering:

```text
sequence
```

---

## Invariant 2

```text
Turn != Session
```

---

## Invariant 3

```text
agent_enabled != tools_enabled
```

Tắt Agent không đồng nghĩa với tắt Tool.

---

## Invariant 4

```text
DIRECT != capability-free
```

DIRECT có thể sử dụng capability phù hợp policy.

---

## Invariant 5

```text
DIRECT capabilities are fail-closed
```

Model chỉ thấy capability được phép.

---

## Invariant 6

```text
Capability permission is based on effects,
not capability name.
```

---

## Invariant 7

```text
CONTEXT_ONLY Skill != Executable Skill
```

---

## Invariant 8

```text
CapabilityDefinition
!= CapabilityImplementation
```

---

## Invariant 9

```text
CapabilityImplementation
!= CapabilityInvocation
```

---

## Invariant 10

```text
Invocation != Attempt
```

---

## Invariant 11

```text
invocation_id is immutable
```

---

## Invariant 12

```text
Routing chooses Implementation.
Driver executes Implementation.
```

---

## Invariant 13

```text
Current time is runtime context,
not user content.
```

---

## Invariant 14

```text
TemporalContext is generated
before Provider Adapter.
```

---

## Invariant 15

```text
Every inference receives fresh TemporalContext.
```

---

## Invariant 16

```text
Conversation historical time
!=
runtime current time
```

---

## Invariant 17

Transport:

```text
HTTP
WebSocket
EventBus
```

không được trở thành execution authority.

---

# 7. FINAL TARGET ARCHITECTURE

```text
                              CLIENT
                                 │
                                 ▼
                       HTTP / WebSocket
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                           SERVER                             │
│                                                              │
│                    Conversation Runtime                      │
│                                                              │
│       session_id / turn_id / sequence / temporal            │
│                         │                                    │
│                         ▼                                    │
│                    Chat Runtime                              │
│                         │                                    │
│              ┌──────────┴──────────┐                         │
│              │                     │                         │
│          DIRECT                  AGENT                       │
│              │                     │                         │
│      DirectChatRuntime         AgentRuntime                  │
│              │                     │                         │
│     DIRECT_READ_ONLY           AgentPolicy                   │
│              │                     │                         │
│              └──────────┬──────────┘                         │
│                         │                                    │
│                         ▼                                    │
│                  CapabilityRuntime                           │
│                         │                                    │
│                         ▼                                    │
│              CapabilityInvocationService                     │
│                         │                                    │
│                         ▼                                    │
│                CapabilityRoutingPolicy                       │
│                         │                                    │
│                         ▼                                    │
│               CapabilityImplementation                       │
│                         │                                    │
│       ┌─────────────────┼────────────────────┐               │
│       │                 │                    │               │
│       ▼                 ▼                    ▼               │
│     TOOL              SKILL                AGENT             │
│       │                 │                    │               │
│ Python/MCP/Remote   SkillDriver       AgentCapabilityDriver │
│                                              │               │
│                                              ▼               │
│                                          AgentRuntime        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

# 8. DEFINITION OF DONE

Hệ thống được coi là đạt mục tiêu roadmap khi chứng minh được:

```text
Conversation
    ↓
Turn
    ↓
Chat execution selection
    ↓
DIRECT hoặc AGENT
    ↓
Context Assembly
    ↓
Temporal Context
    ↓
Capability visibility policy
    ↓
Provider
    ↓
Optional capability invocation
    ↓
Implementation routing
    ↓
Driver execution
    ↓
Retry / cancel / timeout / reconnect
    ↓
Result
    ↓
Context rebuild
    ↓
Final inference
    ↓
Assistant response
```

Các invariant cuối:

```text
deterministic conversation ordering

one logical turn_id per turn

fresh current-time context per inference

DIRECT supports bounded read-only capabilities

DIRECT cannot access WRITE/EXECUTE/side-effect capabilities

context-only Skills work without AgentRuntime

one invocation_id per logical execution

one terminal result per Invocation

late/stale results cannot corrupt state

SERVER / CLIENT / MCP routing is deterministic

TOOL / SKILL / AGENT share Capability infrastructure

AgentRuntime does not need to know physical capability location
```

---

# 9. FUTURE ROADMAP — CHƯA TRIỂN KHAI

Sau khi server runtime hoàn thành mới chuyển sang:

```text
Stateless Direct Provider Inference API

Client-managed AgentRuntime

Local-first Session Runtime

Local Context Management

Local Capability Runtime

Local ↔ Server Session Sync

Agent Presence

Local Agent handshake

Remote Agent invocation

Multi-device session replicas
```

Architecture hiện tại phải chuẩn bị sẵn cho các phase này nhưng không triển khai sớm.

---

# 10. MASTER CHECKPOINT

```text
CURRENT BASELINE
    8ae425571b7e37b1b4a7ef4a5c7ff6bfcae18768

NEXT TARGET
    Phase 1
    Conversation Temporal Contract

THEN
    Phase 1.5
    Chat Execution Profiles
    + Direct Read-only Capability Loop
    + Temporal AI Context

THEN
    Phase 2
    Canonical Invocation Identity

THEN
    Phase 3
    implementation_id Driver Binding

THEN
    Phase 4
    Unified TOOL / SKILL / AGENT

THEN
    Phase 5 + 6
    Capability Invocation Runtime

THEN
    Phase 7
    Real Production E2E

FINAL
    Phase 8
    Legacy Cleanup
```

**Không chuyển sang Client-Orchestrated Runtime trước khi server-side Runtime đạt toàn bộ Phase 7 exit gate.**