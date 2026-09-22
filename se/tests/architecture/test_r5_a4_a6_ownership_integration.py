from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.infrastructure.event_bus.bus import EventDispatcher
from se.src.infrastructure.event_bus.registry import EventRegistry
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.agent.persistence import ExecutionConflictError
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.supervisor import (
    AgentExecutionSupervisor,
    AgentExecutionSupervisorClosedError,
)
from se.src.runtimes.capability.contracts.context import (
    CapabilityExecutionContext,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import (
    AgentCapabilityDriver,
)


def _identity() -> Identity:
    return Identity(
        user_id="user-r5-a4",
        auth_type="api_key",
        scopes={"*"},
    )


def _context(
    execution_id: str,
    *,
    activate_budget: bool = True,
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id="agent-r5-a4",
        session_id="session-r5-a4",
        correlation_id="corr-r5-a4",
        identity=_identity(),
        limits=AgentExecutionLimits(timeout_seconds=30),
        activate_budget=activate_budget,
    )


@pytest.mark.asyncio
async def test_nested_agent_gets_distinct_cancellation_scope_and_supervisor_owner():
    supervisor = AgentExecutionSupervisor()
    captured = {}
    agent = AgentDefinition(
        name="agent-child",
        goal="child",
        instruction="child",
    )
    definition = CapabilityDefinition(
        id=agent.name,
        name=agent.name,
        description=agent.goal,
        kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
    )

    class Runtime:
        async def execute(self, context):
            captured["context"] = context
            return SimpleNamespace(
                error_code=None,
                error_message=None,
                model_dump=lambda mode=None: {
                    "execution_id": context.execution_id
                },
            )

    driver = AgentCapabilityDriver(
        definition,
        agent,
        Runtime(),
        execution_supervisor=supervisor,
        execution_id_factory=AgentExecutionIdFactory(
            prefix="exec_",
            token_factory=lambda: "child",
        ),
    )
    parent_event = asyncio.Event()
    cap_context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-parent",
        invocation_id="inv-child",
        caller_agent_execution_id="exec-parent",
        session_id="session-r5-a4",
        task_id="task-r5-a4",
        cancellation_event=parent_event,
    )

    output = await driver.execute(cap_context, {"prompt": "delegate"})
    child = captured["context"]

    assert output["execution_id"] == "exec_child"
    assert child.parent_execution_id == "exec-parent"
    assert child.cancellation_event is not parent_event
    assert supervisor.active_execution_ids() == ()


class _BlockingBeginStore:
    def __init__(self, record=None):
        self.record = record
        self.claim_entered = asyncio.Event()
        self.allow_claim = asyncio.Event()

    async def load_execution(self, execution_id):
        if self.record is None or self.record.id != execution_id:
            return None
        return SimpleNamespace(**vars(self.record))

    async def save_execution(self, values):
        self.record = SimpleNamespace(**values)

    async def compare_and_set_execution(
        self,
        execution_id,
        expected_revision,
        values,
    ):
        assert self.record is not None
        if (
            self.record.id != execution_id
            or self.record.revision != expected_revision
        ):
            raise ExecutionConflictError("stale")
        if values.get("state") == "RUNNING":
            self.claim_entered.set()
            await self.allow_claim.wait()
        for key, value in values.items():
            setattr(self.record, key, value)
        self.record.revision += 1
        return self.record


def _runtime(store) -> AgentRuntime:
    return AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=store,
    )


@pytest.mark.asyncio
async def test_outer_cancel_during_new_durable_begin_cannot_strand_running():
    store = _BlockingBeginStore()
    runtime = _runtime(store)
    context = _context("exec-begin-cancel")

    outer = asyncio.create_task(runtime.execute(context))
    await asyncio.wait_for(store.claim_entered.wait(), timeout=1)
    outer.cancel()
    store.allow_claim.set()

    with pytest.raises(asyncio.CancelledError):
        await outer

    assert store.record.state == "CANCELLED"
    assert store.record.revision == 2


@pytest.mark.asyncio
async def test_outer_cancel_during_resume_claim_cannot_strand_running():
    context = _context(
        "exec-resume-cancel",
        activate_budget=False,
    )
    context.resume_revision = 7
    store = _BlockingBeginStore(
        SimpleNamespace(
            id=context.execution_id,
            state="WAITING",
            wait_reason="CONNECTION",
            revision=7,
            remaining_active_budget_seconds=20.0,
            wait_expires_at=None,
        )
    )
    runtime = _runtime(store)

    outer = asyncio.create_task(runtime.claim_resume(context))
    await asyncio.wait_for(store.claim_entered.wait(), timeout=1)
    outer.cancel()
    store.allow_claim.set()

    with pytest.raises(asyncio.CancelledError):
        await outer

    assert store.record.state == "CANCELLED"
    assert store.record.revision == 9


@pytest.mark.asyncio
async def test_coordinator_cancel_and_wait_drains_runner_and_supervisor_scope():
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            name="worker",
            goal="work",
            instruction="work",
        )
    )

    class Supervisor:
        def __init__(self):
            self.cancelled = []

        async def cancel_task(self, task_id):
            self.cancelled.append(task_id)

    supervisor = Supervisor()
    coordinator = MultiAgentCoordinator(
        registry,
        execution_supervisor=supervisor,
    )
    identity = _identity()
    session = coordinator.create_session(identity, ["worker"])
    task = coordinator.create_task(
        session.session_id,
        "worker",
        {"prompt": "hello"},
        identity,
    )
    started = asyncio.Event()

    async def executor(_task, **kwargs):
        started.set()
        await asyncio.Event().wait()

    await coordinator.start_task(task.task_id, identity, executor)
    await asyncio.wait_for(started.wait(), timeout=1)

    cancelled = await coordinator.cancel_task_and_wait(
        task.task_id,
        identity,
    )

    assert cancelled.status.value == "CANCELLED"
    assert supervisor.cancelled == [task.task_id]
    assert task.task_id not in coordinator._running_tasks


@pytest.mark.asyncio
async def test_event_dispatcher_shutdown_drains_tracked_worker():
    registry = EventRegistry()
    queue = asyncio.PriorityQueue()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(event):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    registry.register("r5.test", handler)
    container = SimpleNamespace(
        get_dependency=lambda _type: None,
        event_bus=SimpleNamespace(),
    )
    dispatcher = EventDispatcher(
        registry=registry,
        queue=queue,
        dependency_container=container,
        cache_driver=None,
        uow_factory=lambda: None,
    )
    future = asyncio.get_running_loop().create_future()
    event = BaseEvent(
        event_name="r5.test",
        session_id="session-r5",
        payload={},
    )
    loop_task = asyncio.create_task(dispatcher.start())
    queue.put_nowait((5, 1, event, future))

    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await dispatcher.shutdown()
        await asyncio.wait_for(cleaned.wait(), timeout=1)
        assert dispatcher._active_tasks == set()
    finally:
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_supervisor_quiesce_rejects_new_but_allows_admitted_reservation():
    supervisor = AgentExecutionSupervisor()
    admitted = _context("exec-admitted-before-quiesce")
    token = await supervisor.reserve(admitted)
    await supervisor.quiesce()

    with pytest.raises(AgentExecutionSupervisorClosedError):
        await supervisor.reserve(_context("exec-after-quiesce"))

    async def runner():
        return SimpleNamespace(
            execution_id=admitted.execution_id,
            agent_id=admitted.agent_id,
        )

    task = await supervisor.start_reserved(token, admitted, runner)
    await task
    assert supervisor.active_execution_ids() == ()

    await supervisor.shutdown()
