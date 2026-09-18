**CL ↔ SE ARCHITECTURE AUDIT**

**Pre-Implementation Roadmap R0–R7**

_Scope: R0–R3 identity/affinity contracts • R4 HITL/Dispatcher invariants • R6–R7 reconnect/continuation_

| **Item**       | **Value**                                            |
| -------------- | ---------------------------------------------------- |
| Repository     | boxs-51/assistant                                    |
| Audited branch | main                                                 |
| Audited commit | c75e465443df42d610b5ae3cb2d121491c9afa3e             |
| Commit message | can cap nhat client lai                              |
| Audit date     | 2026-09-18                                           |
| Mode           | REVIEW / ROADMAP ONLY — no implementation patch      |
| Primary scope  | cl/src compared against se/src production call-sites |

**Purpose**

Tài liệu này là baseline kiến trúc để duyệt trước khi code. Mục tiêu không phải mô tả mọi file trong repo, mà khóa các contract có khả năng gây migration sai: identity lifetime, connection affinity, wire protocol, HITL boundary, invocation correctness, reconnect và durable continuation.

# Mục lục

- 1\. Executive summary
- 2\. Scope, evidence và baseline
- 3\. Current-state architecture diagnosis
- 4\. R0 — Baseline Freeze
- 5\. Identity Contract v1: user_id / client_id / connection_id / session_id
- 6\. R1 — Architecture Contract Freeze
- 7\. R2 — Wire Contract Alignment
- 8\. R3 — Connection Affinity / Production Execution Binding
- 9\. R4 — HITL & CapabilityDispatcher invariants
- 10\. R6 — Client Reconnect State Machine
- 11\. R7 — Continuation / Resume Protocol
- 12\. Cross-phase test strategy
- 13\. Roadmap, sequencing và stop conditions
- 14\. Decision log cần duyệt
- 15\. Evidence map / audit references

# 1\. Executive summary

Audit xác nhận server execution plane đã gần đạt kiến trúc mong muốn: AgentRuntime là single-agent execution authority; CapabilityRuntime + RoutingPolicy hỗ trợ server/client implementation; RemoteClientDriver + RealtimeMultiplexer đã có hard connection affinity khi connection_id được truyền đúng. Vấn đề chính nằm ở production wiring và client lifecycle, không còn chỉ là thiếu một vài DTO.

| **ID** | **Finding**                                                                                               | **Severity** | **Kết luận**                                                                                                      |
| ------ | --------------------------------------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------- |
| F-01   | HTTP Agent production path không truyền connection_id vào AgentExecutionContext.                          | P0           | Remote client tool có thể không route/authorize được dù E2E trực tiếp AgentRuntime pass.                          |
| F-02   | client_id mặc định là chuỗi chung 'desktop-client', không phải installation identity unique.              | P0.5         | Không đủ để phân biệt nhiều desktop client của cùng user.                                                         |
| F-03   | connection_id được tạo một lần trong ClientRuntime và reuse sau auth switch/reconnect.                    | P0           | Xung đột server lifecycle: implementation của connection cũ bị REMOVED và continuation yêu cầu connection_id mới. |
| F-04   | session_id đang bị overload ở auth identity, realtime transport và conversation/task.                     | P0           | Không được dùng session_id để suy luận client affinity.                                                           |
| F-05   | capability.result wire contract chưa canonical; SE resolve toàn payload trong khi CL bọc {'result': ...}. | P0           | Tool output bị biến dạng; raw scalar/list cũng không có contract ổn định.                                         |
| F-06   | CapabilityDispatcher bypass HITL/risk layer của ToolExecutor.                                             | P0           | Chuyển online Agent lên SE ngay sẽ mất local consent boundary.                                                    |
| F-07   | Dispatcher duplicate handling có thể terminal hóa invocation đang chạy.                                   | P0           | Rủi ro exactly-once và side-effect correctness.                                                                   |
| F-08   | Reconnect state machine và resume transport chưa tồn tại.                                                 | P0           | SE continuation mới là internal capability, chưa là end-to-end client feature.                                    |
| F-09   | DurableAgentStore.resume_execution rehydrate pending tool call thiếu field iteration bắt buộc.            | P0           | Current resume path có thể fail khi ToolExecutionRequest.model_validate().                                        |
| F-10   | CI full suite không chạy vì pywin32==312 trên ubuntu-latest.                                              | P0-Gate      | Không thể dùng CI hiện tại làm regression baseline.                                                               |

DECISION PROPOSAL — Online Agent authority = SE AgentRuntime; CL AgentEngine chỉ giữ LOCAL/OFFLINE cho đến khi streaming/HITL parity hoàn tất.

DECISION PROPOSAL — Remote WebSocket self-registration mặc định dành cho client-executable TOOL; SKILL/AGENT giữ server-owned semantics hiện tại.

DECISION PROPOSAL — client_id stable per installation; connection_id rotate per WebSocket generation; session_id không được dùng thay connection_id.

DECISION PROPOSAL — Local HITL là final consent boundary cho side effects xảy ra trên client, kể cả khi invocation đến từ SE AgentRuntime.

# 2\. Scope, evidence và baseline

Baseline được audit là commit c75e465443df42d610b5ae3cb2d121491c9afa3e trên main. Audit tập trung production call-site, transport contracts, tests và lifecycle behavior; không dựa riêng vào comment hoặc test harness.

