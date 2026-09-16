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