from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.task_budget import TaskBudgetLimits
from se.src.infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentResumeClaimRecord,
    AgentTaskAggregateAdmissionRecord,
    AgentTaskBranchRecord,
    AgentTaskForkAdmissionRecord,
    AgentTaskRecord,
    AgentTaskRetryAdmissionRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.fork_planning import (
    ForkPlanRejected,
    _delegation_depth_in_uow,
    _load_fork_safe_transcript_in_uow,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    RetryControlError,
)
from se.src.runtimes.agent.task_budget import (
    TaskBudgetConflictError,
    TaskBudgetService,
)


def _columns(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _fk_ondelete(model, column_name: str) -> set[str | None]:
    column = model.__table__.c[column_name]
    return {foreign_key.ondelete for foreign_key in column.foreign_keys}


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


def _limits() -> TaskBudgetLimits:
    return TaskBudgetLimits(
        max_total_executions=4,
        max_active_executions=2,
        max_active_branches=2,
        max_parallel_agents=1,
        max_total_tool_calls=4,
        max_total_inference_calls=4,
        max_delegation_depth=2,
        max_total_tokens=100,
        max_total_cost_usd="1.0",
    )


def test_r11_f0_minimum_agent_live_root_fields_are_represented():
    assert {
        "state",
        "current_checkpoint_id",
        "base_checkpoint_id",
        "task_id",
        "branch_id",
    } <= _columns(AgentExecutionRecord)

    assert {
        "checkpoint_id",
        "execution_id",
        "parent_checkpoint_id",
        "transcript_snapshot",
        "transcript_ref",
        "transcript_version",
    } <= _columns(AgentExecutionCheckpointRecord)

    assert {
        "task_id",
        "base_checkpoint_id",
        "current_execution_id",
        "resolution_state",
    } <= _columns(AgentTaskBranchRecord)

    assert {
        "execution_id",
        "checkpoint_id",
        "state",
        "claim_expires_at",
    } <= _columns(AgentResumeClaimRecord)

    assert {
        "source_branch_id",
        "source_execution_id",
        "source_checkpoint_id",
        "branch_id",
        "execution_id",
        "runtime_seed_json",
        "runtime_seed_fingerprint",
    } <= _columns(AgentTaskForkAdmissionRecord)

    assert {
        "branch_id",
        "source_execution_id",
        "source_checkpoint_id",
        "execution_id",
        "plan_fingerprint",
    } <= _columns(AgentTaskRetryAdmissionRecord)

    assert {
        "target_branch_id",
        "execution_id",
        "source_branch_snapshots",
        "source_execution_snapshots",
        "result_fingerprints",
        "runtime_seed_fingerprint",
    } <= _columns(AgentTaskAggregateAdmissionRecord)


def test_r11_f0_checkpoint_and_receipt_fk_semantics_preserve_live_authority():
    assert _fk_ondelete(
        AgentCheckpointPendingInvocationRecord,
        "checkpoint_id",
    ) == {"CASCADE"}
    assert _fk_ondelete(AgentResumeClaimRecord, "checkpoint_id") == {"CASCADE"}
    assert _fk_ondelete(AgentTaskBranchRecord, "base_checkpoint_id") == {
        "RESTRICT"
    }
    assert _fk_ondelete(
        AgentTaskForkAdmissionRecord,
        "source_checkpoint_id",
    ) == {"RESTRICT"}
    assert _fk_ondelete(
        AgentTaskRetryAdmissionRecord,
        "source_checkpoint_id",
    ) == {"RESTRICT"}


def test_r11_f0_task_budget_physical_edges_do_not_replace_replay_authority():
    assert {"task_id", "revision", "state"} <= _columns(TaskBudgetRecord)
    assert {
        "task_id",
        "kind",
        "reservation_key",
        "payload_fingerprint",
    } <= _columns(TaskBudgetReservationRecord)
    assert _fk_ondelete(TaskBudgetRecord, "task_id") == {"CASCADE"}
    assert _fk_ondelete(TaskBudgetReservationRecord, "task_id") == {"CASCADE"}


@pytest.mark.asyncio
async def test_r11_f0_task_budget_reservation_is_durable_replay_authority(tmp_path):
    database = tmp_path / "r11_f0_task_budget.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service = TaskBudgetService(lambda: _Uow(sessions))

        async with sessions() as session:
            session.add(
                AgentTaskRecord(
                    id="task-r11-f0-replay",
                    session_id="session-r11-f0-replay",
                    created_by="r11-f0",
                    assigned_agent_id="agent-r11-f0",
                    status="CREATED",
                    input={},
                )
            )
            await session.commit()

        await service.ensure_budget("task-r11-f0-replay", _limits())
        first = await service.reserve_tool_calls(
            "task-r11-f0-replay",
            reservation_key="logical-tool-call-1",
            count=1,
        )
        replay = await service.reserve_tool_calls(
            "task-r11-f0-replay",
            reservation_key="logical-tool-call-1",
            count=1,
        )

        assert first.used_tool_calls == replay.used_tool_calls == 1
        assert first.revision == replay.revision

        with pytest.raises(TaskBudgetConflictError):
            await service.reserve_tool_calls(
                "task-r11-f0-replay",
                reservation_key="logical-tool-call-1",
                count=2,
            )

        final_budget = await service.get_budget("task-r11-f0-replay")
        assert final_budget is not None
        assert final_budget.used_tool_calls == 1
        assert final_budget.revision == first.revision
    finally:
        await engine.dispose()


def test_r11_f0_transcript_reachability_edges_are_explicit_and_restrictive():
    checkpoint_columns = _columns(AgentExecutionCheckpointRecord)
    assert {"transcript_ref", "transcript_version"} <= checkpoint_columns

    representation_columns = _columns(AgentTranscriptRepresentationRecord)
    assert {
        "transcript_ref",
        "transcript_version",
        "parent_transcript_ref",
        "parent_transcript_version",
        "payload_root_ref",
    } <= representation_columns

    payload_columns = _columns(AgentTranscriptPayloadNodeRecord)
    assert {
        "payload_root_ref",
        "parent_payload_root_ref",
        "chunk_id",
    } <= payload_columns

    assert {"chunk_id", "payload"} <= _columns(AgentTranscriptChunkRecord)

    rep_fk_constraints = {
        constraint.ondelete
        for constraint in AgentTranscriptRepresentationRecord.__table__.foreign_key_constraints
        if {
            element.parent.name for element in constraint.elements
        } == {"parent_transcript_ref", "parent_transcript_version"}
    }
    assert rep_fk_constraints == {"RESTRICT"}

    assert _fk_ondelete(
        AgentTranscriptRepresentationRecord,
        "payload_root_ref",
    ) == {"RESTRICT"}
    assert _fk_ondelete(
        AgentTranscriptPayloadNodeRecord,
        "parent_payload_root_ref",
    ) == {"RESTRICT"}
    assert _fk_ondelete(
        AgentTranscriptPayloadNodeRecord,
        "chunk_id",
    ) == {"RESTRICT"}


def test_r11_f0_checkpoint_parent_is_not_transcript_parent_authority():
    assert "parent_checkpoint_id" in _columns(AgentExecutionCheckpointRecord)
    assert {
        "parent_transcript_ref",
        "parent_transcript_version",
    } <= _columns(AgentTranscriptRepresentationRecord)


class _ForkEvidenceAgents:
    def __init__(self, *, iterations=(), results=None):
        self.iterations = tuple(iterations)
        self.results = dict(results or {})

    async def list_checkpoint_pending_invocations(self, checkpoint_id):
        return ()

    async def list_iterations(self, execution_id):
        return self.iterations

    async def get_tool_result(self, execution_id, tool_call_id):
        return self.results.get(tool_call_id)


class _ForkEvidenceInvocations:
    def __init__(self, records=None):
        self.records = dict(records or {})

    async def get_record(self, invocation_id):
        return self.records.get(invocation_id)


class _ForkEvidenceUow:
    def __init__(self, *, iterations=(), results=None, invocations=None):
        self.agents = _ForkEvidenceAgents(
            iterations=iterations,
            results=results,
        )
        self.capability_invocations = _ForkEvidenceInvocations(invocations)


def _fork_checkpoint():
    return SimpleNamespace(
        checkpoint_id="cp-r11-f0",
        iteration=4,
        transcript_snapshot=(
            {
                "role": "user",
                "content": "base",
                "tool_calls": [],
                "name": None,
                "tool_call_id": None,
                "metadata": {},
            },
        ),
        transcript_ref=None,
        transcript_version=None,
    )


def _fork_iteration():
    return SimpleNamespace(
        id="iter-r11-f0",
        iteration=4,
        tool_call_ids=["call-r11-f0"],
    )


def _fork_committed_result(**updates):
    values = {
        "execution_id": "exec-r11-f0",
        "iteration_id": "iter-r11-f0",
        "invocation_id": "inv-r11-f0",
        "tool_call_id": "call-r11-f0",
        "capability_id": "tool.r11-f0",
        "success": True,
        "output": {"ok": True},
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "attempt": 1,
        "commit_state": "COMMITTED",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _fork_invocation(**updates):
    values = {
        "invocation_id": "inv-r11-f0",
        "execution_id": "exec-r11-f0",
        "tool_call_id": "call-r11-f0",
        "capability_id": "tool.r11-f0",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_r11_f0_execution_replay_physical_and_semantic_edges_are_explicit():
    assert {"execution_id", "iteration", "tool_call_ids"} <= _columns(
        AgentIterationRecord
    )
    assert {
        "execution_id",
        "iteration_id",
        "invocation_id",
        "tool_call_id",
        "capability_id",
    } <= _columns(AgentToolCallRecord)
    assert {
        "execution_id",
        "iteration_id",
        "invocation_id",
        "tool_call_id",
        "capability_id",
        "commit_state",
    } <= _columns(AgentToolResultRecord)

    assert _fk_ondelete(AgentIterationRecord, "execution_id") == {"CASCADE"}
    assert _fk_ondelete(AgentToolCallRecord, "execution_id") == {"CASCADE"}
    assert _fk_ondelete(AgentToolCallRecord, "iteration_id") == {"CASCADE"}
    assert _fk_ondelete(AgentToolResultRecord, "execution_id") == {"CASCADE"}
    assert _fk_ondelete(AgentToolResultRecord, "iteration_id") == {"CASCADE"}

    assert {
        "invocation_id",
        "execution_id",
        "tool_call_id",
        "capability_id",
        "state",
        "remote_outcome_state",
    } <= _columns(CapabilityInvocationRecord)
    # execution_id is a semantic Agent<->Capability GC edge, not a SQL FK.
    assert _fk_ondelete(CapabilityInvocationRecord, "execution_id") == set()
    assert _fk_ondelete(
        CapabilityInvocationAttemptRecord,
        "invocation_id",
    ) == {"CASCADE"}


@pytest.mark.asyncio
async def test_r11_f0_real_fork_reader_fails_closed_without_checkpoint_iteration():
    uow = _ForkEvidenceUow(iterations=())

    with pytest.raises(ForkPlanRejected) as exc:
        await _load_fork_safe_transcript_in_uow(
            uow,
            "exec-r11-f0",
            _fork_checkpoint(),
        )
    assert exc.value.code == "FORK_TRANSCRIPT_UNSAFE"


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_state", [None, "PROVISIONAL"])
async def test_r11_f0_real_fork_reader_requires_durable_committed_result(
    commit_state,
):
    results = {}
    if commit_state is not None:
        results["call-r11-f0"] = _fork_committed_result(
            commit_state=commit_state,
        )
    uow = _ForkEvidenceUow(
        iterations=(_fork_iteration(),),
        results=results,
        invocations={"inv-r11-f0": _fork_invocation()},
    )

    with pytest.raises(ForkPlanRejected) as exc:
        await _load_fork_safe_transcript_in_uow(
            uow,
            "exec-r11-f0",
            _fork_checkpoint(),
        )
    assert exc.value.code == "FORK_TRANSCRIPT_UNSAFE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invocation",
    [
        None,
        _fork_invocation(execution_id="exec-other"),
        _fork_invocation(tool_call_id="call-other"),
        _fork_invocation(capability_id="tool.other"),
    ],
)
async def test_r11_f0_real_fork_reader_requires_matching_invocation_authority(
    invocation,
):
    invocations = {}
    if invocation is not None:
        invocations["inv-r11-f0"] = invocation
    uow = _ForkEvidenceUow(
        iterations=(_fork_iteration(),),
        results={"call-r11-f0": _fork_committed_result()},
        invocations=invocations,
    )

    with pytest.raises(ForkPlanRejected) as exc:
        await _load_fork_safe_transcript_in_uow(
            uow,
            "exec-r11-f0",
            _fork_checkpoint(),
        )
    assert exc.value.code == "FORK_COMMITTED_RESULT_CONFLICT"


@pytest.mark.asyncio
async def test_r11_f0_real_fork_reader_accepts_complete_retained_authority():
    uow = _ForkEvidenceUow(
        iterations=(_fork_iteration(),),
        results={"call-r11-f0": _fork_committed_result()},
        invocations={"inv-r11-f0": _fork_invocation()},
    )

    transcript = await _load_fork_safe_transcript_in_uow(
        uow,
        "exec-r11-f0",
        _fork_checkpoint(),
    )

    assert [item.role for item in transcript] == ["user", "tool"]
    assert transcript[-1].tool_call_id == "call-r11-f0"
    assert transcript[-1].content == {"ok": True}


class _LineageAgents:
    def __init__(self, executions=None):
        self.executions = dict(executions or {})

    async def get_execution(self, execution_id):
        return self.executions.get(execution_id)


class _LineageUow:
    def __init__(self, executions=None):
        self.agents = _LineageAgents(executions)


class _RetryLineageAgents:
    def __init__(self, *, retry_execution, source_execution=None):
        self.retry_execution = retry_execution
        self.source_execution = source_execution

    async def get_execution(self, execution_id):
        if execution_id == self.retry_execution.id:
            return self.retry_execution
        if execution_id == self.retry_execution.retry_of_execution_id:
            return self.source_execution
        return None

    async def get_task_retry_admission_by_execution(self, execution_id):
        return SimpleNamespace()

    async def get_task(self, task_id):
        return SimpleNamespace()

    async def get_task_branch(self, branch_id):
        return SimpleNamespace()


class _RetryLineageUow:
    def __init__(self, *, retry_execution, source_execution=None):
        self.agents = _RetryLineageAgents(
            retry_execution=retry_execution,
            source_execution=source_execution,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        return None


def _retry_execution_for_lineage():
    return SimpleNamespace(
        id="retry-r11-f0",
        state="RUNNING",
        revision=1,
        current_checkpoint_id=None,
        task_id="task-r11-f0-lineage",
        branch_id="branch-r11-f0-lineage",
        retry_of_execution_id="source-r11-f0",
        bound_client_id=None,
        bound_connection_id=None,
    )


def test_r11_f0_execution_lineage_columns_are_semantic_not_fk_edges():
    assert {
        "parent_execution_id",
        "retry_of_execution_id",
        "base_execution_id",
        "base_checkpoint_id",
    } <= _columns(AgentExecutionRecord)

    for column_name in (
        "parent_execution_id",
        "retry_of_execution_id",
        "base_execution_id",
        "base_checkpoint_id",
    ):
        assert _fk_ondelete(AgentExecutionRecord, column_name) == set()


@pytest.mark.asyncio
async def test_r11_f0_real_delegation_reader_fails_closed_when_parent_is_missing():
    child = SimpleNamespace(
        id="child-r11-f0",
        task_id="task-r11-f0-lineage",
        parent_execution_id="parent-r11-f0",
    )
    uow = _LineageUow(executions={})

    with pytest.raises(ForkPlanRejected) as exc:
        await _delegation_depth_in_uow(uow, child)
    assert exc.value.code == "FORK_EXECUTION_LINEAGE_CONFLICT"


@pytest.mark.asyncio
async def test_r11_f0_real_delegation_reader_accepts_retained_parent_chain():
    parent = SimpleNamespace(
        id="parent-r11-f0",
        task_id="task-r11-f0-lineage",
        parent_execution_id=None,
    )
    child = SimpleNamespace(
        id="child-r11-f0",
        task_id="task-r11-f0-lineage",
        parent_execution_id=parent.id,
    )
    uow = _LineageUow(executions={parent.id: parent})

    assert await _delegation_depth_in_uow(uow, child) == 1


@pytest.mark.asyncio
async def test_r11_f0_real_delegation_reader_fails_closed_on_lineage_cycle():
    parent = SimpleNamespace(
        id="parent-r11-f0-cycle",
        task_id="task-r11-f0-lineage",
        parent_execution_id="child-r11-f0-cycle",
    )
    child = SimpleNamespace(
        id="child-r11-f0-cycle",
        task_id="task-r11-f0-lineage",
        parent_execution_id=parent.id,
    )
    uow = _LineageUow(
        executions={
            parent.id: parent,
            child.id: child,
        }
    )

    with pytest.raises(ForkPlanRejected) as exc:
        await _delegation_depth_in_uow(uow, child)
    assert exc.value.code == "FORK_EXECUTION_LINEAGE_CONFLICT"


@pytest.mark.asyncio
async def test_r11_f0_retry_bootstrap_fails_closed_when_retry_source_is_missing():
    retry_execution = _retry_execution_for_lineage()
    store = DurableAgentStore(
        lambda: _RetryLineageUow(
            retry_execution=retry_execution,
            source_execution=None,
        )
    )

    with pytest.raises(RetryControlError) as exc:
        await store.prepare_retry_execution_context(
            retry_execution.id,
            identity=SimpleNamespace(user_id="user-r11-f0"),
            agent=SimpleNamespace(name="agent-r11-f0"),
        )
    assert exc.value.code == "RETRY_ADMISSION_CORRUPT"