- Client: ClientRuntime, GatewayRealtimeClient, CapabilityRuntime, CapabilityDispatcher, HITLManager/RiskAnalyzer, Gateway schemas, UIBridge.
- Server: WorkflowRuntime, AgentRuntime, CapabilityRuntime/RoutingPolicy, RemoteClientDriver, ConnectionRuntime/RealtimeMultiplexer, ClientCapabilityRegistrationService, continuation/persistence, HTTP/WS routers.
- Tests: true TCP WebSocket E2E, Phase 6.9 continuation tests, Phase 5.9 durable resume tests, cl/tests coverage.
- CI/dependencies: GitHub Actions exit-gate workflows và root requirements.txt.

Không có patch implementation trong tài liệu. Mọi tên field/message mới dưới đây là contract proposal cần duyệt trước khi code.

# 3\. Current-state architecture diagnosis

CURRENT ONLINE SURFACES  
<br/>UI  
├─ submit_prompt()  
│ └─ CL AgentEngine  
│ ├─ Gateway inference  
│ └─ CL ToolExecutor -> HITL -> local tool  
│  
├─ run_agent()  
│ └─ /v1/multi-agent/\*  
│ └─ SE MultiAgentCoordinator -> SE AgentRuntime  
│  
└─ ClientRuntime  
├─ HTTP Gateway  
└─ WebSocket  
└─ CL CapabilityDispatcher -> local function  
<br/>SERVER  
Transport -> WorkflowRuntime  
├─ DIRECT -> DirectChatRuntime  
└─ AGENT -> AgentRuntime  
\-> ToolExecutionPort  
\-> CapabilityRuntime  
\-> RemoteClientDriver  
\-> WebSocket

Kiến trúc đích không cần một Agent loop thứ hai ở client cho online mode. Tuy nhiên AgentEngine/ToolExecutor chưa thể xóa sớm vì chúng đang chứa behavior mà canonical remote path chưa có, đặc biệt HITL/risk handling và một phần UX streaming.

# 4\. R0 — Baseline Freeze

## 4.1 CI hiện tại chưa phải regression baseline

Các workflow Phase 5.x dùng ubuntu-latest + Python 3.12.10 và cài trực tiếp root requirements.txt. Run hiện tại fail ở bước dependency installation vì pywin32==312 không có distribution cho Linux. Vì vậy full pytest và exit gate không chạy.

| **R0 risk**                                            | **Current evidence**                                                           | **Roadmap action (review-level)**                                                                                                     |
| ------------------------------------------------------ | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Windows dependency in cross-platform root requirements | pywin32==312 làm Ubuntu install fail.                                          | Tách/marker dependency theo platform; CI phải install được trước mọi architecture migration.                                          |
| WebSocket dependency namespace                         | requirements có websocket, websocket-client và websockets đồng thời.           | Audit package ownership; CL sync transport đang dùng API của websocket-client. Không để hai distribution cạnh tranh module websocket. |
| Client runtime test gap                                | cl/tests không có direct tests cho GatewayRealtimeClient/CapabilityDispatcher. | Thêm contract/unit tests trước refactor để bắt lifecycle regression.                                                                  |
| Exit-gate fragmentation                                | Nhiều phase5 workflow đều cài cùng root requirements.                          | Chốt một baseline workflow canonical; phase-specific gate chỉ chạy sau baseline.                                                      |

**EXIT GATE —** R0 chỉ pass khi ít nhất một Linux CI job chạy full suite thật sự, một Windows client-focused job chạy transport/HITL tests, và không còn dependency install failure che test result.

## 4.2 R0 test inventory tối thiểu

- Schema/contract tests CL↔SE.
- ClientRuntime auth transition + connection generation tests.
- GatewayRealtimeClient single-receiver and disconnect tests.
- CapabilityDispatcher duplicate/cancel/result tests.
- Real TCP WebSocket E2E giữ lại làm integration truth.
- Continuation durable resume test phải model_validate pending ToolExecutionRequest, không chỉ assert dictionary fields.

# 5\. Identity Contract v1

Đây là contract quan trọng nhất cần khóa trước R2/R3. Bốn ID có lifetime và authority khác nhau; không ID nào được dùng như fallback ngầm cho ID khác.

| **ID**        | **Semantic authority**                   | **Lifetime**                       | **Ai tạo**                | **Security role**                    | **Routing role**                                   |
| ------------- | ---------------------------------------- | ---------------------------------- | ------------------------- | ------------------------------------ | -------------------------------------------------- |
| user_id       | Authenticated principal / owner          | Auth identity                      | SE auth                   | AUTHORITATIVE                        | Owner filter; không chọn socket trực tiếp          |
| client_id     | Client installation identity             | Nhiều auth sessions / app restarts | CL once + persist         | NOT proof of auth                    | Device/client affinity metadata                    |
| connection_id | One live realtime transport generation   | Một WS connection                  | CL per connect generation | Validated against authenticated user | AUTHORITATIVE remote capability affinity           |
| session_id    | Domain conversation / agent task session | Conversation/task lifecycle        | Domain/API                | Ownership checked separately         | Context/history correlation; NOT transport routing |

## 5.1 user_id contract

- Source of truth: authenticated Identity từ SE; client không tự đặt user_id.
- Connection registration phải bind snapshot.user_id = authenticated identity.user_id.
- Capability owner_id cho CLIENT implementation phải bằng user_id của active connection.
- Login/logout đổi principal phải invalidate transport generation hiện tại trước khi capability re-register.
- user_id không đủ để chọn một client khi cùng user có nhiều devices/connections.

## 5.2 client_id contract

- client_id là opaque installation identifier, không phải tên hiển thị và không phải token.
- Nên generate UUID một lần và persist độc lập với auth session; không dùng literal chung 'desktop-client' làm identity thực tế.
- client_id không được dùng để bypass user_id authorization.
- Server lưu client_id trong ConnectionSnapshot metadata và CLIENT implementation metadata.
- Default resume policy đề xuất: cùng user_id + cùng client_id; cross-client resume phải là policy opt-in.

