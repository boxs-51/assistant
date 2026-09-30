from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.persistence import (
    AggregateControlError,
    DurableAgentStore,
)
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import AggregateAdmissionError
from se.tests.integration.test_r8_d_atomic_fork_consume import (
    _RecreateBudgetOnFirstCasRepository,
    _Uow,
    _recreate_task_budget_incarnation,
    _setup,
)
from se.tests.integration.test_r9_de_branch_resolution import (
    _seed_two_completed_branches,
)


def _identity() -> Identity:
    return Identity(user_id="user-r8-d", auth_type="api_key", scopes={"*"})


def _agent() -> AgentDefinition:
    return AgentDefinition(
        name="agent-r8-d",
        goal="Aggregate branch results",
        instruction="Produce a deterministic aggregate result.",
    )


def _store(sessions) -> DurableAgentStore:
    return DurableAgentStore(lambda: _Uow(sessions))


def _config(database) -> Config:
    config = Config("alembic.ini")
    config.set_main_option(
        "script_location", "se/src/infrastructure/storage/migrations/sql"
    )
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}"
    )
    return config


class _AggregateIndependentRecreateBudgetRepository(
    _RecreateBudgetOnFirstCasRepository
):
    """Inject A->B only after rolling back the operation-under-test UoW."""

    sessions = None

    @classmethod
    def reset_race(cls) -> None:
        super().reset_race()
        cls.sessions = None

    async def compare_and_set_task_budget(
        self,
        task_id,
        expected_revision,
        values,
        *,
        expected_incarnation_generation=None,
    ):
        cls = type(self)
        if cls.armed and not cls.triggered:
            cls.triggered = True
            budget = await self.get_task_budget(task_id)
            assert budget is not None
            observed_generation = int(budget.incarnation_generation)

            # aggregate_branches() has already staged its Task CAS in this
            # transaction. Roll it back before the concurrent B incarnation
            # is created so the race injector cannot commit partial state from
            # the operation under test.
            await self.session.rollback()
            assert cls.sessions is not None
            old_generation, new_generation = (
                await _recreate_task_budget_incarnation(
                    cls.sessions,
                    task_id,
                )
            )
            assert old_generation == observed_generation
            cls.old_generation = old_generation
            cls.new_generation = new_generation
            return None

        return await super().compare_and_set_task_budget(
            task_id,
            expected_revision,
            values,
            expected_incarnation_generation=expected_incarnation_generation,
        )


