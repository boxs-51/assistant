from __future__ import annotations

import asyncio

import pytest

from se.src.infrastructure.storage.models.sql.agent import AgentIterationRecord
from se.src.runtimes.agent.task_budget import ForkConsumeError

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _Uow,
    _runtime_context_state,
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
        goal="R8-F activity",
        instruction="canonical-system",
    )


def _store(sessions) -> DurableAgentStore:
    return DurableAgentStore(lambda: _Uow(sessions))


async def _activate(store, admission):
    bootstrap = await store.prepare_fork_execution_context(
        admission.execution_id,
        identity=_identity(),
        agent=_agent(),
    )
    return await store.activate_fork_execution(
        bootstrap,
        identity=_identity(),
    )


async def _second_fork(planner, service, source, *, suffix: str):
    plan = await planner.build_fork_plan(
        fork_request_id=f"fork-{suffix}",
        task_id=source["task_id"],
        source_branch_id=source["source_branch_id"],
        source_execution_id=source["source_execution_id"],
        source_checkpoint_id=source["checkpoint_id"],
        target_user_id=_identity().user_id,
        overlay_messages=(
            {"role": "user", "content": f"overlay-{suffix}"},
        ),
    )
    return await service.consume_fork_plan(plan)


@pytest.mark.asyncio
async def test_r8_f_activity_running_branch_overrides_stale_task_waiting(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activity_running.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activity-running",
        )
        await service.consume_fork_plan(source["plan"])

        guarded = await service.transition_task(
            source["task_id"],
            allowed_source_states=("RUNNING",),
            target_state="WAITING",
            values={"wait_reasons": ["RESOURCE"]},
        )
        assert str(guarded.status) == "RUNNING"
        assert list(guarded.wait_reasons or []) == []

        first, second = await asyncio.gather(
            service.reconcile_multibranch_task_activity(source["task_id"]),
            service.reconcile_multibranch_task_activity(source["task_id"]),
        )
        assert str(first.status) == "RUNNING"
        assert str(second.status) == "RUNNING"

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            assert str(task.status) == "RUNNING"
            assert list(task.wait_reasons or []) == []
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_terminal_branch_does_not_terminalize_task(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activity_terminal.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activity-terminal",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        activation = await _activate(store, admission)

        await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=admission.execution_id,
            source_revision=activation.activated_execution_revision,
            transition_values={
                "state": "COMPLETED",
                "wait_reason": None,
            },
            delegated=False,
        )

        task = await service.reconcile_multibranch_task_activity(
            source["task_id"]
        )

        assert str(task.status) == "WAITING"
        assert list(task.wait_reasons or []) == ["RESOURCE"]
        budget = await service.get_budget(source["task_id"])
        assert str(budget.state.value) == "OPEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_waiting_reasons_derive_from_all_open_branch_heads(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activity_waiting_union.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activity-union",
        )
        first_admission = await service.consume_fork_plan(source["plan"])
        second_admission = await _second_fork(
            planner,
            service,
            source,
            suffix="r8-f-activity-union-2",
        )
        store = _store(sessions)
        first_activation = await _activate(store, first_admission)
        second_activation = await _activate(store, second_admission)

        async with _Uow(sessions) as uow:
            first = await uow.agents.compare_and_set_execution(
                first_admission.execution_id,
                first_activation.activated_execution_revision,
                {
                    "state": "WAITING",
                    "wait_reason": "HUMAN_APPROVAL",
                },
            )
            second = await uow.agents.compare_and_set_execution(
                second_admission.execution_id,
                second_activation.activated_execution_revision,
                {
                    "state": "WAITING",
                    "wait_reason": "CONNECTION",
                },
            )
            assert first is not None
            assert second is not None
            await uow.commit()

        results = await asyncio.gather(
            service.reconcile_multibranch_task_activity(source["task_id"]),
            service.reconcile_multibranch_task_activity(source["task_id"]),
        )
        assert all(str(item.status) == "WAITING" for item in results)

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            assert str(task.status) == "WAITING"
            assert list(task.wait_reasons or []) == [
                "CONNECTION",
                "HUMAN_APPROVAL",
                "RESOURCE",
            ]
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_all_open_branch_heads_terminal_preserves_nonterminal_task(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_activity_unresolved.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-activity-unresolved",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        activation = await _activate(store, admission)

        await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=admission.execution_id,
            source_revision=activation.activated_execution_revision,
            transition_values={
                "state": "FAILED",
                "wait_reason": None,
                "error": "branch failed",
            },
            delegated=False,
        )

        async with _Uow(sessions) as uow:
            source_execution = await uow.agents.get_execution(
                source["source_execution_id"]
            )
            terminal_source = await uow.agents.compare_and_set_execution(
                source["source_execution_id"],
                int(source_execution.revision),
                {
                    "state": "FAILED",
                    "wait_reason": None,
                    "error": "source failed",
                },
            )
            assert terminal_source is not None
            await uow.commit()

        before = await service.reconcile_multibranch_task_activity(
            source["task_id"]
        )
        assert str(before.status) in {"RUNNING", "WAITING"}

        after = await service.reconcile_multibranch_task_activity(
            source["task_id"]
        )
        assert str(after.status) == str(before.status)
        budget = await service.get_budget(source["task_id"])
        assert str(budget.state.value) == "OPEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_legacy_waiting_write_cannot_override_running_sibling(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_legacy_waiting_guard.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-legacy-waiting-guard",
        )
        await service.consume_fork_plan(source["plan"])

        durable = await service.transition_task(
            source["task_id"],
            allowed_source_states=("RUNNING",),
            target_state="WAITING",
            values={"wait_reasons": ["RESOURCE"]},
        )

        assert str(durable.status) == "RUNNING"
        assert list(durable.wait_reasons or []) == []
        budget = await service.get_budget(source["task_id"])
        assert str(budget.state.value) == "OPEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_legacy_terminal_write_cannot_close_multibranch_task(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_legacy_terminal_guard.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-legacy-terminal-guard",
        )
        admission = await service.consume_fork_plan(source["plan"])

        durable = await service.terminalize_task(
            source["task_id"],
            allowed_source_states=("RUNNING",),
            target_state="COMPLETED",
            values={"output": {"stale_root_result": True}},
        )

        assert str(durable.status) == "RUNNING"
        assert durable.output is None
        budget = await service.get_budget(source["task_id"])
        assert str(budget.state.value) == "OPEN"
        assert budget.active_branches == 2
        async with _Uow(sessions) as uow:
            branch = await uow.agents.get_task_branch(admission.branch_id)
            assert branch is not None
            assert str(branch.resolution_state) == "OPEN"
            await uow.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_fork_consume_vs_legacy_terminalize_has_no_split_brain(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_fork_vs_terminalize.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-fork-vs-terminalize",
        )

        fork_outcome, terminal_outcome = await asyncio.gather(
            service.consume_fork_plan(source["plan"]),
            service.terminalize_task(
                source["task_id"],
                allowed_source_states=("RUNNING",),
                target_state="COMPLETED",
                values={"output": {"legacy": True}},
            ),
            return_exceptions=True,
        )

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            branches = await uow.agents.list_task_branches(
                source["task_id"]
            )
            budget_record = await uow.agents.get_task_budget(
                source["task_id"]
            )
            receipts = await uow.agents.list_task_fork_admissions(
                source["task_id"]
            )
            await uow.commit()

        if isinstance(fork_outcome, BaseException):
            assert isinstance(fork_outcome, ForkConsumeError)
            assert not isinstance(terminal_outcome, BaseException)
            assert str(task.status) == "COMPLETED"
            assert str(budget_record.state) == "CLOSED"
            assert len(branches) == 1
            assert receipts == []
        else:
            assert not isinstance(terminal_outcome, BaseException)
            assert str(task.status) == "RUNNING"
            assert str(budget_record.state) == "OPEN"
            assert len(branches) == 2
            assert len(receipts) == 1
            assert receipts[0].execution_id == fork_outcome.execution_id

        # Forbidden split-brain state: committed fork plus terminal Task.
        assert not (
            len(receipts) == 1
            and str(task.status) in {"COMPLETED", "FAILED", "CANCELLED"}
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_terminal_wrapper_after_waiting_reconcile_stays_nonterminal(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_waiting_then_terminal_guard.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-waiting-then-terminal-guard",
        )
        admission = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        activation = await _activate(store, admission)

        async with _Uow(sessions) as uow:
            waiting = await uow.agents.compare_and_set_execution(
                admission.execution_id,
                activation.activated_execution_revision,
                {
                    "state": "WAITING",
                    "wait_reason": "CONNECTION",
                },
            )
            assert waiting is not None
            await uow.commit()

        aggregated = await service.reconcile_multibranch_task_activity(
            source["task_id"]
        )
        assert str(aggregated.status) == "WAITING"

        # The legacy root wrapper still calls terminalize_task with
        # allowed_source_states=(RUNNING,). Multi-branch reconciliation must
        # run before that stale source-state check.
        guarded = await service.terminalize_task(
            source["task_id"],
            allowed_source_states=("RUNNING",),
            target_state="COMPLETED",
            values={"output": {"stale_root_result": True}},
        )

        assert str(guarded.status) == "WAITING"
        assert list(guarded.wait_reasons or []) == [
            "CONNECTION",
            "RESOURCE",
        ]
        assert guarded.output is None
        budget = await service.get_budget(source["task_id"])
        assert str(budget.state.value) == "OPEN"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_fork_consume_vs_standalone_reconcile_has_no_waiting_running_split(
    tmp_path,
):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_fork_vs_reconcile.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-fork-vs-reconcile",
            fork_request_id="fork-r8-f-b2",
        )
        b2 = await service.consume_fork_plan(source["plan"])
        store = _store(sessions)
        activation = await _activate(store, b2)

        # Emulate the normal runtime persistence that makes B2 a future
        # forkable WAITING source.
        async with _Uow(sessions) as uow:
            updated = await uow.agents.update_execution(
                b2.execution_id,
                {
                    "context_state": _runtime_context_state(
                        "task-r8-f-fork-vs-reconcile-b2"
                    )
                },
            )
            assert updated is not None
            uow.session.add(
                AgentIterationRecord(
                    id="iter-r8-f-fork-vs-reconcile-b2",
                    execution_id=b2.execution_id,
                    iteration=1,
                    state="WAITING",
                    tool_call_ids=[],
                )
            )
            await uow.commit()

        b2_checkpoint = "cp-r8-f-fork-vs-reconcile-b2"
        assert await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=b2.execution_id,
            source_revision=activation.activated_execution_revision,
            transition_values={
                "state": "WAITING",
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
                "completed_at": None,
            },
            delegated=False,
            checkpoint_values={
                "checkpoint_id": b2_checkpoint,
                "execution_id": b2.execution_id,
                "execution_revision": 3,
                "session_id": source["session_id"],
                "task_id": source["task_id"],
                "branch_id": b2.branch_id,
                "iteration": 1,
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
                "transcript_snapshot": [
                    {"role": "user", "content": "base"},
                    {"role": "user", "content": "fork-local"},
                ],
                "metadata_json": {},
            },
            pending_invocations=(),
        ) == 3

        # B1 is terminal while B2 is the only WAITING open branch head.
        # Task intentionally remains RUNNING until aggregate reconciliation.
        async with _Uow(sessions) as uow:
            source_execution = await uow.agents.get_execution(
                source["source_execution_id"]
            )
            terminal = await uow.agents.compare_and_set_execution(
                source["source_execution_id"],
                int(source_execution.revision),
                {
                    "state": "COMPLETED",
                    "wait_reason": None,
                    "completed_at": None,
                },
            )
            assert terminal is not None
            await uow.commit()

        b3_plan = await planner.build_fork_plan(
            fork_request_id="fork-r8-f-b3",
            task_id=source["task_id"],
            source_branch_id=b2.branch_id,
            source_execution_id=b2.execution_id,
            source_checkpoint_id=b2_checkpoint,
            target_user_id=_identity().user_id,
            overlay_messages=(
                {"role": "user", "content": "b3-overlay"},
            ),
        )

        fork_outcome, reconcile_outcome = await asyncio.gather(
            service.consume_fork_plan(b3_plan),
            service.reconcile_multibranch_task_activity(source["task_id"]),
            return_exceptions=True,
        )

        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            receipts = await uow.agents.list_task_fork_admissions(
                source["task_id"]
            )
            branches = await uow.agents.list_task_branches(source["task_id"])
            branch_heads = []
            for branch in branches:
                if branch.current_execution_id is None:
                    continue
                execution = await uow.agents.get_execution(
                    branch.current_execution_id
                )
                if execution is not None:
                    branch_heads.append((branch.branch_id, str(execution.state)))
            await uow.commit()

        b3_receipts = [
            item for item in receipts
            if item.fork_request_id == "fork-r8-f-b3"
        ]
        if b3_receipts:
            assert not isinstance(fork_outcome, BaseException)
            assert str(task.status) == "RUNNING"
            assert any(
                branch_id == b3_receipts[0].branch_id and state == "RUNNING"
                for branch_id, state in branch_heads
            )
        else:
            assert isinstance(fork_outcome, ForkConsumeError)
            assert str(task.status) == "WAITING"

        # The forbidden stale snapshot outcome.
        assert not (
            b3_receipts
            and str(task.status) == "WAITING"
            and any(
                branch_id == b3_receipts[0].branch_id and state == "RUNNING"
                for branch_id, state in branch_heads
            )
        )
        assert not isinstance(reconcile_outcome, BaseException)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_f_waiting_activity_cas_requires_exact_live_reason_set(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="r8_f_waiting_reason_exact_set.sqlite",
    )
    try:
        source = await _seed_source(
            sessions,
            service,
            planner,
            task_id="task-r8-f-wait-reason-exact",
        )
        first_admission = await service.consume_fork_plan(source["plan"])
        second_admission = await _second_fork(
            planner,
            service,
            source,
            suffix="r8-f-wait-reason-exact-2",
        )
        store = _store(sessions)
        first_activation = await _activate(store, first_admission)
        second_activation = await _activate(store, second_admission)

        async with _Uow(sessions) as uow:
            first = await uow.agents.compare_and_set_execution(
                first_admission.execution_id,
                first_activation.activated_execution_revision,
                {
                    "state": "WAITING",
                    "wait_reason": "HUMAN_APPROVAL",
                },
            )
            second = await uow.agents.compare_and_set_execution(
                second_admission.execution_id,
                second_activation.activated_execution_revision,
                {
                    "state": "WAITING",
                    "wait_reason": "CONNECTION",
                },
            )
            assert first is not None
            assert second is not None
            task = await uow.agents.get_task(source["task_id"])
            snapshot_revision = int(task.revision)

            # Missing one currently live reason must fail closed.
            missing_live_reason = await uow.agents.compare_and_set_task_activity(
                source["task_id"],
                snapshot_revision,
                target_state="WAITING",
                wait_reasons=["CONNECTION", "RESOURCE"],
            )
            assert missing_live_reason is None
            await uow.commit()

        # Snapshot now contains all three reasons. Remove HUMAN_APPROVAL before
        # attempting the stale Task WAITING projection.
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            snapshot_revision = int(task.revision)
            first = await uow.agents.get_execution(first_admission.execution_id)
            terminal = await uow.agents.compare_and_set_execution(
                first_admission.execution_id,
                int(first.revision),
                {
                    "state": "COMPLETED",
                    "wait_reason": None,
                },
            )
            assert terminal is not None
            await uow.commit()

        async with _Uow(sessions) as uow:
            stale = await uow.agents.compare_and_set_task_activity(
                source["task_id"],
                snapshot_revision,
                target_state="WAITING",
                wait_reasons=[
                    "CONNECTION",
                    "HUMAN_APPROVAL",
                    "RESOURCE",
                ],
            )
            assert stale is None
            await uow.commit()

        final = await service.reconcile_multibranch_task_activity(
            source["task_id"]
        )
        assert str(final.status) == "WAITING"
        assert list(final.wait_reasons or []) == [
            "CONNECTION",
            "RESOURCE",
        ]
    finally:
        await engine.dispose()
