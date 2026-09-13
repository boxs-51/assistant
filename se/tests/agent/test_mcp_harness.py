from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.mcp_driver import McpCapabilityDriver
from src.runtimes.capability.runtime import CapabilityRuntime

from .harness import AgentToolLoopHarness, ExecutionTrace, FakeLLM, make_identity


@dataclass
class FakeTextContent:
    text: str


@dataclass
class FakeMcpResult:
    content: list[FakeTextContent]


class FakeMcpSession:
    def __init__(self, response: str = "repo-a repo-b") -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, *, arguments: dict):
        self.calls.append((name, dict(arguments)))
        return FakeMcpResult([FakeTextContent(self.response)])


class FakeMcpManager:
    def __init__(self, session: FakeMcpSession) -> None:
        self.session = session

    def get_raw_session(self, server_name: str):
        return self.session if server_name == "github" else None


@pytest.mark.asyncio
async def test_agent_tool_loop_executes_mcp_capability_through_real_driver():
    session = FakeMcpSession("repo-a\nrepo-b")
    runtime = CapabilityRuntime()
    definition = CapabilityDefinition(
        id="github:search",
        name="github:search",
        description="Search repositories",
        source="MCP",
        execution_kind="MCP",
        input_schema={"type": "object"},
        metadata={
            "mcp_server": "github",
            "mcp_tool_name": "search_repositories",
        },
    )
    runtime.register_capability(McpCapabilityDriver(definition, FakeMcpManager(session)))
    trace = ExecutionTrace()
    llm = FakeLLM([
        {
            "id": "call-mcp",
            "name": "github:search",
            "arguments": {"query": "asyncio"},
        },
        "Found repo-a and repo-b.",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
        trace=trace,
    ).run("Search GitHub for asyncio.")

    assert result == "Found repo-a and repo-b."
    assert session.calls == [("search_repositories", {"query": "asyncio"})]
    assert "tool.execution.completed" in trace.names


@pytest.mark.asyncio
async def test_mcp_capability_unavailable_is_reported_without_crashing_agent():
    runtime = CapabilityRuntime()
    definition = CapabilityDefinition(
        id="github:search",
        name="github:search",
        description="Search repositories",
        source="MCP",
        execution_kind="MCP",
        metadata={"mcp_server": "github", "mcp_tool_name": "search"},
    )
    runtime.register_capability(
        McpCapabilityDriver(
            definition,
            FakeMcpManager(session=None),
        )
    )
    llm = FakeLLM([
        {"id": "call-mcp-down", "name": "github:search", "arguments": {"query": "x"}},
        "GitHub is unavailable.",
    ])

    result = await AgentToolLoopHarness(
        llm=llm,
        capability_runtime=runtime,
        identity=make_identity(),
    ).run("Search GitHub.")

    assert result == "GitHub is unavailable."
