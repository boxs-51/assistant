from __future__ import annotations

import asyncio

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ForkControlError,
)
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _seed_source,
    _setup,
)


def _identity() -> Identity:
    return Identity(
        user_id="user-r8-d",
        auth_type="api_key",
        scopes={"*"},
    )


def _agent() -> AgentDefinition:
    return AgentDefinition(
        name="agent-r8-d",
        goal="R8-F",
        instruction="canonical-system",
    )


def _store(sessions) -> DurableAgentStore:
    return DurableAgentStore(lambda: _Uow(sessions))


@pytest.mark.asyncio
async def test_r8_f_replay_survives_source_progress(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_replay_progress.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-replay-progress",
            fork_request_id="fork-r8-f-replay-progress",
        )
        admission = await service.consume_fork_plan(source["plan"])

        async with _Uow(sessions) as uow:
            await uow.agents.update_execution(
                source["source_execution_id"],
                {
                    "state": "RUNNING",
                    "revision": 3,
                    "current_checkpoint_id": None,
                },
            )
            await uow.commit()

        replay = await _store(sessions).load_fork_replay(
            task_id=source["task_id"],
            fork_request_id=source["plan"].fork_request_id,
            source_branch_id=source["source_branch_id"],
            source_execution_id=source["source_execution_id"],
            source_checkpoint_id=source["checkpoint_id"],
            target_user_id=_identity().user_id,
            overlay_messages=source["plan"].overlay_messages,
        )

        assert replay is not None
        assert replay.admission.branch_id == admission.branch_id
        assert replay.admission.execution_id == admission.execution_id
        assert replay.preactivation is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_replay_rejects_semantic_or_principal_change(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_replay_conflict.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-replay-conflict",
            fork_request_id="fork-r8-f-replay-conflict",
        )
        await service.consume_fork_plan(source["plan"])
        store = _store(sessions)

        with pytest.raises(ForkControlError) as overlay_exc:
            await store.load_fork_replay(
                task_id=source["task_id"],
                fork_request_id=source["plan"].fork_request_id,
                source_branch_id=source["source_branch_id"],
                source_execution_id=source["source_execution_id"],
                source_checkpoint_id=source["checkpoint_id"],
                target_user_id=_identity().user_id,
                overlay_messages=({"role": "user", "content": "different"},),
            )
        assert overlay_exc.value.code == "FORK_REQUEST_SEMANTIC_CONFLICT"

        with pytest.raises(ForkControlError) as owner_exc:
            await store.load_fork_replay(
                task_id=source["task_id"],
                fork_request_id=source["plan"].fork_request_id,
                source_branch_id=source["source_branch_id"],
                source_execution_id=source["source_execution_id"],
                source_checkpoint_id=source["checkpoint_id"],
                target_user_id="foreign-user",
                overlay_messages=source["plan"].overlay_messages,
            )
        assert owner_exc.value.code == "FORK_FOREIGN_PRINCIPAL"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_activation_is_accounting_neutral_and_exact(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activation_exact.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activation",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        before = await service.get_budget(source["task_id"])

        activated = await store.activate_fork_execution(
            bootstrap,
            identity=_identity(),
        )
        after = await service.get_budget(source["task_id"])
        execution = await store.load_execution(admission.execution_id)

        assert activated.source_execution_revision == 1
        assert activated.activated_execution_revision == 2
        assert execution.revision == 2
        assert execution.state == "RUNNING"
        assert before.active_branches == after.active_branches
        assert before.used_executions == after.used_executions
        assert before.active_executions == after.active_executions
        assert (
            before.active_parallel_agents
            == after.active_parallel_agents
        )
        assert bootstrap.context.active_budget_running is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_cancelled_task_cannot_activate_preactivation_fork(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activation_cancelled.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-cancelled",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        await service.cancel_task(source["task_id"])

        with pytest.raises(ForkControlError) as exc:
            await store.activate_fork_execution(
                bootstrap,
                identity=_identity(),
            )
        assert exc.value.code == "FORK_TASK_TERMINAL"

        execution = await store.load_execution(admission.execution_id)
        assert execution.revision == 1
        assert execution.state == "RUNNING"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_activation_rejects_branch_context_change(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activation_context.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-context",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )

        async with _Uow(sessions) as uow:
            changed = await uow.agents.compare_and_set_task_branch_context(
                admission.branch_id,
                0,
                [{"role": "user", "content": "changed"}],
            )
            assert changed is not None
            await uow.commit()

        with pytest.raises(ForkControlError) as exc:
            await store.activate_fork_execution(
                bootstrap,
                identity=_identity(),
            )
        assert exc.value.code == "FORK_BRANCH_CONTEXT_CHANGED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_two_activation_attempts_have_one_winner_and_loser_is_neutral(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activation_race.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-race",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        first_bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        second_bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        before = await service.get_budget(source["task_id"])

        results = await asyncio.gather(
            store.activate_fork_execution(
                first_bootstrap,
                identity=_identity(),
            ),
            store.activate_fork_execution(
                second_bootstrap,
                identity=_identity(),
            ),
            return_exceptions=True,
        )

        winners = [
            item for item in results
            if not isinstance(item, BaseException)
        ]
        losers = [
            item for item in results
            if isinstance(item, BaseException)
        ]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], ForkControlError)
        assert losers[0].code in {
            "FORK_ACTIVATION_CONFLICT",
            "FORK_ACTIVATION_CONTEXT_CONFLICT",
        }

        execution = await store.load_execution(admission.execution_id)
        after = await service.get_budget(source["task_id"])
        assert execution.revision == 2
        assert execution.state == "RUNNING"
        assert before.active_executions == after.active_executions
        assert before.active_branches == after.active_branches
    finally:
        await engine.dispose()
