# se/src/runtimes/agent/contracts/context_assembly.py

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

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