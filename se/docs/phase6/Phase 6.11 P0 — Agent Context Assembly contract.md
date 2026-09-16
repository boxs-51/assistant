```python
# se/src/runtimes/agent/contracts/context_assembly.py

from __future__ import annotations

from typing imp
ort Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .inference import InferenceMessage, InferenceToolDefinition


class AgentSystemPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    source: str = "agent"
    version: str = "1"


class AgentCapabilityView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    name: str
    description: str = ""
    parameters: Mapping[str, Any] = Field(default_factory=dict)


class AgentContextAssembly(BaseModel):
    """
    Canonical semantic context assembled for one Agent iteration.

    AgentRuntime consumes the resulting snapshot but does not know how
    system prompt, capabilities or constraints were resolved.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: AgentSystemPrompt
    capabilities: tuple[AgentCapabilityView, ...] = ()
    constraints: Mapping[str, Any] = Field(default_factory=dict)
    messages: tuple[InferenceMessage, ...] = ()
    tools: tuple[InferenceToolDefinition, ...] = ()


class AgentSystemPromptProvider(Protocol):
    async def build(
        self,
        *,
        agent,
        context,
    ) -> AgentSystemPrompt:
        ...


class AgentCapabilityResolver(Protocol):
    async def resolve(
        self,
        *,
        agent_id: str,
        identity,
    ) -> Sequence[AgentCapabilityView]:
        ...


class AgentContextAssembler(Protocol):
    async def assemble(
        self,
        *,
        context,
        prior_messages: Sequence[Mapping[str, Any]],
    ) -> AgentContextAssembly:
        ...
```

---

## System prompt builder

```python
# se/src/runtimes/agent/system_prompt.py

from __future__ import annotations

from typing import Any

from .contracts.context_assembly import AgentSystemPrompt


class DefaultAgentSystemPromptProvider:
    """
    Canonical Agent system-prompt composition.

    AGENT.md is supplied through execution metadata rather than read from
    filesystem here. This keeps filesystem/config ownership outside the
    Agent runtime.
    """

    async def build(self, *, agent, context) -> AgentSystemPrompt:
        sections: list[str] = []

        metadata = context.metadata or {}

        constitution = metadata.get("constitution")
        if isinstance(constitution, str) and constitution.strip():
            sections.append(
                "[AGENT CONSTITUTION]\n"
                + constitution.strip()
            )

        if agent is not None:
            if agent.name.strip():
                sections.append(
                    "[AGENT IDENTITY]\n"
                    f"Name: {agent.name.strip()}"
                )

            if agent.goal.strip():
                sections.append(
                    "[AGENT GOAL]\n"
                    + agent.goal.strip()
                )

            if agent.instruction.strip():
                sections.append(
                    "[AGENT INSTRUCTION]\n"
                    + agent.instruction.strip()
                )

        constraints = context.limits
        sections.append(
            "[EXECUTION CONSTRAINTS]\n"
            f"- max_iterations: {constraints.max_iterations}\n"
            f"- max_tool_calls: {constraints.max_tool_calls}\n"
            f"- max_parallel_tools: {constraints.max_parallel_tools}\n"
            f"- timeout_seconds: {constraints.timeout_seconds}\n"
            f"- inference_timeout_seconds: "
            f"{constraints.inference_timeout_seconds}\n"
            f"- tool_timeout_seconds: "
            f"{constraints.tool_timeout_seconds}\n"
            f"- max_retry_attempts: {constraints.max_retry_attempts}"
        )

        return AgentSystemPrompt(
            content="\n\n".join(section for section in sections if section),
            source="agent+constitution",
            version="6.11",
        )
```

---

## Capability resolver

```python
# se/src/runtimes/agent/capabilities.py

from __future__ import annotations

from .contracts.context_assembly import AgentCapabilityView
from .contracts.inference import InferenceToolDefinition
from .contracts.policy import AgentToolPolicy, PolicyDecision


class RegistryAgentCapabilityResolver:
    def __init__(
        self,
        *,
        agent_registry,
        capability_registry,
        tool_policy: AgentToolPolicy,
    ) -> None:
        self._agents = agent_registry
        self._capabilities = capability_registry
        self._policy = tool_policy

    async def resolve(
        self,
        *,
        agent_id: str,
        identity,
    ) -> tuple[AgentCapabilityView, ...]:
        agent = self._agents.get(agent_id)
        if agent is None:
            return ()

        result: list[AgentCapabilityView] = []

        for capability_id in agent.tools or []:
            if not self._policy.is_visible(
                agent_id=agent_id,
                capability_id=capability_id,
            ):
                continue

            if self._policy.authorize(
                identity=identity,
                agent_id=agent_id,
                capability_id=capability_id,
            ) is not PolicyDecision.ALLOW:
                continue

            record = self._capabilities.get(capability_id)
            if record is None or not record.executable:
                continue

            definition = record.definition

            result.append(
                AgentCapabilityView(
                    capability_id=capability_id,
                    name=definition.name,
                    description=definition.description,
                    parameters=dict(definition.parameters or {}),
                )
            )

        return tuple(result)
```

