# Agent Tool Test Harness — Phase v1

This phase intentionally stays in `tests/` because the current repository does
not yet contain a production `AgentRuntime` inference/tool loop.  The harness
uses the real `CapabilityRuntime`, `CapabilityRegistry`, authorization policy,
`PythonCapabilityDriver`, and `McpCapabilityDriver` contracts already present in
the repository.

The harness covers:

- deterministic scripted LLM responses;
- local Python capability execution;
- MCP capability execution through the real MCP driver contract;
- execution tracing;
- permission denial;
- retryable tool failure;
- max-iteration protection;
- parallel tool calls;
- tool-not-found and MCP-unavailable behavior.

Focused run:

```text
py -m pytest -q tests/agent
```

The acceptance boundary is the agent/tool protocol itself.  Once a production
`AgentRuntime` inference loop is introduced, these fixtures can be injected
directly into that runtime without changing the capability execution contract.