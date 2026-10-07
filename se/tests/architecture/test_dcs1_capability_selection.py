from __future__ import annotations

from pathlib import Path

import faulthandler
import sys

import pytest
from _pytest.terminal import TerminalReporter


# Temporary DCS-1 CI diagnostic instrumentation. This stays inside the
# released focused-test path and does not change production behavior.
_DCS1_ORIGINAL_LOGREPORT = TerminalReporter.pytest_runtest_logreport


def _dcs1_diagnostic_logreport(self, report):
    if report.failed:
        sys.stderr.write(
            f"\nDCS1_DIAG_FAILED when={report.when} nodeid={report.nodeid}\n"
        )
        sys.stderr.flush()
    return _DCS1_ORIGINAL_LOGREPORT(self, report)


TerminalReporter.pytest_runtest_logreport = _dcs1_diagnostic_logreport
faulthandler.enable()
faulthandler.dump_traceback_later(300, repeat=True)

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.context_assembly import (
    AgentCapabilityView,
    AgentSkillView,
    AgentSystemPrompt,
)
from se.src.runtimes.agent.contracts.context_builder import (
    AgentContextSnapshot,
)
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceResponse,
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
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.selection import (
    CapabilitySelectionViolationError,
    DeterministicCapabilitySelector,
    ensure_selected_tool_calls,
    select_active_assigned_skills,
)
from se.src.runtimes.capability.contracts.skill_manifest import (
    SkillActivationDescriptor,
    SkillActivationMode,
    SkillCapabilityHint,
)


class _PromptProvider:
    async def build(self, *, agent, context):
        return AgentSystemPrompt(content="BASE PROMPT", source="test", version="1")


class _CapabilityResolver:
    async def resolve(self, *, agent_id, identity):
        return (
            AgentCapabilityView(
                capability_id="file.read",
                name="file.read",
                description="Read a file by path",
                parameters={"type": "object"},
            ),
            AgentCapabilityView(
                capability_id="terminal.run",
                name="terminal.run",
                description="Execute a terminal command",
                parameters={"type": "object"},
            ),
        )


class _SkillResolver:
    def __init__(self):
        self._assigned = AgentSkillView(
            skill_id="review-skill",
            name="review-skill",
            description="Review procedure",
            instruction="ASSIGNED REVIEW BODY",
            version="2.0",
        )

    async def resolve(self, *, agent_id, identity):
        return (self._assigned,)

    async def list_descriptors(self, *, identity):
        return (
            SkillDescriptor(
                skill_id="review-skill",
                version="2.0",
                name="review-skill",
                description="Review procedure",
                activation=SkillActivationDescriptor(
                    mode=SkillActivationMode.ALWAYS_ON,
                ),
                capability_hints=(
                    SkillCapabilityHint(
                        capability_id="terminal.run",
                        purpose="Advisory only",
                    ),
                ),
                provenance="SERVER_SKILL_MANIFEST",
            ),
            SkillDescriptor(
                skill_id="unassigned-skill",
                version="2.0",
                name="unassigned-skill",
                description="Must not activate",
                provenance="SERVER_SKILL_MANIFEST",
            ),
        )

    async def list_available(self, *, identity):
        return (
            self._assigned,
            AgentSkillView(
                skill_id="unassigned-skill",
                name="unassigned-skill",
                description="Must not leak",
                instruction="UNASSIGNED SECRET",
                version="2.0",
            ),
        )


def _context(*, skills=None):
    agent = AgentDefinition(
        name="dcs-agent",
        goal="Test DCS",
        instruction="Use only relevant tools.",
        tools=["file.read", "terminal.run"],
        skills=list(skills or []),
    )
    context = AgentExecutionContext.create(
        execution_id="exec-dcs",
        agent_id=agent.name,
        session_id="session-dcs",
        correlation_id="corr-dcs",
        identity=Identity(user_id="user-1", auth_type="jwt"),
        limits=AgentExecutionLimits(
            max_iterations=2,
            timeout_seconds=30,
            iteration_timeout_seconds=20,
            inference_timeout_seconds=10,
        ),
        agent=agent,
        input={"prompt": "hello"},
    )
    context.iteration = 1
    return context