**EXIT GATE —** Một user có hai desktop clients phải tạo hai client_id khác nhau và server quan sát được hai active connection độc lập.

## 5.3 connection_id contract

- connection_id đại diện đúng một WebSocket generation.
- Generate trước connection.register; immutable trong suốt socket lifetime.
- Bất kỳ reconnect sau disconnect/auth switch đều tạo connection_id mới.
- connection_id cũ không được reuse vì server đã remove implementation binding của connection cũ.
- Mọi remote CLIENT invocation phải carry explicit connection_id.
- Foreign CLIENT implementation luôn bị excluded khi request có hard connection affinity.
- Connection ID không được persist như durable installation identity.

## 5.4 session_id contract và namespace collision

Repo hiện dùng cùng tên session_id cho ít nhất ba khái niệm: Identity.session_id (auth session), GatewayRealtimeClient.session_id (transport-generated session-\*), và chat/multi-agent session_id (conversation/task). Đây là namespace collision và là nguyên nhân khiến fallback resolve_connection_id(identity.session_id) nguy hiểm.

DECISION PROPOSAL — Canonical domain session_id = conversation/agent session. Auth session và transport session phải được đặt tên riêng trong migration.

| **Current field**                | **Current meaning**                         | **Target name/semantics**                                          |
| -------------------------------- | ------------------------------------------- | ------------------------------------------------------------------ |
| Identity.session_id              | Auth/session record từ authentication layer | auth_session_id (hoặc giữ alias tạm, nhưng không dùng cho routing) |
| GatewayRealtimeClient.session_id | Random transport session-\*                 | transport_session_id hoặc bỏ nếu không còn cần                     |
| ConnectionSnapshot.session_id    | Transport/session key dùng registry mapping | Legacy only; không dùng resolve remote capability                  |
| GatewayChatRequest.session_id    | Conversation/context session                | Giữ session_id canonical                                           |
| AgentSession.session_id          | Multi-agent domain session                  | Giữ session_id canonical                                           |

## 5.5 Forbidden inference rules

| **Forbidden rule**                                         | **Reason**                                                                       |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------- |
| connection_id := resolve(identity.session_id)              | Auth session != transport affinity; sai khi nhiều client hoặc conversation khác. |
| client_id := user_id                                       | Một user có thể có nhiều installations.                                          |
| user_id := capability.owner_id từ payload không kiểm chứng | Owner phải được kiểm bằng authenticated connection snapshot.                     |
| session_id := connection_id                                | Conversation lifecycle dài hơn transport generation.                             |
| reconnect := reuse old connection_id                       | Server capability implementation đã REMOVED; continuation yêu cầu identity mới.  |

IDENTITY RELATIONSHIP (TARGET)  
<br/>user_id = U1  
│  
├─ client_id = C1 (desktop installation)  
│ ├─ connection_id = K1 \[dead\]  
│ └─ connection_id = K2 \[active reconnect\]  
│  
└─ client_id = C2 (another installation)  
└─ connection_id = K3 \[active\]  
<br/>session_id = S_CHAT_42  
└─ may execute through K2 now, but session S_CHAT_42 is not K2 and must survive K2 death.

# 6\. R1 — Architecture Contract Freeze

| **Decision**             | **Proposed contract**                                                    | **Why**                                                                      |
| ------------------------ | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Agent authority          | ONLINE -> SE AgentRuntime; LOCAL/OFFLINE -> CL LocalAgentEngine.         | Loại split-brain nhưng vẫn giữ fallback/test mode.                           |
| Remote client capability | WS self-registration cho client-executable TOOL.                         | Khớp server registration semantics và RemoteClientDriver.                    |
| SKILL                    | Server catalog/context/runtime owned.                                    | CONTEXT_ONLY skill không phải remote executable function.                    |
| AGENT                    | Server registered + AgentCapabilityDriver/AgentRuntime.                  | Agent execution authority nằm SE.                                            |
| HITL                     | Local client là final consent boundary cho local side effects.           | Server authorization không thay thế user consent trên device.                |
| Affinity                 | Hard connection affinity chỉ ở physical CLIENT implementation selection. | Không khóa toàn bộ Agent loop vào socket; server continuation vẫn được phép. |

**EXIT GATE —** R1 pass khi các decision trên được viết thành contract tests/docs và không còn production component dùng session_id như implicit client routing key.

# 7\. R2 — Wire Contract Alignment

## 7.1 Realtime envelope v1 proposal

RealtimeEnvelope v1  
{  
"protocol_version": 1,  
"type": "...",  
"message_id": "...",  
"session_id": "&lt;domain session or null&gt;",  
"connection_id": "&lt;required for correlated client invocation&gt;",  
"execution_id": "...",  
"invocation_id": "...",  
"trace_id": "...",  
"payload": {...}  
}

- Server remains source of validation; extra fields should be explicitly versioned, not silently ignored.
- connection_id required for capability.invoke/result/error/cancel/cancelled.
- execution_id and invocation_id keep separate semantics: execution = Agent/Capability execution scope; invocation = one remote attempt identity.
- message_id is transport message identity, never idempotency identity for a tool.

## 7.2 capability.result

Current CL wraps result dưới key result, trong khi SE resolves toàn envelope.payload. Không nên sửa bằng cách gửi raw payload vì tool output có thể là scalar/list/null. Contract nên có wrapper canonical.

capability.result  
payload = {  
"output": &lt;Any JSON value&gt;,  
"metadata": {  
"duration_ms": ...,  
"implementation_version": ...  
}  
}

**EXIT GATE —** Exact E2E assertion: local return value X phải trở thành CapabilityResult.output == X cho dict/string/number/list/null.

