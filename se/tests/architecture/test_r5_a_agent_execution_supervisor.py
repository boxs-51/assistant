from __future__ import annotations

import asyncio

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.loop import AgentLoopState
from se.src.runtimes.agent.contracts.result import AgentExecutionResult
from se.src.runtimes.agent.supervisor import (
    AgentExecutionOwnershipError,
    AgentExecutionSupervisor,
    AgentExecutionSupervisorClosedError,
)


def _identity() -> Identity:
    return Identity(
        user_id="r5-a-user",
        auth_type="api_key",
        scopes={"*"},
    )


def _context(
    execution_id: str,
    *,
    task_id: str | None = "task-r5-a",
    parent_execution_id: str | None = None,
    cancellation_event: asyncio.Event | None = None,
) -> AgentExecutionContext:
    context = AgentExecutionContext.create(
        execution_id=execution_id,
        agent_id=f"agent-{execution_id}",
        session_id="session-r5-a",
        correlation_id="corr-r5-a",
        identity=_identity(),
        limits=AgentExecutionLimits(timeout_seconds=30),
        task_id=task_id,
        parent_execution_id=parent_execution_id,
        input={"prompt": execution_id},
    )
    if cancellation_event is not None:
        context.cancellation_event = cancellation_event
    return context


def _result(context: AgentExecutionContext) -> AgentExecutionResult:
    return AgentExecutionResult(
        execution_id=context.execution_id,
        agent_id=context.agent_id,
        state=AgentLoopState.COMPLETED,
        output="done",
    )


def _blocking_runner(
    context: AgentExecutionContext,
    started: asyncio.Event,
    cleaned: asyncio.Event,
):
    async def run() -> AgentExecutionResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return _result(context)

    return run


@pytest.mark.asyncio
async def test_reservation_is_exclusive_and_explicitly_releasable():
    supervisor = AgentExecutionSupervisor()
    context = _context("exec-reserved")

    token = await supervisor.reserve(context)

    with pytest.raises(AgentExecutionOwnershipError, match="already reserved"):
        await supervisor.reserve(context)

    assert await supervisor.release_reserved(token) is True
    assert await supervisor.release_reserved(token) is False

    replacement = await supervisor.reserve(context)
    assert replacement.execution_id == context.execution_id
    assert replacement.token != token.token
    assert await supervisor.release_reserved(replacement) is True


@pytest.mark.asyncio
async def test_start_reserved_rejects_context_identity_or_scope_change():
    supervisor = AgentExecutionSupervisor()
    context = _context("exec-reserved-scope")
    token = await supervisor.reserve(context)

    changed = _context(
        "exec-reserved-scope",
        task_id="different-task",
    )

    async def runner():
        return _result(changed)

    with pytest.raises(
        AgentExecutionOwnershipError,
        match="identity/cancellation scope changed",
    ):
        await supervisor.start_reserved(token, changed, runner)

    assert await supervisor.release_reserved(token) is True


