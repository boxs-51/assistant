from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskForkRequest
from se.src.main import execute_forked_agent_task_control_plane
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.fork_planning import ForkPlanDeferred
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ForkControlError,
)
from se.src.runtimes.agent.supervisor import AgentExecutionSupervisor
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _seed_source,
    _setup,
)


USER = "user-r8-d"


def _identity() -> Identity:
    return Identity(
        user_id=USER,
        auth_type="api_key",
        scopes={"*"},
    )


def _agent() -> AgentDefinition:
    return AgentDefinition(
        name="agent-r8-d",
        goal="R8-G vertical exit gate",
        instruction="Preserve frozen fork semantics.",
    )


def _request(source, request_id: str, overlay: str) -> AgentTaskForkRequest:
    return AgentTaskForkRequest(
        fork_request_id=request_id,
        source_branch_id=source["source_branch_id"],
        source_execution_id=source["source_execution_id"],
        source_checkpoint_id=source["checkpoint_id"],
        overlay_messages=[{"role": "user", "content": overlay}],
    )


def _store(sessions) -> DurableAgentStore:
    return DurableAgentStore(lambda: _Uow(sessions))


class _RuntimeProbe:
    def __init__(self, *, block: bool = False):
        self.contexts = []
        self.revisions = []
        self.started = asyncio.Event()
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = block

    async def execute(self, context, *, durable_revision):
        self.contexts.append(context)
        self.revisions.append(durable_revision)
        self.started.set()
        if len(self.contexts) >= 2:
            self.all_started.set()
        if self.block:
            await self.release.wait()
        return SimpleNamespace(state="RUNNING")

    async def cancel_activated_fork_execution(
        self,
        context,
        revision,
        *,
        error_message,
    ):
        raise AssertionError(
            "vertical happy path must not enter activated cleanup: "
            f"{context.execution_id}@{revision} {error_message}"
        )


def _container(store, planner, service, supervisor, runtime):
    return SimpleNamespace(
        agent_durable_store=store,
        fork_planning_service=planner,
        task_budget_service=service,
        agent_execution_supervisor=supervisor,
        agent_runtime=runtime,
        agent_registry=SimpleNamespace(
            get=lambda agent_id: _agent()
            if agent_id == "agent-r8-d"
            else None
        ),
    )


