from __future__ import annotations

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.context_assembly import (
    AgentContextAssembly,
    AgentSystemPrompt,
)
from se.src.runtimes.agent.contracts.context_builder import (
    AgentContextHistoryMode,
    AgentContextRequest,
)
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceToolDefinition,
)
from se.src.runtimes.agent.contracts.selection import (
    CapabilitySelectionCandidate,
    CapabilitySelectionContext,
)
from se.src.runtimes.agent.contracts.skills import (
    ActiveSkill,
    ActiveSkillSet,
    SkillActivationSource,
    SkillDescriptor,
)
from se.src.runtimes.agent.selection import DeterministicCapabilitySelector
from se.src.runtimes.capability.contracts.skill_manifest import SkillCapabilityHint


def _candidate(capability_id: str, description: str = "opaque operation"):
    return CapabilitySelectionCandidate(
        capability_id=capability_id,
        name=capability_id,
        description=description,
    )


def _selection_context(
    *,
    text: str = "hello",
    candidates=(),
    messages=None,
    active_skill_set: ActiveSkillSet | None = None,
    explicit=(),
):
    if messages is None:
        messages = (InferenceMessage(role="user", content=text),)
    return CapabilitySelectionContext(
        owner_user_id="user-1",
        agent_id="dcs2-agent",
        execution_id="exec-dcs2",
        iteration=2,
        messages=tuple(messages),
        eligible_capabilities=tuple(candidates),
        explicit_requested_capability_ids=tuple(explicit),
        active_skill_set=active_skill_set or ActiveSkillSet(),
    )


def test_dcs2_group_identity_is_deterministic_metadata_only():
    selector = DeterministicCapabilitySelector()

    result = selector.select(
        _selection_context(
            text="please read the file",
            candidates=(
                _candidate("file.read", "Read a file by path"),
                _candidate("terminal.run", "Execute a terminal command"),
            ),
        )
    )

    assert result.working_set.visible_capability_ids == ("file.read",)
    assert result.working_set.active_groups == ("file",)
    assert result.working_set.reason == "DCS2_BOUNDED_RANKED_SELECTION"


def test_dcs2_relevant_skill_hint_ranks_only_already_eligible_capability():
    hinted = ActiveSkill(
        descriptor=SkillDescriptor(
            skill_id="editor-skill",
            version="2.0",
            name="editor-skill",
            capability_hints=(
                SkillCapabilityHint(
                    capability_id="file.write",
                    purpose="revise document",
                ),
                SkillCapabilityHint(
                    capability_id="dangerous.exfiltrate",
                    purpose="revise document",
                ),
            ),
        ),
        source=SkillActivationSource.ASSIGNED,
    )
    selector = DeterministicCapabilitySelector()

    result = selector.select(
        _selection_context(
            text="revise document",
            candidates=(
                _candidate("file.write", "Persist bytes"),
                _candidate("terminal.run", "Execute command"),
            ),
            active_skill_set=ActiveSkillSet(skills=(hinted,)),
        )
    )

    assert result.working_set.visible_capability_ids == ("file.write",)
    assert "dangerous.exfiltrate" not in result.working_set.visible_capability_ids
    assert "skill-hint:file.write" in result.working_set.provenance


def test_dcs2_visible_set_is_bounded_to_eight_and_ties_are_stable():
    candidates = tuple(
        _candidate(f"group{i}.run")
        for i in range(10)
    )
    explicit = tuple(item.capability_id for item in candidates)
    selector = DeterministicCapabilitySelector()

    first = selector.select(
        _selection_context(candidates=candidates, explicit=explicit)
    )
    second = selector.select(
        _selection_context(candidates=candidates, explicit=explicit)
    )

    expected = tuple(item.capability_id for item in candidates[:8])
    assert first == second
    assert first.working_set.visible_capability_ids == expected
    assert len(first.working_set.visible_capability_ids) == 8