def test_r9_f_15b_migration_is_linear_and_reversible(tmp_path, monkeypatch):
    database = tmp_path / "r9_f_15b.sqlite"
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"sqlite+aiosqlite:///{database.as_posix()}",
    )
    config = _config(database)
    command.upgrade(config, "15b_r9_aggregate_admission")
    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(agent_task_aggregate_admissions)"
            )
        }
        assert {
            "task_id",
            "aggregate_request_id",
            "plan_fingerprint",
            "runtime_seed_fingerprint",
            "target_branch_id",
            "execution_id",
            "source_branch_snapshots",
            "source_execution_snapshots",
            "result_fingerprints",
            "created_by",
            "created_at",
        } == columns
    finally:
        connection.close()
    command.downgrade(config, "15a_r9_retry_admission")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "agent_task_aggregate_admissions" not in tables
        assert "agent_task_retry_admissions" in tables
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_r9_f_explicit_aggregate_is_durable_and_never_adopts(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_aggregate.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-aggregate",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        before = await service.get_budget(source["task_id"])
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )
        after = await service.get_budget(source["task_id"])
        replay = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )

        assert replay.execution_id == admission.execution_id
        assert admission.source_branch_ids == ordered
        assert after.used_executions == before.used_executions + 1
        assert after.active_executions == before.active_executions + 1
        assert after.active_branches == before.active_branches
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            target = await uow.agents.get_task_branch(fork.branch_id)
            execution = await uow.agents.get_execution(admission.execution_id)
            receipt = await uow.agents.get_task_aggregate_admission(
                source["task_id"], "aggregate-r9-f"
            )
            count = await uow.session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_task_aggregate_admissions "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": source["task_id"]},
            )

        assert task.status == "RUNNING"
        assert task.output is None
        assert target.resolution_state == "OPEN"
        assert target.current_execution_id == admission.execution_id
        assert execution.state == "RUNNING"
        assert execution.revision == 1
        assert execution.retry_of_execution_id is None
        assert receipt.source_branch_snapshots[0]["branch_id"] == ordered[0]
        assert receipt.source_branch_snapshots[1]["branch_id"] == ordered[1]
        assert len(receipt.result_fingerprints) == 2
        assert count.scalar_one() == 1

        with pytest.raises(AggregateAdmissionError) as raised:
            await service.aggregate_branches(
                source["task_id"],
                aggregate_request_id="aggregate-r9-f",
                target_branch_id=fork.branch_id,
                source_branch_ids=tuple(reversed(ordered)),
                target_user_id="user-r8-d",
            )
        assert raised.value.code == "AGGREGATE_REQUEST_CONFLICT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_f_restart_bootstrap_and_single_activation_owner(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_restart_activation.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-restart",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f-restart",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )

        restarted = _store(sessions)
        first = await restarted.prepare_aggregate_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        second = await restarted.prepare_aggregate_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        assert first.context.input["aggregate_request_id"] == (
            "aggregate-r9-f-restart"
        )
        assert [
            item["branch_id"]
            for item in first.context.input["source_results"]
        ] == list(ordered)
        assert first.context.active_budget_running is False

        outcomes = await asyncio.gather(
            restarted.activate_aggregate_execution(
                first,
                identity=_identity(),
            ),
            restarted.activate_aggregate_execution(
                second,
                identity=_identity(),
            ),
            return_exceptions=True,
        )
        winners = [
            item for item in outcomes if not isinstance(item, BaseException)
        ]
        losers = [
            item for item in outcomes if isinstance(item, BaseException)
        ]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], AggregateControlError)
        assert losers[0].code in {
            "AGGREGATE_ACTIVATION_CONFLICT",
            "AGGREGATE_ACTIVATION_CONTEXT_CONFLICT",
        }

        execution = await restarted.load_execution(admission.execution_id)
        assert execution.state == "RUNNING"
        assert execution.revision == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_f_restart_rejects_source_result_drift_after_admission(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_source_drift.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-source-drift",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f-source-drift",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )

        async with _Uow(sessions) as uow:
            source_execution = await uow.agents.get_execution(
                source["source_execution_id"]
            )
            source_execution.result = {"winner": "tampered"}
            await uow.commit()

        restarted = _store(sessions)
        with pytest.raises(AggregateControlError) as raised:
            await restarted.prepare_aggregate_execution_context(
                admission.execution_id,
                identity=_identity(),
                agent=_agent(),
            )
        assert raised.value.code == "AGGREGATE_PROVENANCE_CHANGED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_f_activated_aggregate_can_complete_then_be_adopted(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_complete_adopt.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-complete-adopt",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f-complete-adopt",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )
        store = _store(sessions)
        bootstrap = await store.prepare_aggregate_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        activation = await store.activate_aggregate_execution(
            bootstrap,
            identity=_identity(),
        )

        aggregate_result = {"winner": "aggregate", "sources": list(ordered)}
        assert await service.finish_task_scoped_execution(
            source["task_id"],
            execution_id=admission.execution_id,
            source_revision=activation.activated_execution_revision,
            transition_values={
                "state": "COMPLETED",
                "wait_reason": None,
                "result": aggregate_result,
                "completed_at": datetime.now(timezone.utc),
            },
            delegated=bootstrap.context.parent_execution_id is not None,
        ) == 3

        adopted = await service.adopt_branch(
            source["task_id"],
            fork.branch_id,
            target_user_id="user-r8-d",
        )
        async with _Uow(sessions) as uow:
            task = await uow.agents.get_task(source["task_id"])
            target = await uow.agents.get_task_branch(fork.branch_id)
        assert adopted.selected_execution_id == admission.execution_id
        assert task.status == "COMPLETED"
        assert task.output == aggregate_result
        assert target.resolution_state == "ADOPTED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_f_post_activation_cleanup_releases_real_task_capacity(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_handoff_cleanup.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-handoff-cleanup",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f-handoff-cleanup",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )

        store = _store(sessions)
        bootstrap = await store.prepare_aggregate_execution_context(
            admission.execution_id,
            identity=_identity(),
            agent=_agent(),
        )
        activation = await store.activate_aggregate_execution(
            bootstrap,
            identity=_identity(),
        )
        before = await service.get_budget(source["task_id"])
        bootstrap.context.restore_active_budget(
            activation.remaining_active_budget_seconds
        )

        runtime = AgentRuntime(
            context_builder=None,
            inference=None,
            tool_execution=None,
            execution_policy=None,
            durable_store=store,
            task_budget_service=service,
        )
        await runtime.cancel_activated_aggregate_execution(
            bootstrap.context,
            activation.activated_execution_revision,
            error_message="AGGREGATE_RUNTIME_HANDOFF_FAILED: injected",
        )

        after = await service.get_budget(source["task_id"])
        execution = await store.load_execution(admission.execution_id)
        assert execution.state == "CANCELLED"
        assert execution.revision == 3
        assert execution.error == (
            "AGGREGATE_RUNTIME_HANDOFF_FAILED: injected"
        )
        assert after.active_executions == before.active_executions - 1
        expected_parallel_release = (
            1 if bootstrap.context.parent_execution_id is not None else 0
        )
        assert after.active_parallel_agents == (
            before.active_parallel_agents - expected_parallel_release
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r9_f_task_cancel_settles_dormant_aggregate_once(tmp_path):
    engine, sessions, service, planner = await _setup(
        tmp_path, name="r9_f_cancel_dormant.sqlite"
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-r9-f-cancel-dormant",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        admission = await service.aggregate_branches(
            source["task_id"],
            aggregate_request_id="aggregate-r9-f-cancel-dormant",
            target_branch_id=fork.branch_id,
            source_branch_ids=ordered,
            target_user_id="user-r8-d",
        )
        before = await service.get_budget(source["task_id"])
        await service.cancel_task(source["task_id"])
        after = await service.get_budget(source["task_id"])

        execution = await _store(sessions).load_execution(
            admission.execution_id
        )
        assert execution.state == "CANCELLED"
        assert execution.revision == 2
        assert execution.error == (
            "TASK_CANCELLED_BEFORE_AGGREGATE_ACTIVATION"
        )
        assert after.active_executions == before.active_executions - 1

        await service.cancel_task(source["task_id"])
        repeated = await service.get_budget(source["task_id"])
        assert repeated.active_executions == after.active_executions
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_ubq2_aggregate_never_adopts_recreated_taskbudget_incarnation(
    tmp_path,
):
    repository_cls = _AggregateIndependentRecreateBudgetRepository
    repository_cls.reset_race()
    engine, sessions, service, planner = await _setup(
        tmp_path,
        name="ubq2_aggregate_incarnation_revalidation.sqlite",
        repository_cls=repository_cls,
    )
    try:
        source, fork = await _seed_two_completed_branches(
            sessions,
            service,
            planner,
            task_id="task-ubq2-aggregate-incarnation",
        )
        ordered = (source["source_branch_id"], fork.branch_id)
        async with _Uow(sessions) as uow:
            before = await uow.agents.get_task_budget(source["task_id"])
            task_before = await uow.agents.get_task(source["task_id"])
            target_before = await uow.agents.get_task_branch(fork.branch_id)
            assert before is not None
            assert task_before is not None
            assert target_before is not None
            before_used = int(before.used_executions)
            before_active = int(before.active_executions)
            before_task_revision = int(task_before.revision)
            before_task_status = str(task_before.status)
            before_target_execution_id = target_before.current_execution_id
            reservation_count = await uow.session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_task_budget_reservations "
                    "WHERE task_id = :task_id AND kind = 'NEW_EXECUTION'"
                ),
                {"task_id": source["task_id"]},
            )
            before_new_execution_reservations = reservation_count.scalar_one()

        repository_cls.sessions = sessions
        repository_cls.armed = True
        with pytest.raises(AggregateAdmissionError) as raised:
            await service.aggregate_branches(
                source["task_id"],
                aggregate_request_id="aggregate-ubq2-incarnation-race",
                target_branch_id=fork.branch_id,
                source_branch_ids=ordered,
                target_user_id="user-r8-d",
            )
        assert raised.value.code == "AGGREGATE_TASK_BUDGET_INCARCATION_CHANGED"
        assert repository_cls.triggered is True
        assert repository_cls.old_generation is not None
        assert repository_cls.new_generation == repository_cls.old_generation + 1

        async with _Uow(sessions) as uow:
            budget = await uow.agents.get_task_budget(source["task_id"])
            task_after = await uow.agents.get_task(source["task_id"])
            target_after = await uow.agents.get_task_branch(fork.branch_id)
            receipt = await uow.agents.get_task_aggregate_admission(
                source["task_id"],
                "aggregate-ubq2-incarnation-race",
            )
            reservation_count = await uow.session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_task_budget_reservations "
                    "WHERE task_id = :task_id AND kind = 'NEW_EXECUTION'"
                ),
                {"task_id": source["task_id"]},
            )
            after_new_execution_reservations = reservation_count.scalar_one()
            assert budget is not None
            assert task_after is not None
            assert target_after is not None
            assert int(budget.incarnation_generation) == repository_cls.new_generation
            assert int(budget.used_executions) == before_used
            assert int(budget.active_executions) == before_active
            assert int(task_after.revision) == before_task_revision
            assert str(task_after.status) == before_task_status
            assert target_after.current_execution_id == before_target_execution_id
            assert (
                after_new_execution_reservations
                == before_new_execution_reservations
            )
            assert receipt is None
    finally:
        repository_cls.reset_race()
        await engine.dispose()