@pytest.mark.asyncio
async def test_duplicate_live_execution_id_is_rejected():
    supervisor = AgentExecutionSupervisor()
    context = _context("exec-duplicate")
    started = asyncio.Event()
    cleaned = asyncio.Event()

    outer = asyncio.create_task(
        supervisor.run(
            context,
            _blocking_runner(context, started, cleaned),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(AgentExecutionOwnershipError, match="already running"):
        await supervisor.reserve(_context("exec-duplicate"))

    await supervisor.cancel_execution("exec-duplicate")
    result = await asyncio.gather(outer, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    await asyncio.wait_for(cleaned.wait(), timeout=1)
    assert supervisor.active_execution_ids() == ()


@pytest.mark.asyncio
async def test_supervisor_rejects_cancellation_event_alias_between_live_executions():
    supervisor = AgentExecutionSupervisor()
    shared_event = asyncio.Event()
    parent = _context(
        "exec-parent-alias",
        cancellation_event=shared_event,
    )
    started = asyncio.Event()
    cleaned = asyncio.Event()

    parent_task = asyncio.create_task(
        supervisor.run(
            parent,
            _blocking_runner(parent, started, cleaned),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    child = _context(
        "exec-child-alias",
        parent_execution_id=parent.execution_id,
        cancellation_event=shared_event,
    )
    with pytest.raises(
        AgentExecutionOwnershipError,
        match="Cancellation event is already owned",
    ):
        await supervisor.reserve(child)

    await supervisor.cancel_execution(parent.execution_id)
    await asyncio.gather(parent_task, return_exceptions=True)
    await asyncio.wait_for(cleaned.wait(), timeout=1)


@pytest.mark.asyncio
async def test_child_cancellation_does_not_cancel_parent():
    supervisor = AgentExecutionSupervisor()

    parent = _context("exec-parent")
    parent_started = asyncio.Event()
    parent_cleaned = asyncio.Event()
    parent_outer = asyncio.create_task(
        supervisor.run(
            parent,
            _blocking_runner(parent, parent_started, parent_cleaned),
        )
    )
    await asyncio.wait_for(parent_started.wait(), timeout=1)

    child = _context(
        "exec-child",
        parent_execution_id=parent.execution_id,
    )
    child_started = asyncio.Event()
    child_cleaned = asyncio.Event()
    child_outer = asyncio.create_task(
        supervisor.run(
            child,
            _blocking_runner(child, child_started, child_cleaned),
        )
    )
    await asyncio.wait_for(child_started.wait(), timeout=1)

    await supervisor.cancel_execution(child.execution_id)

    child_result = await asyncio.gather(
        child_outer,
        return_exceptions=True,
    )
    assert isinstance(child_result[0], asyncio.CancelledError)
    assert child.cancellation_event.is_set()
    assert not parent.cancellation_event.is_set()
    assert supervisor.is_running(parent.execution_id)

    await supervisor.cancel_execution(parent.execution_id)
    await asyncio.gather(parent_outer, return_exceptions=True)
    await asyncio.wait_for(parent_cleaned.wait(), timeout=1)
    await asyncio.wait_for(child_cleaned.wait(), timeout=1)


@pytest.mark.asyncio
async def test_parent_cancellation_cascades_to_descendants():
    supervisor = AgentExecutionSupervisor()

    parent = _context("exec-parent-cascade")
    child = _context(
        "exec-child-cascade",
        parent_execution_id=parent.execution_id,
    )
    grandchild = _context(
        "exec-grandchild-cascade",
        parent_execution_id=child.execution_id,
    )

    contexts = [parent, child, grandchild]
    started = [asyncio.Event() for _ in contexts]
    cleaned = [asyncio.Event() for _ in contexts]
    outers: list[asyncio.Task] = []

    for context, start_event, clean_event in zip(
        contexts,
        started,
        cleaned,
        strict=True,
    ):
        outers.append(
            asyncio.create_task(
                supervisor.run(
                    context,
                    _blocking_runner(
                        context,
                        start_event,
                        clean_event,
                    ),
                )
            )
        )
        await asyncio.wait_for(start_event.wait(), timeout=1)

    await supervisor.cancel_execution(parent.execution_id)

    await asyncio.gather(*outers, return_exceptions=True)
    assert all(context.cancellation_event.is_set() for context in contexts)
    for event in cleaned:
        await asyncio.wait_for(event.wait(), timeout=1)
    assert supervisor.active_execution_ids() == ()


@pytest.mark.asyncio
async def test_task_cancellation_targets_only_matching_task_and_descendants():
    supervisor = AgentExecutionSupervisor()

    first = _context("exec-task-a-1", task_id="task-a")
    second = _context(
        "exec-task-a-2",
        task_id="task-a",
        parent_execution_id=first.execution_id,
    )
    unrelated = _context("exec-task-b", task_id="task-b")

    contexts = [first, second, unrelated]
    started = [asyncio.Event() for _ in contexts]
    cleaned = [asyncio.Event() for _ in contexts]
    outers: list[asyncio.Task] = []

    for context, start_event, clean_event in zip(
        contexts,
        started,
        cleaned,
        strict=True,
    ):
        outers.append(
            asyncio.create_task(
                supervisor.run(
                    context,
                    _blocking_runner(
                        context,
                        start_event,
                        clean_event,
                    ),
                )
            )
        )
        await asyncio.wait_for(start_event.wait(), timeout=1)

    await supervisor.cancel_task("task-a")

    assert first.cancellation_event.is_set()
    assert second.cancellation_event.is_set()
    assert not unrelated.cancellation_event.is_set()
    assert supervisor.is_running(unrelated.execution_id)

    await supervisor.cancel_execution(unrelated.execution_id)
    await asyncio.gather(*outers, return_exceptions=True)
    for event in cleaned:
        await asyncio.wait_for(event.wait(), timeout=1)


@pytest.mark.asyncio
async def test_caller_cancellation_drains_supervised_runtime_task():
    supervisor = AgentExecutionSupervisor()
    context = _context("exec-caller-cancel")
    started = asyncio.Event()
    cleaned = asyncio.Event()

    outer = asyncio.create_task(
        supervisor.run(
            context,
            _blocking_runner(context, started, cleaned),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    await asyncio.wait_for(cleaned.wait(), timeout=1)
    assert context.cancellation_event.is_set()
    assert supervisor.active_execution_ids() == ()


@pytest.mark.asyncio
async def test_cancelling_reserved_execution_invalidates_token_before_start():
    supervisor = AgentExecutionSupervisor()
    context = _context("exec-reserved-cancel")
    token = await supervisor.reserve(context)

    await supervisor.cancel_execution(context.execution_id)
    assert context.cancellation_event.is_set()

    async def runner():
        return _result(context)

    with pytest.raises(
        AgentExecutionOwnershipError,
        match="missing or stale",
    ):
        await supervisor.start_reserved(token, context, runner)


@pytest.mark.asyncio
async def test_shutdown_drains_all_live_executions_and_rejects_new_work():
    supervisor = AgentExecutionSupervisor()
    contexts = [
        _context("exec-shutdown-1", task_id="task-shutdown"),
        _context("exec-shutdown-2", task_id="task-shutdown"),
    ]
    started = [asyncio.Event(), asyncio.Event()]
    cleaned = [asyncio.Event(), asyncio.Event()]
    outers: list[asyncio.Task] = []

    for context, start_event, clean_event in zip(
        contexts,
        started,
        cleaned,
        strict=True,
    ):
        outers.append(
            asyncio.create_task(
                supervisor.run(
                    context,
                    _blocking_runner(
                        context,
                        start_event,
                        clean_event,
                    ),
                )
            )
        )
        await asyncio.wait_for(start_event.wait(), timeout=1)

    await supervisor.shutdown()
    await asyncio.gather(*outers, return_exceptions=True)

    for event in cleaned:
        await asyncio.wait_for(event.wait(), timeout=1)
    assert supervisor.active_execution_ids() == ()
    assert supervisor.closing is True

    with pytest.raises(AgentExecutionSupervisorClosedError):
        await supervisor.reserve(_context("exec-after-shutdown"))