def test_dcs2_progressive_expansion_is_same_group_and_eligible_only():
    selector = DeterministicCapabilitySelector()
    messages = (
        InferenceMessage(role="user", content="continue"),
        InferenceMessage(
            role="tool",
            name="file.read",
            tool_call_id="call-1",
            content={"ok": True},
        ),
    )

    result = selector.select(
        _selection_context(
            candidates=(
                _candidate("file.read", "Read bytes"),
                _candidate("file.write", "Write bytes"),
                _candidate("terminal.run", "Execute command"),
            ),
            messages=messages,
        )
    )

    assert result.working_set.visible_capability_ids == (
        "file.read",
        "file.write",
    )
    assert result.working_set.active_groups == ("file",)
    assert "terminal.run" not in result.working_set.visible_capability_ids
    assert "group-expand:file:file.write" in result.working_set.provenance


def test_dcs2_ambiguity_never_falls_back_to_all_tools():
    selector = DeterministicCapabilitySelector()
    candidates = tuple(
        _candidate(f"opaque{i}.invoke")
        for i in range(12)
    )

    result = selector.select(
        _selection_context(
            text="hello there",
            candidates=candidates,
        )
    )

    assert result.working_set.visible_capability_ids == ()
    assert result.working_set.active_groups == ()
    assert result.working_set.reason == "DCS2_ZERO_TOOL_FAST_PATH"


def _execution_context() -> AgentExecutionContext:
    agent = AgentDefinition(
        name="dcs2-agent",
        goal="test token estimation",
        instruction="base instruction",
        tools=[],
        skills=[],
    )
    context = AgentExecutionContext.create(
        execution_id="exec-dcs2",
        agent_id=agent.name,
        session_id="session-dcs2",
        correlation_id="corr-dcs2",
        identity=Identity(user_id="user-1", auth_type="jwt"),
        limits=AgentExecutionLimits(),
        agent=agent,
        input={"prompt": "hello"},
    )
    context.iteration = 1
    return context


class _Assembler:
    def __init__(self, tools):
        self._tools = tuple(tools)

    async def assemble(self, *, context, prior_messages):
        return AgentContextAssembly(
            system_prompt=AgentSystemPrompt(
                content="SYSTEM",
                source="dcs2-test",
                version="1",
            ),
            messages=(
                InferenceMessage(role="system", content="SYSTEM"),
                InferenceMessage(role="user", content="hello"),
            ),
            tools=self._tools,
        )


async def _build_snapshot(tools):
    adapter = ContextBuilderAdapter(
        context_runtime=None,
        capability_runtime=None,
        tool_policy=object(),
        context_assembler=_Assembler(tools),
    )
    context = _execution_context()
    request = AgentContextRequest(
        execution_id=context.execution_id,
        iteration=1,
        history_mode=AgentContextHistoryMode.EXPLICIT,
        prior_messages=({"role": "user", "content": "hello"},),
    )
    return await adapter.build(context, request)


@pytest.mark.asyncio
async def test_dcs2_token_estimate_accounts_for_final_model_visible_tool_schema():
    small = InferenceToolDefinition(
        name="file.read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    large = InferenceToolDefinition(
        name="file.read",
        description="Read a file with a deliberately larger selected schema",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute logical path"},
                "encoding": {
                    "type": "string",
                    "enum": ["utf-8", "utf-16", "latin-1"],
                    "description": "Requested decoding",
                },
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    zero_snapshot = await _build_snapshot(())
    small_snapshot = await _build_snapshot((small,))
    large_snapshot = await _build_snapshot((large,))
    repeat_large = await _build_snapshot((large,))

    assert zero_snapshot.tools == ()
    assert zero_snapshot.token_estimate >= 0
    assert small_snapshot.token_estimate > zero_snapshot.token_estimate
    assert large_snapshot.token_estimate > small_snapshot.token_estimate
    assert repeat_large.token_estimate == large_snapshot.token_estimate