def _selection_context(
    text: str,
    *,
    active_skill_set: ActiveSkillSet | None = None,
    explicit=(),
):
    return CapabilitySelectionContext(
        owner_user_id="user-1",
        agent_id="dcs-agent",
        execution_id="exec-dcs",
        iteration=1,
        messages=(InferenceMessage(role="user", content=text),),
        eligible_capabilities=(
            CapabilitySelectionCandidate(
                capability_id="file.read",
                name="file.read",
                description="Read a file by path",
            ),
            CapabilitySelectionCandidate(
                capability_id="terminal.run",
                name="terminal.run",
                description="Execute a terminal command",
            ),
        ),
        explicit_requested_capability_ids=tuple(explicit),
        active_skill_set=active_skill_set or ActiveSkillSet(),
    )


def test_dcs1_zero_tool_fast_path_and_deterministic_subset():
    selector = DeterministicCapabilitySelector()

    zero = selector.select(_selection_context("hello there"))
    first = selector.select(_selection_context("please read the file"))
    second = selector.select(_selection_context("please read the file"))

    assert zero.working_set.visible_capability_ids == ()
    assert first == second
    assert first.working_set.visible_capability_ids == ("file.read",)
    assert set(first.working_set.visible_capability_ids) < {
        "file.read",
        "terminal.run",
    }


def test_dcs1_explicit_unknown_and_skill_hint_cannot_create_tool_authority():
    hinted_skill = ActiveSkill(
        descriptor=SkillDescriptor(
            skill_id="review-skill",
            version="2.0",
            name="review-skill",
            capability_hints=(
                SkillCapabilityHint(
                    capability_id="dangerous.unavailable",
                    purpose="Advisory only",
                ),
            ),
        ),
        source=SkillActivationSource.ASSIGNED,
    )
    selector = DeterministicCapabilitySelector()

    result = selector.select(
        _selection_context(
            "hello",
            active_skill_set=ActiveSkillSet(skills=(hinted_skill,)),
            explicit=("dangerous.unavailable",),
        )
    )

    assert result.working_set.visible_capability_ids == ()
    assert result.active_skill_set.skills[0].descriptor.capability_hints[0].capability_id == (
        "dangerous.unavailable"
    )


def test_dcs1_v2_skill_activation_is_relevance_gated():
    candidate = ActiveSkill(
        descriptor=SkillDescriptor(
            skill_id="research-skill",
            version="2.0",
            name="research-skill",
            activation=SkillActivationDescriptor(
                mode=SkillActivationMode.AUTO_ELIGIBLE,
                keywords=("research", "sources"),
            ),
        ),
        source=SkillActivationSource.ASSIGNED,
    )
    assigned = ActiveSkillSet(skills=(candidate,))

    irrelevant = select_active_assigned_skills(
        assigned,
        (InferenceMessage(role="user", content="say hello"),),
    )
    relevant = select_active_assigned_skills(
        assigned,
        (InferenceMessage(role="user", content="research these sources"),),
    )

    assert irrelevant == ActiveSkillSet()
    assert [item.descriptor.skill_id for item in relevant.skills] == [
        "research-skill"
    ]
    assert relevant.skills[0].source is SkillActivationSource.AUTO_MATCH


@pytest.mark.asyncio
async def test_dcs1_assembler_selects_tools_and_only_assigned_skill_body():
    context = _context(skills=["review-skill"])
    assembler = DefaultAgentContextAssembler(
        _PromptProvider(),
        _CapabilityResolver(),
        _SkillResolver(),
        selector=DeterministicCapabilitySelector(),
    )

    assembled = await assembler.assemble(
        context=context,
        prior_messages=[{"role": "user", "content": "please read the file"}],
    )

    assert [item.name for item in assembled.tools] == ["file.read"]
    assert assembled.working_set is not None
    assert assembled.working_set.visible_capability_ids == ("file.read",)
    assert [
        item.descriptor.skill_id for item in assembled.active_skill_set.skills
    ] == ["review-skill"]
    assert "ASSIGNED REVIEW BODY" in assembled.system_prompt.content
    assert "UNASSIGNED SECRET" not in assembled.system_prompt.content
    # The assigned Skill hints terminal.run, but hints are not DCS-1 grants.
    assert "terminal.run" not in assembled.working_set.visible_capability_ids