---

# 10. ContextBuilderAdapter sau patch

Điểm quan trọng là bỏ business logic capability khỏi adapter.

Pseudo-flow mới:

```python
system_prompt = await self._system_prompt_provider.build(
    agent=context.agent,
    context=context,
)

capabilities = await self._capability_resolver.resolve(
    agent_id=context.agent_id,
    identity=context.identity,
)

history = ...

messages = [
    InferenceMessage(
        role="system",
        content=system_prompt.content,
    ),
    *history,
]
```

Sau đó:

```python
tools = tuple(
    InferenceToolDefinition(
        name=item.name,
        description=item.description,
        parameters=dict(item.parameters),
    )
    for item in capabilities
)
```

Và snapshot:

```python
return AgentContextSnapshot(
    execution_id=context.execution_id,
    iteration=request.iteration,
    messages=tuple(messages),
    tools=tools,
    token_estimate=...,
    metadata={
        **context.metadata,
        "agent_id": context.agent_id,
        "capability_ids": [
            item.capability_id
            for item in capabilities
        ],
        "system_prompt_source": system_prompt.source,
        "system_prompt_version": system_prompt.version,
    },
)
```

---

# 11. Một điểm cực kỳ quan trọng: không append system prompt mỗi iteration

Hiện adapter có logic:

```python
if instruction and not any(
    message.role == "system" for message in history
):
    history.insert(...)
```



Phase 6.11 phải chuyển sang invariant mạnh hơn:

```text
snapshot.messages[0].role == "system"
```

và:

```text
exactly_one(system_message)
```

Không được:

```text
iteration 1:
system
user

iteration 2:
system
user
assistant(tool)
tool
```

hay:

```text
system
system
user
```

Canonical transcript:

```text
SYSTEM
  ↓
USER
  ↓
ASSISTANT(tool_call)
  ↓
TOOL
  ↓
ASSISTANT(final)
```

System message được rebuild từ Agent Context, nhưng transcript không được nhân bản.

---

# 12. AgentRuntime thay đổi rất ít

Đây là chủ ý.

AgentRuntime hiện đã gọi:

```python
snapshot = await self._context_builder.build(...)
```

sau đó:

```python
InferenceRequest(
    messages=list(snapshot.messages),
    tools=list(snapshot.tools),
    ...
)
```



Vì vậy **không cần nhét system prompt vào AgentRuntime**.

Đây là boundary đúng:

```text
AgentRuntime
    │
    └── ContextBuilderPort
            │
            └── Phase 6.11 Assembly
```

AgentRuntime vẫn không biết:

```text
AGENT.md
Skill
CLIENT
SERVER
Capability implementation
```

---

# 13. Initial Agent hoàn chỉnh

Phase 6.11 cần định nghĩa “initial Agent” tối thiểu như:

```python
AgentDefinition(
    name="assistant",
    goal="Assist the user with the current task.",
    instruction=(
        "You are the primary assistant. "
        "Use available capabilities when they are required. "
        "Do not claim a tool result before receiving it."
    ),
    tools=[
        "client.echo",
    ],
)
```

và execution:

```text
AgentRegistry
     │
     ▼
AgentDefinition
     │
     ├── goal
     ├── instruction
     └── tools
           │
           ▼
AgentContextAssembly
     │
     ├── AGENT.md
     ├── system prompt
     ├── capabilities
     ├── constraints
     └── conversation
           │
           ▼
Inference #1
     │
     └── tool_call(client.echo)
              │
              ▼
       ToolExecutionPort
              │
              ▼
        RemoteClientDriver
              │
              ▼
        WebSocket Client
              │
              ▼
        CapabilityDispatcher
              │
              ▼
          tool result
              │
              ▼
         AgentRuntime
              │
              ▼
      Context rebuild
              │
              ▼
        Inference #2
              │
              ▼
          final answer
```

---

# 14. E2E test P0

Test bắt buộc phải assert **nội dung**, không chỉ assert execution completed.

Các invariant:

### I1 — System prompt

```python
assert first.messages[0].role == "system"
assert "AGENT CONSTITUTION" in first.messages[0].content
assert "test-agent" in first.messages[0].content
assert "Complete the requested task" in first.messages[0].content
```

### I2 — Capability visibility

Agent có:

```text
client.echo
client.secret
```

nhưng chỉ:

```text
client.echo
```

được expose.

```python
assert [tool.name for tool in first.tools] == ["client.echo"]
assert "client.secret" not in {
    tool.name for tool in first.tools
}
```

### I3 — Inference #1

Fake deterministic model:

```text
assistant.tool_calls = [
    client.echo({"value": "hello"})
]
```

### I4 — Remote execution

Phải thực sự đi:

```text
AgentRuntime
 → ToolExecutionPort
 → CapabilityRuntime
 → RemoteClientDriver
 → WebSocket
 → Client receiver
 → CapabilityDispatcher
 → real tool
 → WebSocket result
 → RemoteClientDriver
```