@pytest.mark.asyncio
async def test_r8_g_g1_vertical_fork_happy_path_and_same_request_replay(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g1_happy.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    runtime = _RuntimeProbe()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g1",
            fork_request_id="unused-r8-g-g1-plan",
        )
        store = _store(sessions)
        container = _container(
            store,
            planner,
            service,
            supervisor,
            runtime,
        )
        request = _request(source, "fork-r8-g-g1", "overlay-g1")
        before = await service.get_budget(source["task_id"])

        first = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            request,
            _identity(),
        )
        await asyncio.wait_for(runtime.started.wait(), timeout=1)
        await asyncio.sleep(0)

        replay = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            request,
            _identity(),
        )
        after = await service.get_budget(source["task_id"])

        assert first["started"] is True
        assert replay["started"] is False
        assert replay["branch_id"] == first["branch_id"]
        assert replay["execution_id"] == first["execution_id"]
        assert runtime.revisions == [2]
        assert len(runtime.contexts) == 1
        assert [
            message["content"]
            for message in runtime.contexts[0].branch_base_transcript
        ] == ["base", "overlay-g1"]

        async with _Uow(sessions) as uow:
            branches = await uow.agents.list_task_branches(source["task_id"])
            receipt = await uow.agents.get_task_fork_admission(
                source["task_id"],
                request.fork_request_id,
            )
            execution = await uow.agents.get_execution(first["execution_id"])
            source_checkpoint = await uow.agents.get_execution_checkpoint(
                source["checkpoint_id"]
            )
            task = await uow.agents.get_task(source["task_id"])
            await uow.commit()

        assert len(branches) == 2
        assert receipt.branch_id == first["branch_id"]
        assert receipt.execution_id == first["execution_id"]
        assert execution.state == "RUNNING"
        assert execution.revision == 2
        assert source_checkpoint.transcript_snapshot == [
            {"role": "user", "content": "base"}
        ]
        assert task.status in {"RUNNING", "WAITING"}
        assert task.output is None
        assert after.active_branches == before.active_branches + 1
        assert after.used_executions == before.used_executions + 1
    finally:
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_g_g2_sibling_runners_have_isolated_branch_contexts(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g2_siblings.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    runtime = _RuntimeProbe(block=True)
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g2",
            fork_request_id="unused-r8-g-g2-plan",
        )
        store = _store(sessions)
        container = _container(
            store,
            planner,
            service,
            supervisor,
            runtime,
        )

        first = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            _request(source, "fork-r8-g-g2-a", "overlay-alpha"),
            _identity(),
        )
        second = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            _request(source, "fork-r8-g-g2-b", "overlay-beta"),
            _identity(),
        )
        await asyncio.wait_for(runtime.all_started.wait(), timeout=1)

        assert first["execution_id"] != second["execution_id"]
        assert first["branch_id"] != second["branch_id"]
        assert set(supervisor.active_execution_ids()) == {
            first["execution_id"],
            second["execution_id"],
        }

        by_branch = {
            context.branch_id: [
                item["content"] for item in context.branch_base_transcript
            ]
            for context in runtime.contexts
        }
        assert by_branch[first["branch_id"]] == ["base", "overlay-alpha"]
        assert by_branch[second["branch_id"]] == ["base", "overlay-beta"]

        async with _Uow(sessions) as uow:
            source_checkpoint = await uow.agents.get_execution_checkpoint(
                source["checkpoint_id"]
            )
            task = await uow.agents.get_task(source["task_id"])
            await uow.commit()

        assert source_checkpoint.transcript_snapshot == [
            {"role": "user", "content": "base"}
        ]
        assert task.status == "RUNNING"
        assert task.output is None
    finally:
        runtime.release.set()
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_g_g2_root_compat_runner_does_not_block_fork_ownership(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g2_root_compat.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    runtime = _RuntimeProbe(block=True)
    root_release = asyncio.Event()
    root_started = asyncio.Event()
    root_runner = None
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g2-root-compat",
            fork_request_id="unused-r8-g-g2-root-plan",
        )
        store = _store(sessions)
        container = _container(
            store,
            planner,
            service,
            supervisor,
            runtime,
        )

        async def root_compat_runner():
            root_started.set()
            await root_release.wait()

        root_runner = asyncio.create_task(
            root_compat_runner(),
            name=f"agent-task:{source['task_id']}",
        )

        async def fork_executor(task_id, request, identity):
            return await execute_forked_agent_task_control_plane(
                container,
                task_id,
                request,
                identity,
            )

        coordinator = MultiAgentCoordinator(
            AgentRegistry(),
            durable_store=store,
            fork_executor=fork_executor,
            execution_supervisor=supervisor,
            task_budget_service=service,
        )
        coordinator._running_tasks[source["task_id"]] = root_runner

        await asyncio.wait_for(root_started.wait(), timeout=1)
        assert not root_runner.done()

        response = await coordinator.fork_task(
            source["task_id"],
            _request(
                source,
                "fork-r8-g-g2-root-compat",
                "overlay-root-compat",
            ),
            _identity(),
        )
        await asyncio.wait_for(runtime.started.wait(), timeout=1)

        assert response.started is True
        assert coordinator._running_tasks[source["task_id"]] is root_runner
        assert not root_runner.done()
        assert response.execution_id in supervisor.active_execution_ids()

        # Execution-scoped fork ownership can drain independently without
        # treating the legacy/root task_id wrapper as fork exclusivity.
        await supervisor.cancel_execution(response.execution_id)
        assert not root_runner.done()
        assert coordinator._running_tasks[source["task_id"]] is root_runner

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            await uow.commit()
        assert task.output is None
    finally:
        runtime.release.set()
        root_release.set()
        if root_runner is not None:
            await asyncio.gather(root_runner, return_exceptions=True)
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_g_g3_capacity_rejection_has_zero_partial_fork_state(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g3_capacity.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    runtime = _RuntimeProbe()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g3",
            fork_request_id="fork-r8-g-g3-first",
        )
        first_admission = await service.consume_fork_plan(source["plan"])
        before = await service.get_budget(source["task_id"])

        # Fill the branch limit durably without starting a local runner.
        async with _Uow(sessions) as uow:
            budget = await uow.agents.get_task_budget(source["task_id"])
            budget.max_active_branches = int(budget.active_branches)
            await uow.commit()

        store = _store(sessions)
        container = _container(
            store,
            planner,
            service,
            supervisor,
            runtime,
        )

        with pytest.raises(ForkPlanDeferred) as exc:
            await execute_forked_agent_task_control_plane(
                container,
                source["task_id"],
                _request(source, "fork-r8-g-g3-second", "overlay-second"),
                _identity(),
            )

        assert exc.value.code == "FORK_BRANCH_CAPACITY_UNAVAILABLE"
        after = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            branches = await uow.agents.list_task_branches(source["task_id"])
            receipts = await uow.agents.list_task_fork_admissions(
                source["task_id"]
            )
            await uow.commit()

        assert len(branches) == 2
        assert len(receipts) == 1
        assert receipts[0].execution_id == first_admission.execution_id
        assert after.active_branches == before.active_branches
        assert after.used_executions == before.used_executions
        assert after.active_executions == before.active_executions
        assert runtime.contexts == []
        assert supervisor.active_execution_ids() == ()
    finally:
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_g_g4_restart_after_consume_activates_same_execution_once(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g4_restart.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    runtime = _RuntimeProbe()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g4",
            fork_request_id="fork-r8-g-g4",
        )
        admission = await service.consume_fork_plan(source["plan"])

        store = _store(sessions)
        container = _container(
            store,
            planner,
            service,
            supervisor,
            runtime,
        )
        request = _request(source, "fork-r8-g-g4", "fork-local")

        result = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            request,
            _identity(),
        )
        await asyncio.wait_for(runtime.started.wait(), timeout=1)

        assert result["branch_id"] == admission.branch_id
        assert result["execution_id"] == admission.execution_id
        assert result["execution_revision"] == 2
        assert runtime.revisions == [2]

        replay = await execute_forked_agent_task_control_plane(
            container,
            source["task_id"],
            request,
            _identity(),
        )
        assert replay["started"] is False
        assert replay["execution_id"] == admission.execution_id
        assert runtime.revisions == [2]
    finally:
        await supervisor.shutdown()
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_g_g4_restart_safe_cancel_settles_dormant_fork(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_g_g4_cancel.sqlite",
    )
    supervisor = AgentExecutionSupervisor()
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-g-g4-cancel",
            fork_request_id="fork-r8-g-g4-cancel",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        coordinator = MultiAgentCoordinator(
            AgentRegistry(),
            durable_store=store,
            task_budget_service=service,
            execution_supervisor=supervisor,
        )

        assert coordinator._tasks == {}
        assert coordinator._sessions == {}

        result = await coordinator.cancel_task_and_wait(
            source["task_id"],
            _identity(),
        )

        budget = await service.get_budget(source["task_id"])
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            execution = await uow.agents.get_execution(admission.execution_id)
            branches = await uow.agents.list_task_branches(source["task_id"])
            await uow.commit()

        assert result.status.value == "CANCELLED"
        assert task.status == "CANCELLED"
        assert budget.state.value == "CLOSED"
        assert budget.active_executions == 0
        assert budget.active_branches == 2
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert all(branch.resolution_state == "OPEN" for branch in branches)
    finally:
        await supervisor.shutdown()
        await engine.dispose()


