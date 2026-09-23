from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import update

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.storage.models.sql.agent.checkpoint import (
    AgentExecutionCheckpointRecord,
)
from se.src.infrastructure.storage.models.sql.agent.fork_admission import (
    AgentTaskForkAdmissionRecord,
)
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.contracts.context import (
    AgentExecutionContext,
    ExecutionContextSeedError,
)
from se.src.runtimes.agent.contracts.context_builder import (
    AgentContextHistoryMode,
    AgentContextRequest,
)
from se.src.runtimes.agent.fork_planning import ForkPlanRejected
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _seed_source,
    _setup,
)


def _identity():
    return Identity(user_id="user-r8-d", auth_type="api_key", scopes={"*"})


def _agent():
    return AgentDefinition(
        name="agent-r8-d",
        goal="R8-E",
        instruction="canonical-system",
    )


def _store(sessions):
    return DurableAgentStore(lambda: _Uow(sessions))


@pytest.mark.asyncio
async def test_r8_e_bootstrap_is_restart_safe_transport_free_and_exact(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r8_e_restart.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r8-e-restart"
        )
        admission = await service.consume_fork_plan(source["plan"])
        first = await _store(sessions).prepare_fork_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )
        second = await _store(sessions).prepare_fork_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )

        assert first.expected_execution_revision == 1
        assert first.runtime_seed_fingerprint == second.runtime_seed_fingerprint
        assert first.context.branch_base_transcript == second.context.branch_base_transcript
        assert [m["content"] for m in first.context.branch_base_transcript] == [
            "base", "fork-local"
        ]
        assert first.context.deadline is None
        assert first.context.remaining_active_budget_seconds == 30.0
        assert first.context.iteration == 0
        assert first.context.tool_calls_used == 0
        assert first.context.retry_attempts_used == 0
        assert first.context.usage.total_tokens == 0
        assert first.context.connection_id is None
        assert first.context.metadata["safe"] == "task-r8-e-restart"
        for key in (
            "client_id", "connection_id", "origin_client_id",
            "origin_connection_id", "routing_connection_id",
        ):
            assert key not in first.context.metadata
        assert first.context.cancellation_event is not second.context.cancellation_event
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_e_source_progress_after_fork_cannot_move_branch_base(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r8_e_source_progress.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r8-e-progress"
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        before = await store.prepare_fork_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )
        async with _Uow(sessions) as uow:
            await uow.agents.update_execution(
                source["source_execution_id"],
                {
                    "state": "RUNNING", "revision": 3,
                    "current_checkpoint_id": None,
                    "context_state": {
                        "limits": AgentExecutionLimits(
                            timeout_seconds=999.0
                        ).model_dump(mode="json"),
                        "metadata": {"later": True},
                    },
                },
            )
            await uow.commit()
        after = await store.prepare_fork_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )
        assert after.context.branch_base_transcript == before.context.branch_base_transcript
        assert after.runtime_seed_fingerprint == before.runtime_seed_fingerprint
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_e_sibling_overlays_never_merge(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r8_e_siblings.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner,
            task_id="task-r8-e-siblings", fork_request_id="fork-b2",
        )
        b2 = await service.consume_fork_plan(source["plan"])
        plan_b3 = await planner.build_fork_plan(
            fork_request_id="fork-b3", task_id=source["task_id"],
            source_branch_id=source["source_branch_id"],
            source_execution_id=source["source_execution_id"],
            source_checkpoint_id=source["checkpoint_id"],
            target_user_id="user-r8-d",
            overlay_messages=({"role": "user", "content": "only-b3"},),
        )
        b3 = await service.consume_fork_plan(plan_b3)
        store = _store(sessions)
        c2 = (await store.prepare_fork_execution_context(
            b2.execution_id, identity=_identity(), agent=_agent()
        )).context.branch_base_transcript
        c3 = (await store.prepare_fork_execution_context(
            b3.execution_id, identity=_identity(), agent=_agent()
        )).context.branch_base_transcript
        assert [m["content"] for m in c2] == ["base", "fork-local"]
        assert [m["content"] for m in c3] == ["base", "only-b3"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "error_code"),
    [
        ("seed", "FORK_RUNTIME_SEED_INVALID"),
        ("request", "FORK_RUNTIME_CONTEXT_CONFLICT"),
        ("budget", "FORK_RUNTIME_CONTEXT_CONFLICT"),
        ("context", "FORK_BRANCH_CONTEXT_CHANGED"),
        ("base", "FORK_BASE_TRANSCRIPT_CHANGED"),
    ],
)
async def test_r8_e_tamper_fails_closed_before_runtime(tmp_path, kind, error_code):
    engine, sessions, service, planner = await _setup(
        tmp_path, name=f"r8_e_tamper_{kind}.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner,
            task_id=f"task-r8-e-tamper-{kind}",
        )
        admission = await service.consume_fork_plan(source["plan"])
        async with _Uow(sessions) as uow:
            if kind == "seed":
                await uow.session.execute(
                    update(AgentTaskForkAdmissionRecord)
                    .where(AgentTaskForkAdmissionRecord.execution_id == admission.execution_id)
                    .values(runtime_seed_fingerprint="0" * 64)
                )
            elif kind == "request":
                await uow.agents.update_execution(
                    admission.execution_id, {"request": {"prompt": "tampered"}}
                )
            elif kind == "budget":
                await uow.agents.update_execution(
                    admission.execution_id, {"remaining_active_budget_seconds": 29.0}
                )
            elif kind == "context":
                changed = await uow.agents.compare_and_set_task_branch_context(
                    admission.branch_id, 0,
                    [{"role": "user", "content": "changed"}],
                )
                assert changed is not None
            elif kind == "base":
                await uow.session.execute(
                    update(AgentExecutionCheckpointRecord)
                    .where(AgentExecutionCheckpointRecord.checkpoint_id == source["checkpoint_id"])
                    .values(transcript_snapshot=[{"role": "user", "content": "changed-base"}])
                )
            await uow.commit()
        with pytest.raises(ExecutionConflictError, match=error_code):
            await _store(sessions).prepare_fork_execution_context(
                admission.execution_id, identity=_identity(), agent=_agent()
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_e_overlay_role_policy_is_user_only(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r8_e_overlay.sqlite"
    )
    try:
        source = await _seed_source(
            sessions, service, planner, task_id="task-r8-e-overlay"
        )
        for role in ("system", "assistant", "tool"):
            message = {"role": role, "content": "unsafe"}
            if role == "tool":
                message["tool_call_id"] = "fake"
            with pytest.raises(ForkPlanRejected) as exc:
                await planner.build_fork_plan(
                    fork_request_id=f"fork-{role}", task_id=source["task_id"],
                    source_branch_id=source["source_branch_id"],
                    source_execution_id=source["source_execution_id"],
                    source_checkpoint_id=source["checkpoint_id"],
                    target_user_id="user-r8-d", overlay_messages=(message,),
                )
            assert exc.value.code == "FORK_OVERLAY_ROLE_INVALID"
    finally:
        await engine.dispose()


def test_r8_e_fork_and_resume_seed_authority_is_mutually_exclusive():
    context = AgentExecutionContext.create(
        execution_id="exec", agent_id="agent-r8-d", session_id="session",
        correlation_id="corr", identity=_identity(),
        limits=AgentExecutionLimits(), branch_base_transcript=[],
        branch_runtime_seed_fingerprint="f" * 64, activate_budget=False,
    )
    context.resume_revision = 2
    with pytest.raises(ExecutionContextSeedError):
        context.validate_context_seed()


class _SessionTrap:
    def __init__(self):
        self.calls = 0

    async def load_context(self, session_id, identity):
        self.calls += 1
        return SimpleNamespace(session=SimpleNamespace(messages=[]))


class _ToolPolicy:
    def is_visible(self, **kwargs):
        return True

    def authorize(self, **kwargs):
        return True


@pytest.mark.asyncio
async def test_r8_e_explicit_empty_history_never_loads_session_or_readds_input():
    trap = _SessionTrap()
    adapter = ContextBuilderAdapter(
        trap, SimpleNamespace(registry=None), _ToolPolicy()
    )
    context = AgentExecutionContext.create(
        execution_id="fork-empty", agent_id="agent-r8-d",
        session_id="session", correlation_id="corr", identity=_identity(),
        limits=AgentExecutionLimits(), agent=_agent(),
        input={"prompt": "source-prompt-must-not-be-readded"},
        branch_base_transcript=[], branch_runtime_seed_fingerprint="a" * 64,
        remaining_active_budget_seconds=30.0, activate_budget=False,
    )
    snapshot = await adapter.build(
        context,
        AgentContextRequest(
            execution_id=context.execution_id, iteration=1,
            prior_messages=[], history_mode=AgentContextHistoryMode.EXPLICIT,
        ),
    )
    assert trap.calls == 0
    assert [message.role for message in snapshot.messages] == ["system"]
    assert all(
        message.content != "source-prompt-must-not-be-readded"
        for message in snapshot.messages
    )