### I5 — Inference #2

Second inference phải nhận:

```text
system
user
assistant(tool_call)
tool(result)
```

và:

```python
assert second.messages[-1].role == "tool"
assert second.messages[-1].content == "hello"
```

### I6 — Final

```python
assert result.output == "remote tool completed: hello"
```

---

# 15. Test architecture

Không dùng `FakeSocket`.

Không fake `RealtimeMultiplexer`.

Không manually gọi:

```python
realtime.handle_inbound(...)
```

Test phải dựng:

```text
                   TEST PROCESS
                        │
             ┌──────────┴──────────┐
             │                     │
          Gateway                Client
             │                     │
        FastAPI WS          GatewayRealtimeClient
             │                     │
      ConnectionRuntime      receiver thread
             │                     │
      RealtimeMultiplexer    CapabilityRuntime
             │                     │
       RemoteClientDriver    CapabilityDispatcher
             │                     │
             └────── WebSocket ────┘
                        │
                    real tool
```

Agent model có thể deterministic fake vì mục tiêu test ở đây là **Context Assembly + real remote execution transport**, không phải test provider.

---

# 16. E2E assertions nên lưu trace

Tôi khuyến nghị test giữ:

```python
inference_trace = [
    {
        "iteration": 1,
        "messages": ...,
        "tools": ...,
    },
    {
        "iteration": 2,
        "messages": ...,
        "tools": ...,
    },
]
```

rồi assert:

```text
iteration 1
 ├─ system
 ├─ user
 └─ tools = [client.echo]

iteration 2
 ├─ system
 ├─ user
 ├─ assistant(tool_call)
 ├─ tool(result)
 └─ tools = [client.echo]
```

Đây mới là bằng chứng Agent Context thực sự hoạt động.

---

# 17. Không triển khai Skill Runtime

Phase 6.11 **không** làm:

```text
SkillRegistry
SkillResolver
SkillActivation
Skill lifecycle
Skill → Capability binding
Skill on-demand retrieval
```

Mặc dù repo hiện đã có `SkillDefinition` trong domain schema, đó chỉ là DTO/control-plane definition; chưa được biến thành Skill Runtime.

Phase 6.11 chỉ đảm bảo:

```text
Agent
 ├── System Prompt
 ├── Capabilities
 ├── Constraints
 └── Conversation
```

Skill sẽ bắt đầu từ Phase 6.12:

```text
Agent
   │
   ▼
SkillResolver
   │
   ▼
Skill Activation
   │
   ▼
Capability expansion
   │
   ▼
Context rebuild
```

---

# 18. P0 acceptance criteria

Phase 6.11 chỉ được coi là **PASS** nếu toàn bộ điều kiện sau đúng:

```text
[PASS] AgentDefinition resolves deterministically
[PASS] AGENT.md/constitution reaches Gateway Agent Context
[PASS] exactly one canonical system message
[PASS] goal is represented in system context
[PASS] instruction is represented in system context
[PASS] execution constraints are represented
[PASS] capability visibility is resolved centrally
[PASS] unauthorized capabilities are absent
[PASS] non-executable capabilities are absent
[PASS] InferenceRequest.tools matches capability view
[PASS] conversation is preserved
[PASS] tool result is preserved
[PASS] first inference receives system + capabilities
[PASS] remote tool call uses real WebSocket
[PASS] client dispatcher executes real capability
[PASS] result returns through WebSocket
[PASS] second inference receives tool result
[PASS] second inference sees same canonical system context
[PASS] final answer completes
[PASS] AgentRuntime has no CLIENT/SERVER branching
[PASS] no Skill Runtime code added
```

---

## Kết luận audit

Điểm quan trọng nhất tôi xác nhận lại sau khi đọc trực tiếp commit `3e03cce` là:

> **Không phải AgentRuntime chưa có context. Nó đã có context. Vấn đề là context hiện đang được assemble “inline” trong ****`ContextBuilderAdapter`****, khiến system prompt, capability visibility và conversation chưa trở thành một canonical Agent Context contract.**

`AgentRuntime` bản thân đã có boundary khá đúng: nó gọi `ContextBuilderPort`, lấy immutable snapshot rồi tạo `InferenceRequest`.

Do đó **Phase 6.11 không nên rewrite AgentRuntime**. P0 đúng là:

```text
AgentDefinition
       ↓
AgentContextAssembly
       ├── SystemPromptProvider
       ├── CapabilityResolver
       ├── ConstraintView
       └── Conversation
       ↓
ContextBuilderAdapter
       ↓
AgentContextSnapshot
       ↓
InferenceRequest
       ↓
Inference #1
       ↓
Remote Capability
       ↓
WebSocket
       ↓
Tool Result
       ↓
Context rebuild
       ↓
Inference #2
       ↓
Final
```

Đây là boundary phù hợp nhất với kiến trúc Phase 6 hiện tại và tránh biến Gateway thành một execution engine thứ hai.

