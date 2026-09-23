from __future__ import annotations

import asyncio

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    RetryControlError,
)
from se.tests.integration.test_r8_d_atomic_fork_consume import _Uow
from se.tests.integration.test_r9_b_atomic_retry_admission import (
    _seed_failed_source,
    _setup,
)


def _identity() -> Identity:
    return Identity(user_id="user-r9", auth_type="api_key", scopes={"*"})


def _agent() -> AgentDefinition:
    return AgentDefinition(
        name="agent-r9",
        goal="R9 retry",
        instruction="retry safely",
    )


def _store(sessions) -> DurableAgentStore:
    return DurableAgentStore(lambda: _Uow(sessions))


@pytest.mark.asyncio
async def test_r9_c_restart_reconstructs_same_retry_without_resurrecting_source(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_c_restart.sqlite"
    )
    try:
        root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-c-restart"
        )
        admission = await service.consume_retry_plan(plan)

        restarted = _store(sessions)
        replay = await restarted.load_retry_replay(
            task_id=plan.task_id,
            retry_request_id=plan.retry_request_id,
            branch_id=plan.branch_id,
            source_execution_id=plan.source_execution_id,
            source_checkpoint_id=None,
            target_user_id="user-r9",
        )
        bootstrap = await restarted.prepare_retry_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )

        assert replay is not None and replay.preactivation
        assert replay.admission.execution_id == admission.execution_id
        assert bootstrap.branch_id == root.branch_id
        assert bootstrap.context.retry_of_execution_id == plan.source_execution_id
        assert bootstrap.context.execution_id == admission.execution_id
        assert bootstrap.context.active_budget_running is False

        activation = await restarted.activate_retry_execution(
            bootstrap, identity=_identity()
        )
        assert activation.activated_execution_revision == 2
        source = await restarted.load_execution(plan.source_execution_id)
        retry = await restarted.load_execution(admission.execution_id)
        assert source.state == "FAILED"
        assert source.revision == plan.expected_execution_revision
        assert retry.state == "RUNNING"
        assert retry.revision == 2
        assert retry.retry_of_execution_id == source.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_c_simultaneous_activation_has_one_cas_winner(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_c_activation_race.sqlite"
    )
    try:
        _root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-c-race"
        )
        admission = await service.consume_retry_plan(plan)
        store = _store(sessions)
        first = await store.prepare_retry_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )
        second = await store.prepare_retry_execution_context(
            admission.execution_id, identity=_identity(), agent=_agent()
        )
        outcomes = await asyncio.gather(
            store.activate_retry_execution(first, identity=_identity()),
            store.activate_retry_execution(second, identity=_identity()),
            return_exceptions=True,
        )
        winners = [item for item in outcomes if not isinstance(item, BaseException)]
        losers = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], RetryControlError)
        assert losers[0].code in {
            "RETRY_ACTIVATION_CONFLICT",
            "RETRY_ACTIVATION_CONTEXT_CONFLICT",
        }
        assert (await store.load_execution(admission.execution_id)).revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_c_task_cancel_settles_dormant_retry_once(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, "r9_c_cancel.sqlite"
    )
    try:
        _root, plan = await _seed_failed_source(
            sessions, service, planner, task_id="task-r9-c-cancel"
        )
        admission = await service.consume_retry_plan(plan)
        before = await service.get_budget(plan.task_id)
        task = await service.cancel_task(plan.task_id)
        after = await service.get_budget(plan.task_id)
        retry = await _store(sessions).load_execution(admission.execution_id)

        assert task.status == "CANCELLED"
        assert retry.state == "CANCELLED"
        assert retry.revision == 2
        assert after.state.value == "CLOSED"
        assert after.active_executions == before.active_executions - 1

        await service.cancel_task(plan.task_id)
        repeated = await service.get_budget(plan.task_id)
        assert repeated.active_executions == after.active_executions
    finally:
        await engine.dispose()
