from __future__ import annotations

import asyncio

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskForkRequest
from se.src.main import execute_forked_agent_task_control_plane
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ForkControlError,
)
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.supervisor import (
    AgentExecutionOwnershipError,
    AgentExecutionSupervisor,
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
        before = await service.get_budget(source["task_id"])
        await service.cancel_task(source["task_id"])

        with pytest.raises(ForkControlError) as exc:
            await store.activate_fork_execution(
                bootstrap,
                identity=_identity(),
            )
        assert exc.value.code == "FORK_TASK_TERMINAL"

        execution = await store.load_execution(admission.execution_id)
        after = await service.get_budget(source["task_id"])
        assert execution.revision == 2
        assert execution.state == "CANCELLED"
        assert execution.error == "TASK_CANCELLED_BEFORE_FORK_ACTIVATION"
        assert after.active_executions == before.active_executions - 1
        assert after.active_branches == before.active_branches
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


@pytest.mark.asyncio
async def test_r8_f_activation_win_handoff_cleanup_releases_real_budget_once(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_handoff_cleanup.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-handoff-cleanup",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        activation = await store.activate_fork_execution(
            bootstrap,
            identity=_identity(),
        )
        before = await service.get_budget(source["task_id"])

        runtime = AgentRuntime(
            context_builder=None,
            inference=None,
            tool_execution=None,
            execution_policy=None,
            durable_store=store,
            task_budget_service=service,
        )
        await runtime.cancel_activated_fork_execution(
            bootstrap.context,
            activation.activated_execution_revision,
            error_message="forced start_reserved failure",
        )

        execution = await store.load_execution(admission.execution_id)
        after = await service.get_budget(source["task_id"])
        branch = await store.load_task_branch(admission.branch_id)
        branch_context = await store.load_task_branch_context(
            admission.branch_id
        )
        async with _Uow(sessions) as uow:
            receipt = await uow.agents.get_task_fork_admission(
                source["task_id"],
                source["plan"].fork_request_id,
            )
            task = await uow.agents.get_task(source["task_id"])
            await uow.commit()

        assert execution.state == "CANCELLED"
        assert execution.revision == 3
        assert execution.error == "forced start_reserved failure"
        assert after.active_executions == before.active_executions - 1
        assert (
            after.active_parallel_agents
            == before.active_parallel_agents
        )
        assert after.active_branches == before.active_branches
        assert branch is not None
        assert branch.current_execution_id == admission.execution_id
        assert branch_context is not None
        assert receipt is not None
        assert str(task.status) not in {"COMPLETED", "FAILED", "CANCELLED"}

        # Duplicate fail-close observation is idempotent and cannot double
        # release the precharged execution capacity.
        await runtime.cancel_activated_fork_execution(
            bootstrap.context,
            activation.activated_execution_revision,
            error_message="forced start_reserved failure",
        )
        repeated = await service.get_budget(source["task_id"])
        repeated_execution = await store.load_execution(
            admission.execution_id
        )
        assert repeated.active_executions == after.active_executions
        assert repeated.active_branches == after.active_branches
        assert repeated_execution.revision == 3
        assert repeated_execution.state == "CANCELLED"
    finally:
        await engine.dispose()


def _fork_request(source) -> AgentTaskForkRequest:
    return AgentTaskForkRequest(
        fork_request_id=source["plan"].fork_request_id,
        source_branch_id=source["source_branch_id"],
        source_execution_id=source["source_execution_id"],
        source_checkpoint_id=source["checkpoint_id"],
        overlay_messages=list(source["plan"].overlay_messages),
    )


def _real_fork_container(*, store, planner, service, supervisor, runtime):
    return type(
        "_ForkContainer",
        (),
        {
            "agent_durable_store": store,
            "fork_planning_service": planner,
            "task_budget_service": service,
            "agent_execution_supervisor": supervisor,
            "agent_runtime": runtime,
            "agent_registry": type(
                "_Registry",
                (),
                {"get": staticmethod(lambda agent_id: _agent())},
            )(),
        },
    )()


@pytest.mark.asyncio
async def test_r8_f_cancel_wins_revision_one_race_and_blocks_runtime(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_cancel_wins_activation_race.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-cancel-wins-race",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        runtime = AgentRuntime(
            context_builder=None,
            inference=None,
            tool_execution=None,
            execution_policy=None,
            durable_store=store,
            task_budget_service=service,
        )
        container = _real_fork_container(
            store=store,
            planner=planner,
            service=service,
            supervisor=supervisor,
            runtime=runtime,
        )

        original_activate = store.activate_fork_execution
        activation_ready = asyncio.Event()
        allow_activation = asyncio.Event()

        async def delayed_activation(bootstrap, *, identity, now_utc=None):
            activation_ready.set()
            await allow_activation.wait()
            return await original_activate(
                bootstrap,
                identity=identity,
                now_utc=now_utc,
            )

        store.activate_fork_execution = delayed_activation

        fork_call = asyncio.create_task(
            execute_forked_agent_task_control_plane(
                container,
                source["task_id"],
                _fork_request(source),
                _identity(),
            )
        )
        await asyncio.wait_for(activation_ready.wait(), timeout=1)

        before = await service.get_budget(source["task_id"])
        cancelled_task = await service.cancel_task(source["task_id"])
        await supervisor.cancel_task(source["task_id"])
        allow_activation.set()

        result = await fork_call
        execution = await store.load_execution(admission.execution_id)
        after = await service.get_budget(source["task_id"])

        assert str(cancelled_task.status) == "CANCELLED"
        assert result["execution_id"] == admission.execution_id
        assert result["execution_state"] == "CANCELLED"
        assert result["execution_revision"] == 2
        assert result["started"] is False
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert after.active_executions == before.active_executions - 1
        assert after.active_branches == before.active_branches
        assert supervisor.active_execution_ids() == ()
    finally:
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_activation_wins_then_task_cancel_forces_handoff_fail_close(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activation_wins_cancel_race.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activation-wins-race",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        runtime = AgentRuntime(
            context_builder=None,
            inference=None,
            tool_execution=None,
            execution_policy=None,
            durable_store=store,
            task_budget_service=service,
        )
        container = _real_fork_container(
            store=store,
            planner=planner,
            service=service,
            supervisor=supervisor,
            runtime=runtime,
        )

        original_activate = store.activate_fork_execution
        activation_committed = asyncio.Event()
        allow_activation_return = asyncio.Event()

        async def committed_then_blocked(
            bootstrap,
            *,
            identity,
            now_utc=None,
        ):
            result = await original_activate(
                bootstrap,
                identity=identity,
                now_utc=now_utc,
            )
            activation_committed.set()
            await allow_activation_return.wait()
            return result

        store.activate_fork_execution = committed_then_blocked

        fork_call = asyncio.create_task(
            execute_forked_agent_task_control_plane(
                container,
                source["task_id"],
                _fork_request(source),
                _identity(),
            )
        )
        await asyncio.wait_for(activation_committed.wait(), timeout=1)

        before_cancel = await service.get_budget(source["task_id"])
        activated = await store.load_execution(admission.execution_id)
        assert activated.state == "RUNNING"
        assert activated.revision == 2

        cancelled_task = await service.cancel_task(source["task_id"])
        await supervisor.cancel_task(source["task_id"])
        closed_budget = await service.get_budget(source["task_id"])
        assert str(cancelled_task.status) == "CANCELLED"
        assert closed_budget.active_executions == before_cancel.active_executions

        allow_activation_return.set()
        with pytest.raises(AgentExecutionOwnershipError):
            await fork_call

        execution = await store.load_execution(admission.execution_id)
        after = await service.get_budget(source["task_id"])
        assert execution.state == "CANCELLED"
        assert execution.revision == 3
        assert after.active_executions == before_cancel.active_executions - 1
        assert after.active_branches == before_cancel.active_branches
        assert supervisor.active_execution_ids() == ()
    finally:
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_fork_from_waiting_task_reactivates_task_before_e2_activation(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_waiting_source_activation.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-waiting-source",
            fork_request_id="fork-r8-f-waiting-source",
            task_waiting=True,
        )
        async with _Uow(sessions) as uow:
            before = await uow.agents.get_task(source["task_id"])
            assert str(before.status) == "WAITING"
            await uow.commit()

        admission = await service.consume_fork_plan(source["plan"])
        async with _Uow(sessions) as uow:
            after_consume = await uow.agents.get_task(source["task_id"])
            assert str(after_consume.status) == "RUNNING"
            assert list(after_consume.wait_reasons or []) == []
            await uow.commit()

        store = _store(sessions)
        bootstrap = await store.prepare_fork_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        activation = await store.activate_fork_execution(
            bootstrap,
            identity=_identity(),
        )

        assert activation.activated_execution_revision == 2
        execution = await store.load_execution(admission.execution_id)
        assert execution.state == "RUNNING"
        assert execution.revision == 2
    finally:
        await engine.dispose()