@pytest.mark.asyncio
async def test_dcs1_empty_assigned_skills_stays_empty():
    context = _context(skills=[])
    assembler = DefaultAgentContextAssembler(
        _PromptProvider(),
        _CapabilityResolver(),
        _SkillResolver(),
        selector=DeterministicCapabilitySelector(),
    )

    assembled = await assembler.assemble(
        context=context,
        prior_messages=[{"role": "user", "content": "hello"}],
    )

    assert assembled.active_skill_set == ActiveSkillSet()
    assert "ASSIGNED REVIEW BODY" not in assembled.system_prompt.content


@pytest.mark.asyncio
async def test_legacy_assembler_without_selector_preserves_full_eligible_projection():
    context = _context()
    assembler = DefaultAgentContextAssembler(
        _PromptProvider(),
        _CapabilityResolver(),
    )

    assembled = await assembler.assemble(
        context=context,
        prior_messages=[{"role": "user", "content": "hello"}],
    )

    assert [item.name for item in assembled.tools] == [
        "file.read",
        "terminal.run",
    ]
    assert assembled.working_set is not None
    assert assembled.working_set.reason == "LEGACY_FULL_ELIGIBLE"


@pytest.mark.asyncio
async def test_legacy_no_selector_preserves_assigned_skill_preload():
    context = _context(skills=["review-skill"])
    assembler = DefaultAgentContextAssembler(
        _PromptProvider(),
        _CapabilityResolver(),
        _SkillResolver(),
    )

    assembled = await assembler.assemble(
        context=context,
        prior_messages=[{"role": "user", "content": "hello"}],
    )

    assert "ASSIGNED REVIEW BODY" in assembled.system_prompt.content
    assert [
        item.descriptor.skill_id for item in assembled.active_skill_set.skills
    ] == ["review-skill"]
    assert [item.name for item in assembled.tools] == [
        "file.read",
        "terminal.run",
    ]


def test_non_selected_model_tool_call_fails_closed():
    with pytest.raises(CapabilitySelectionViolationError) as exc_info:
        ensure_selected_tool_calls(
            ["file.read"],
            ["terminal.run"],
        )

    assert exc_info.value.code == "DCS_TOOL_NOT_SELECTED"
    assert exc_info.value.capability_ids == ("terminal.run",)


@pytest.mark.asyncio
async def test_agent_runtime_rejects_non_selected_tool_before_executor_dispatch():
    class _Builder:
        async def build(self, context, request):
            return AgentContextSnapshot(
                execution_id=context.execution_id,
                iteration=request.iteration,
                messages=(InferenceMessage(role="user", content="hello"),),
                tools=(
                    {
                        "name": "file.read",
                        "description": "Read file",
                        "parameters": {"type": "object"},
                    },
                ),
            )

    class _Inference:
        async def complete(self, request):
            return InferenceResponse(
                request_id=request.request_id,
                execution_id=request.execution_id,
                iteration=request.iteration,
                message=InferenceMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        {
                            "id": "call-1",
                            "name": "terminal.run",
                            "arguments": {"command": "echo forbidden"},
                        },
                    ),
                ),
                provider="test",
                model="test",
            )

    class _Executor:
        calls = 0

        async def execute_many(self, context, requests, *, max_parallel):
            self.calls += 1
            raise AssertionError("non-selected Tool reached executor")

    executor = _Executor()
    runtime = AgentRuntime(
        context_builder=_Builder(),
        inference=_Inference(),
        tool_execution=executor,
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    context = _context()

    result = await runtime._execute_loop(context)

    assert result.error_code == "DCS_TOOL_NOT_SELECTED"
    assert executor.calls == 0


def test_production_wires_selector_and_guard_precedes_tool_request_creation():
    main_source = Path("se/src/main.py").read_text(encoding="utf-8")
    runtime_source = Path("se/src/runtimes/agent/runtime.py").read_text(
        encoding="utf-8"
    )

    assert "selector=DeterministicCapabilitySelector()" in main_source
    guard_at = runtime_source.index("ensure_selected_tool_calls(")
    request_at = runtime_source.index("tool_requests = [", guard_at)
    assert guard_at < request_at