## 7.3 capability.error

capability.error  
payload = {  
"code": "STABLE_MACHINE_CODE",  
"message": "human readable",  
"details": {...},  
"retryable": false  
}

- SE hiện chỉ giữ message khi convert RemoteCapabilityError; R2 phải preserve code/details/retryable.
- Error code là machine contract; exception class name của Python không phải stable protocol code.

## 7.4 cancellation semantics

- capability.cancel = request cancellation; không tự động có nghĩa side effect đã rollback.
- capability.cancelled = local invocation terminal và không còn result được publish cho invocation đó.
- Nếu tool không cooperative, client phải suppress late result; side effect semantics phải được capability metadata mô tả.
- WRITE/EXTERNAL_SIDE_EFFECT capability muốn auto-resume/retry phải khai báo idempotency/resume policy.

## 7.5 registration semantics

- implementation_id phải gắn với connection generation, ví dụ &lt;connection_id&gt;:&lt;capability_id&gt;.
- Re-registration trên connection mới tạo implementation_id mới.
- Definition identity có thể stable theo capability_id/version; implementation identity không stable qua reconnect.
- Capability register ACK phải là canonical realtime envelope, không trộn legacy {'status':'error'} lâu dài.

**EXIT GATE —** R2 pass khi CL và SE dùng cùng contract fixtures và deserialize/serialize round-trip giống nhau.

# 8\. R3 — Connection Affinity / Production Execution Binding

## 8.1 Current production gap

True WebSocket E2E hiện chứng minh RemoteClientDriver hoạt động khi test tự tạo AgentExecutionContext với connection_id. Nhưng WorkflowRuntime.\_execute_agent() và multi-agent executor trong se/src/main.py không truyền connection_id vào AgentExecutionContext. Do đó test path và production HTTP path chưa tương đương.

## 8.2 Proposed execution binding contract

DECISION PROPOSAL — HTTP request phải carry explicit connection_id khi caller muốn dùng capability của chính client đó.

| **Surface**                 | **Target field**                                           | **Propagation**                                                              |
| --------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------- |
| /v1/chat/completions DIRECT | connection_id optional                                     | ChatRequest -> WorkflowRuntime -> DirectChatRuntime -> CapabilityRuntime     |
| /v1/chat/completions AGENT  | connection_id optional/required when client tools expected | ChatRequest -> WorkflowRuntime -> AgentExecutionContext.connection_id        |
| /v1/multi-agent/tasks       | connection_id optional affinity                            | Task record/context_state -> executor -> AgentExecutionContext.connection_id |
| Capability execute HTTP     | already has connection_id                                  | Keep explicit; validate active ownership.                                    |

## 8.3 Server validation sequence

1. Authenticate request -> obtain user_id.
2. If connection_id is supplied: lookup active ConnectionSnapshot.
3. Require snapshot.user_id == authenticated user_id.
4. Optionally enforce expected client_id policy using snapshot.metadata.client_id; never trust client_id alone.
5. Pass connection_id explicitly into DirectChatRuntime/AgentExecutionContext.
6. CapabilityRoutingPolicy may prefer same-connection CLIENT implementation and allow SERVER continuation candidate; foreign clients remain excluded.

## 8.4 Direct mode is affected too

DirectChatRuntime có thể expose READ-only capabilities và gọi CapabilityRuntime, nhưng hiện execute_capability không nhận explicit connection_id. Nếu client advertises READ tool, DIRECT mode cũng cần R3 execution binding; không chỉ Agent mode.

## 8.5 Failure semantics

- No connection_id + only CLIENT implementation available -> deterministic CAPABILITY_CONNECTION_REQUIRED, không route sang foreign client.
- Stale connection_id -> deterministic REMOTE_CONNECTION_LOST/CONNECTION_NOT_ACTIVE.
- Active connection belongs to another user -> 403/authorization failure, không 404.
- Server implementation available -> policy có thể continue server-side sau remote loss theo existing Phase 6.9 intent.

**EXIT GATE —** R3 pass bằng real HTTP request + real TCP WS: chat/agent request mang active connection_id, model gọi remote tool đúng client, second inference nhận exact tool output.

# 9\. R4 — HITL & CapabilityDispatcher invariants

## 9.1 Core problem

CL AgentEngine/ToolExecutor hiện có HITL/risk behavior; remote CapabilityDispatcher lại resolve callable và execute trực tiếp. Nếu chuyển online Agent authority về SE trước R4, local side-effect capability có thể bypass consent.

TARGET CLIENT EXECUTION BOUNDARY  
<br/>capability.invoke  
│  
▼  
CapabilityDispatcher (transport correlation only)  
│  
▼  
LocalCapabilityExecutor  
│  
├─ registration snapshot check  
├─ input schema validation  
├─ local authorization / policy  
├─ risk classification  
├─ HITL approval (if required)  
├─ cancellation/deadline  
└─ invoke local/MCP implementation  
│  
▼  
TerminalOutcome -> result / error / cancelled

## 9.2 Mandatory invariants

| **Invariant** | **Contract**                                                              | **Rationale**                                                           |
| ------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| I-01          | Exactly one execution per invocation_id.                                  | Duplicate frame không được tạo side effect lần hai.                     |
| I-02          | Exactly one terminal outcome.                                             | result/error/cancelled mutually exclusive.                              |
| I-03          | Duplicate while RUNNING is idempotent.                                    | Không gửi terminal DUPLICATE_INVOCATION làm chết invocation gốc.        |
| I-04          | Duplicate after TERMINAL replays cached terminal outcome.                 | Hỗ trợ retransmit/reconnect mà không re-execute.                        |
| I-05          | Terminal cache bounded + TTL.                                             | Không leak memory như \_terminal set hiện tại.                          |
| I-06          | Connection match before execution.                                        | Envelope connection_id phải đúng current transport generation.          |
| I-07          | Capability must belong to registration snapshot of this connection.       | Tránh TOCTOU khi local registry reload.                                 |
| I-08          | Schema validate before HITL/execute.                                      | Không đưa invalid args vào UI/tool.                                     |
| I-09          | Risk decision is local-authoritative.                                     | Không tin risk hint từ server làm giảm mức consent.                     |
| I-10          | HIGH/CRITICAL fail closed when no approval UI.                            | Giữ safety behavior hiện tại.                                           |
| I-11          | Cancel can interrupt WAITING_APPROVAL.                                    | Không để UI approval treo sau server cancel/disconnect.                 |
| I-12          | Late worker completion after cancel is suppressed.                        | Không phát result sau cancelled.                                        |
| I-13          | Async and sync capability execution are explicit.                         | Không return coroutine object hoặc block event loop không kiểm soát.    |
| I-14          | Result must be JSON-normalizable or mapped to stable serialization error. | Wire protocol không phụ thuộc Python object.                            |
| I-15          | Bounded queue / worker backpressure.                                      | Không submit vô hạn vào ThreadPoolExecutor.                             |
| I-16          | Provenance visible to HITL.                                               | Approval UI biết capability, args, execution/agent origin, effect/risk. |

## 9.3 Invocation state machine

RECEIVED  
│ validate/dedupe  
▼  
PENDING  
├─> WAITING_APPROVAL  
│ ├─ deny ------> ERROR/DENIED  
│ ├─ cancel ----> CANCELLED  
│ └─ approve ---> RUNNING  
│  
└────────────────────> RUNNING  
├─ result ---> SUCCEEDED  
├─ error ----> FAILED  
└─ cancel ---> CANCELLING -> CANCELLED  
<br/>TERMINAL = SUCCEEDED | FAILED | CANCELLED | DENIED  
Duplicate invocation_id:  
RUNNING -> no re-execute  
TERMINAL -> replay cached terminal outcome

## 9.4 Cancellation truth

Python thread không thể bị force-kill an toàn. Vì vậy protocol phải tách 'client accepted cancellation' khỏi guarantee về rollback. Tool có side effect cần cooperative cancellation, idempotency key hoặc compensation contract. Không được tuyên bố exactly-once side effect chỉ dựa vào Future.cancel().

## 9.5 HITL provenance proposal

capability.invoke payload (proposed additions)  
{  
"capability_id": "...",  
"arguments": {...},  
"context": {  
"agent_id": "...",  
"tool_call_id": "...",  
"request_id": "...",  
"origin": "AGENT|DIRECT|CAPABILITY_HTTP"  
}  
}

Risk/effects authoritative data nên lấy từ local advertised capability definition; context từ server chỉ dùng provenance/display/correlation.

**EXIT GATE —** R4 pass khi real SE Agent invocation không thể execute HIGH/CRITICAL local tool nếu chưa có explicit local approval, kể cả khi server đã authorize capability.

# 10\. R6 — Client Reconnect State Machine

## 10.1 Required state machine

STOPPED  
│ start  
▼  
AUTH_READY  
│  
▼  
CONNECTING --socket fail--> BACKOFF  
│ socket open │  
▼ └----> CONNECTING (NEW connection_id)  
REGISTERING_CONNECTION  
│ ack ACTIVE  
▼  
REGISTERING_CAPABILITIES  
│ ack  
▼  
READY  
│  
├─ auth principal changes -> DRAINING -> CONNECTING (NEW connection_id)  
├─ socket lost -----------> DISCONNECTED -> BACKOFF  
└─ stop ------------------> STOPPING -> STOPPED

## 10.2 State invariants

| **ID** | **Invariant**                                                                          |
| ------ | -------------------------------------------------------------------------------------- |
| C-01   | One transport generation owns one connection_id.                                       |
| C-02   | Every connect attempt that can become ACTIVE uses a fresh connection_id.               |
| C-03   | client_id persists across reconnect/app restart; auth tokens may change independently. |
| C-04   | READY only after connection.register ACK + capability.register ACK.                    |
| C-05   | Capabilities are re-advertised on every new connection generation.                     |
| C-06   | Old receiver/heartbeat threads cannot mutate state of a newer generation.              |
| C-07   | start() is idempotent under concurrent UI calls.                                       |
| C-08   | Login/logout invalidates old connection before new principal registers capabilities.   |
| C-09   | Backoff is bounded and cancellation-aware; stop() interrupts backoff immediately.      |
| C-10   | Reconnect does not automatically resume executions until registration is complete.     |

## 10.3 Generation token

Khuyến nghị logical transport_generation tăng mỗi connect cycle. Receiver/heartbeat callback phải capture generation; callback từ socket cũ chỉ được cleanup tài nguyên của generation đó và không được set \_ready=False cho generation mới.

## 10.4 client_id persistence

AuthSessionStore hiện persist token/user_id nhưng không persist unique client installation ID. R6 nên có ClientIdentityStore riêng hoặc mở rộng state store để giữ client_id. client_id không được reset khi logout.

## 10.5 Capability lifecycle on reconnect

1. Disconnect marks old connection unusable; SE removes old CLIENT implementations.
2. CL generates K2 (new connection_id), authenticates/registers K2.
3. CL sends full capability registration snapshot with implementation_id derived from K2.
4. SE registers fresh implementations bound to K2.
5. Only after ACK, ClientRuntime enters READY and R7 resume may start.

**EXIT GATE —** R6 pass khi kill socket giữa idle state -> CL backoff -> new connection_id -> capability re-register -> same capability executable again without process restart.

# 11\. R7 — Continuation / Resume Protocol

## 11.1 Existing server capability

- AgentRuntime detects remote connection loss from tool results.
- AgentContinuationService creates durable WAITING_FOR_CONNECTION checkpoint when no server-side alternative exists.
- ContinuationService.reconnect requires a new connection_id and same owner user_id.
- confirm_merge protects against stale branch.
- DurableAgentStore can rebuild AgentExecutionContext and pending tool calls.

Tuy nhiên chưa có transport handler nối client reconnect vào reconnect()/confirm_merge()/resume_execution()/AgentRuntime.execute(). Do đó R7 là wiring + protocol phase, không phải chỉ thêm retry loop ở client.

## 11.2 Confirmed resume defects/gaps

| **Gap**                                                                  | **Impact**                                                                   | **Required gate**                                                |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| resume_execution pending tool dict thiếu iteration                       | ToolExecutionRequest.model_validate yêu cầu iteration -> resume có thể fail. | Test phải validate request thật và execute resumed pending call. |
| No resume HTTP/WS transport                                              | Client không thể yêu cầu continuation sau K2 READY.                          | Real client sends resume command.                                |
| Chat AGENT path converts missing final_message into generic RuntimeError | WAITING_FOR_CONNECTION metadata/checkpoint có thể bị mất khỏi chat response. | Structured waiting response/event.                               |
| Checkpoint không store origin client_id                                  | Không thể enforce same-installation resume policy.                           | Persist origin_client_id or chosen affinity policy.              |
| Client terminal cache stores only set, not outcome                       | Result completed nhưng send failed không thể replay trên duplicate/resume.   | Durable/bounded terminal outcome cache.                          |
| fail_all marks active IDs terminal then drops result                     | Resume cùng invocation_id có thể bị ignored forever.                         | Detach/cancel semantics redesign.                                |

## 11.3 Proposed external resume protocol

DECISION PROPOSAL — Expose one client action execution.resume; keep reconnect/branch/merge as server-internal two-phase transaction.

CLIENT (on NEW active connection K2)  
execution.resume  
{  
"execution_id": "agent_...",  
"checkpoint_id": "checkpoint-...",  
"connection_id": "K2"  
}  
<br/>SERVER validation  
1\. K2 is ACTIVE  
2\. authenticated user owns K2  
3\. execution owner == authenticated user  
4\. checkpoint_id is current WAITING_FOR_CONNECTION checkpoint  
5\. resume client policy passes (default same client_id)  
6\. pending capability exists and is ENABLED on K2  
<br/>SERVER internal  
continuation.reconnect(...)  
\-> continuation.confirm_merge(...)  
\-> durable_store.resume_execution(...)  
\-> context.connection_id = K2  
\-> restore pending tool request(s)  
\-> AgentRuntime.execute(context)  
<br/>SERVER ACK  
execution.resume.accepted  
{  
"execution_id": "...",  
"checkpoint_id": "&lt;new running checkpoint&gt;",  
"state": "RUNNING"  
}

## 11.4 How client learns it must resume

Socket đã chết nên old WS không thể deliver waiting event. Phải có một durable channel. Với chat HTTP, response cần preserve WAITING_FOR_CONNECTION as structured execution status. Với multi-agent background task, polling/get execution có thể trả same status.

Structured waiting status (conceptual)  
{  
"status": "WAITING_FOR_CONNECTION",  
"execution_id": "...",  
"checkpoint_id": "...",  
"pending_capability_id": "...",  
"retry_policy": "AUTO|USER_CONFIRM|NOT_SAFE"  
}

## 11.5 Resume affinity policy

| **Policy**      | **Meaning**                                                                   | **Recommendation**                    |
| --------------- | ----------------------------------------------------------------------------- | ------------------------------------- |
| SAME_CONNECTION | Impossible after disconnect because new connection_id required.               | Không dùng.                           |
| SAME_CLIENT     | Same user_id + same persistent client_id, new connection_id.                  | DEFAULT.                              |
| ANY_USER_CLIENT | Same user_id, another client installation may resume if capability available. | Opt-in only.                          |
| SERVER_CONTINUE | Route to non-client implementation if available.                              | Existing preferred continuation path. |

## 11.6 Exactly-once vs at-least-once

R7 không thể hứa exactly-once side effect cho arbitrary local Python/MCP function chỉ bằng network protocol. Nếu tool hoàn tất side effect nhưng connection mất trước khi result đến server, server không biết trạng thái cuối.

| **Capability class** | **Auto retry after reconnect** | **Requirement**                                                           |
| -------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| READ / pure          | Yes                            | Safe retry; same invocation_id preferred.                                 |
| Idempotent WRITE     | Yes                            | Tool consumes invocation_id/idempotency key and caches outcome.           |
| Non-idempotent WRITE | No automatic retry             | Require reconciliation/user decision or capability-specific resume token. |
| EXTERNAL_SIDE_EFFECT | Default no                     | Explicit idempotency/compensation contract required.                      |

## 11.7 Terminal outcome replay

Để same-client reconnect an toàn, client phải giữ TerminalOutcome theo invocation_id đủ lâu qua reconnect. Nếu server re-sends cùng invocation_id, client replay result/error/cancelled thay vì execute lại. Nếu process restart làm mất cache, policy phải dựa vào capability idempotency declaration.

## 11.8 Resume race invariants

| **Race** | **Scenario**                                         | **Required behavior**                                                                       |
| -------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| RACE-1   | Old late result arrives after disconnect             | Reject because old connection no longer owns invocation; do not merge into new branch.      |
| RACE-2   | Two resume requests for same checkpoint              | Only one branch/merge wins; second is idempotent if same branch token or conflict if stale. |
| RACE-3   | Reconnect happens before capability registration ACK | Resume rejected/not-ready; retry only after READY.                                          |
| RACE-4   | User logs out while waiting                          | Old principal cannot resume; new principal must pass ownership policy.                      |
| RACE-5   | Pending capability missing on new connection         | Remain WAITING; do not execute foreign client.                                              |
| RACE-6   | Server implementation appears after disconnect       | Server continuation may win; subsequent client resume sees stale checkpoint/conflict.       |
| RACE-7   | Local tool completed, result send failed             | Duplicate/resume must replay cached outcome, not re-execute.                                |

**EXIT GATE —** R7 pass only with real TCP disconnect mid-tool -> durable WAITING -> new connection_id -> capability re-register -> resume -> exact one committed tool result -> Agent second inference -> completion.

# 12\. Cross-phase test strategy

| **Layer**    | **Tests to add/strengthen**                                                           | **Purpose**                             |
| ------------ | ------------------------------------------------------------------------------------- | --------------------------------------- |
| Contract     | Shared fixtures for ChatRequest, RealtimeEnvelope, result/error/cancel.               | Stop CL↔SE drift.                       |
| CL unit      | Dispatcher state machine, HITL cancel, duplicate replay, terminal TTL, serialization. | Catch local correctness without server. |
| SE unit      | Execution binding validation, connection ownership, resume reconstruction.            | Catch routing/security issues.          |
| Architecture | No session->connection fallback in execution path; online Agent authority single.     | Protect architecture rules.             |
| Integration  | Connection rotation + re-registration + stale old implementation.                     | Lifecycle correctness.                  |
| Real E2E     | HTTP Agent + real WS client + remote tool + HITL + disconnect/resume.                 | Production flow truth.                  |
| Race tests   | late result, duplicate resume, auth switch, server continuation race.                 | Concurrency correctness.                |

## 12.1 Minimum R0–R7 E2E scenarios

- E2E-01: guest ClientRuntime -> WS READY -> HTTP DIRECT read tool -> exact result.
- E2E-02: HTTP AGENT -> remote client tool -> second inference -> final answer.
- E2E-03: HIGH-risk tool -> HITL deny -> Agent receives stable tool error; no side effect.
- E2E-04: HIGH-risk tool -> HITL approve -> one execution -> exact result.
- E2E-05: disconnect before tool start -> WAITING -> new connection -> resume.
- E2E-06: disconnect after local result computed but before server receives it -> replay outcome; no duplicate side effect.
- E2E-07: two active clients same user -> hard affinity selects requested connection only.
- E2E-08: login changes user -> old connection cannot remain owner of advertised capabilities.
- E2E-09: stale resume branch -> deterministic conflict; current execution unchanged.
- E2E-10: non-idempotent side-effect capability -> automatic reconnect retry blocked by policy.

# 13\. Roadmap, sequencing và stop conditions

| **Phase** | **Scope**                               | **Must not do yet**               | **Exit gate**                                                |
| --------- | --------------------------------------- | --------------------------------- | ------------------------------------------------------------ |
| R0        | CI/dependency/test baseline             | No architecture refactor          | Full tests actually run on target CI.                        |
| R1        | Freeze authority + identity semantics   | No online Agent migration         | Decision log approved + architecture tests.                  |
| R2        | Wire DTO/protocol alignment             | No reconnect automation           | CL↔SE round-trip contract tests.                             |
| R3        | Explicit execution connection affinity  | No removal of CL AgentEngine      | Real HTTP -> real WS remote tool E2E.                        |
| R4        | Canonical local execution policy + HITL | No bypass path                    | High-risk remote invocation cannot execute without approval. |
| R5\*      | Invocation correctness hardening        | No aggressive auto-retry          | Duplicate/cancel/result/race tests pass.                     |
| R6        | Reconnect generation state machine      | No resume before capability ACK   | New K on reconnect + re-register works.                      |
| R7        | Durable resume protocol                 | No auto-retry unsafe side effects | Mid-tool disconnect/resume E2E passes.                       |

\*R5 được nhắc để giữ dependency sequencing dù yêu cầu audit lần này tập trung R0–R4 và R6–R7.

## 13.1 Stop conditions

- Dừng trước R3 nếu R0 CI vẫn đỏ do dependency install.
- Dừng trước R4 nếu identity/session contract chưa được duyệt.
- Dừng migration online Agent nếu R4 HITL chưa canonical.
- Dừng reconnect auto-resume nếu result/duplicate semantics chưa hardened.
- Dừng auto-retry side-effect nếu capability không có idempotency/resume contract.
- Dừng legacy deletion cho đến khi R7 + streaming/UX parity hoàn tất.

# 14\. Decision log cần duyệt

| **ID** | **Decision**                     | **Proposal**                                                             | **Status**  |
| ------ | -------------------------------- | ------------------------------------------------------------------------ | ----------- |
| D-01   | Online Agent execution authority | SE AgentRuntime                                                          | Recommended |
| D-02   | Client AgentEngine               | LOCAL/OFFLINE compatibility only sau migration                           | Recommended |
| D-03   | client_id                        | Persistent unique installation UUID, not 'desktop-client' literal        | Recommended |
| D-04   | connection_id                    | Fresh per WebSocket generation                                           | Required    |
| D-05   | session_id                       | Domain conversation/task only; auth/transport session renamed/deprecated | Required    |
| D-06   | Execution affinity transport     | Explicit connection_id in chat/task request context                      | Required    |
| D-07   | Remote capability kind           | TOOL by default; SKILL/AGENT remain server-owned semantics               | Recommended |
| D-08   | HITL authority                   | Client-local final consent for client side effects                       | Required    |
| D-09   | Resume affinity                  | Same user + same client_id by default                                    | Recommended |
| D-10   | Resume external API              | Single execution.resume action; server keeps branch/merge internal       | Recommended |
| D-11   | Unsafe side-effect retry         | No automatic retry without idempotency/compensation declaration          | Required    |
| D-12   | Terminal duplicate behavior      | Replay cached outcome; never re-execute                                  | Required    |

# 15\. Evidence map / audit references

Các reference dưới đây đều thuộc commit c75e465443df42d610b5ae3cb2d121491c9afa3e.

| **File**                                                        | **Symbol/area**                                                                      | **Audit evidence**                                                                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| cl/src/core/client_runtime.py                                   | ClientRuntime.\__init_\_/start/\_handle_disconnect/\_activate_authenticated_identity | connection_id created once; no reconnect state machine; auth switch closes then start() reuses object identity.          |
| cl/src/core/realtime_client.py                                  | GatewayRealtimeClient.connect/send/send_result/\_receiver_loop/\_mark_disconnected   | single receiver invariant; transport session auto-generated; result wrapper; unbounded inbound deque risk; no reconnect. |
| cl/src/core/capability_runtime.py                               | build_registration/register/handle_message                                           | tool-centric registration; implementation_id bound to connection; dispatcher receives invoke/cancel.                     |
| cl/src/core/capability_dispatcher.py                            | dispatch/\_complete/cancel/fail_all/\_send_error                                     | direct callable execution; no HITL; duplicate/terminal semantics; unbounded terminal set.                                |
| cl/src/hitl/hitl_manager.py                                     | request_approval                                                                     | LOW/MEDIUM auto allow; HIGH/CRITICAL requires callback, fail closed if missing.                                          |
| cl/src/hitl/risk_analyzer.py                                    | evaluate_action                                                                      | local risk classification and danger patterns.                                                                           |
| se/src/domain/schemas/identity.py                               | Identity                                                                             | user_id authoritative; Identity.session_id currently auth/session namespace.                                             |
| se/src/runtimes/connection/contracts.py                         | ConnectionSnapshot                                                                   | connection_id + session_id + user_id; lifecycle states.                                                                  |
| se/src/transport/gateway/api/v1/events_router.py                | \_ensure_connection/websocket_endpoint                                               | connection.register binds authenticated user; capability register validates active connection.                           |
| se/src/runtimes/capability/registration.py                      | register/unregister_connection/\_validate_existing_implementations                   | owner binding; disconnect removes implementations; REMOVED implementation cannot be re-registered.                       |
| se/src/runtimes/capability/policy.py                            | CapabilityRoutingPolicy.select                                                       | same-connection CLIENT first; foreign client excluded; non-client continuation allowed.                                  |
| se/src/runtimes/capability/drivers/remote_client_driver.py      | execute                                                                              | hard connection equality; sends execution_id/invocation_id over realtime.                                                |
| se/src/runtimes/connection/realtime.py                          | invoke/handle_inbound/disconnect                                                     | correlated multiplexing; result currently resolves whole payload; error loses structured fields.                         |
| se/src/runtimes/connection/runtime.py                           | disconnect_connection                                                                | fails pending invocations and unregisters connection capabilities.                                                       |
| se/src/runtimes/workflow/runtime.py                             | \_execute_direct/\_execute_agent                                                     | production DIRECT/AGENT path; Agent context currently lacks connection_id.                                               |
| se/src/main.py                                                  | execute_registered_agent_task                                                        | multi-agent task enters AgentRuntime but context currently lacks connection_id.                                          |
| se/src/runtimes/agent/continuation.py                           | checkpoint_disconnect/reconnect/confirm_merge                                        | WAITING checkpoint; reconnect requires new connection_id + same user; stale branch protection.                           |
| se/src/runtimes/agent/persistence.py                            | resume_execution                                                                     | rehydrates context/pending tool calls; pending dict currently omits required iteration.                                  |
| se/src/runtimes/agent/contracts/tool.py                         | ToolExecutionRequest                                                                 | iteration required; connection_id optional.                                                                              |
| se/tests/architecture/test_phase5_9_exit_gate.py                | test_E2_resume...                                                                    | resume test checks dict fields but does not model_validate the pending ToolExecutionRequest.                             |
| se/tests/architecture/test_phase6_9_continuation.py             | continuation tests                                                                   | server-side checkpoint/reconnect races covered, but not real client resume transport.                                    |
| se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py | true WebSocket E2E                                                                   | real TCP path exists; production HTTP binding and reconnect/resume still separate gaps.                                  |
| .github/workflows/phase5-6-exit-gate.yml                        | Install dependencies/full pytest                                                     | ubuntu-latest + Python 3.12.10 + root requirements.                                                                      |
| requirements.txt                                                | pywin32/websocket dependencies                                                       | pywin32==312 breaks Ubuntu install; websocket package ownership needs cleanup.                                           |

## 15.1 Final reviewer recommendation

Không bắt đầu bằng việc xóa AgentEngine hoặc viết reconnect loop. Thứ tự đúng là: làm CI nhìn thấy regression (R0), khóa identity/authority semantics (R1), khóa wire contract (R2), đưa connection_id vào production execution path (R3), đưa HITL vào canonical local execution boundary (R4), harden invocation correctness (R5), rồi mới reconnect (R6) và durable resume (R7). Nếu đảo thứ tự, reconnect sẽ chỉ làm các contract sai chạy lại nhanh hơn và tăng nguy cơ duplicate side effect.

**END OF REVIEW BASELINE — NO IMPLEMENTATION HAS BEEN APPLIED**