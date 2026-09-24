from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from ..interfaces.repository import BaseRepository
from ..transcript_representation import (
    HARD_MAX_DELTA_DEPTH,
    canonical_json_bytes,
    canonical_transcript_messages,
    logical_transcript_fingerprint,
    transcript_chunk_id,
    transcript_payload_root_ref,
    transcript_representation_ref,
)
from ..models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentMessageRecord,
    AgentResumeClaimRecord,
    AgentSessionMemberRecord,
    AgentSessionRecord,
    AgentTaskRecord,
    AgentTaskBranchContextRecord,
    AgentTaskBranchRecord,
    AgentTaskForkAdmissionRecord,
    AgentTaskRetryAdmissionRecord,
    AgentTaskAggregateAdmissionRecord,
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
)


_TASK_BRANCH_MUTABLE_FIELDS = frozenset({
    "current_execution_id",
    "resolution_state",
})


class AgentRepository(BaseRepository):
    """Transactional persistence boundary for multi-agent state."""

    def __init__(self, session):
        self.session = session

    async def _insert_immutable_do_nothing(
        self,
        model,
        values: Dict[str, Any],
        *,
        conflict_columns: tuple[str, ...],
    ) -> bool:
        """Insert one immutable identity without poisoning replay races.

        SQLite/PostgreSQL use native ON CONFLICT DO NOTHING so a concurrent
        duplicate does not invalidate the outer UoW. Other dialects fall back
        to an isolated savepoint; the caller always re-reads and validates the
        immutable winner after a no-op/conflict.
        """

        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            statement = (
                sqlite_insert(model)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=list(conflict_columns)
                )
            )
            result = await self.session.execute(statement)
            return result.rowcount == 1

        if dialect == "postgresql":
            statement = (
                postgresql_insert(model)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=list(conflict_columns)
                )
            )
            result = await self.session.execute(statement)
            return result.rowcount == 1

        try:
            async with self.session.begin_nested():
                self.session.add(model(**values))
                await self.session.flush()
            return True
        except IntegrityError:
            return False

    async def create_session(self, session_id: str, owner_user_id: str, agent_ids: List[str]):
        session = AgentSessionRecord(id=session_id, owner_user_id=owner_user_id)
        self.session.add(session)
        for agent_id in agent_ids:
            self.session.add(AgentSessionMemberRecord(session_id=session_id, agent_id=agent_id))
        await self.session.flush()
        return session

    async def get_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        result = await self.session.execute(
            select(AgentSessionRecord).where(AgentSessionRecord.id == session_id)
        )
        return result.scalar_one_or_none()

    async def add_member(self, session_id: str, agent_id: str):
        self.session.add(AgentSessionMemberRecord(session_id=session_id, agent_id=agent_id))
        await self.session.flush()

    async def list_members(self, session_id: str) -> List[str]:
        result = await self.session.execute(
            select(AgentSessionMemberRecord.agent_id).where(
                AgentSessionMemberRecord.session_id == session_id
            )
        )
        return list(result.scalars().all())

    async def save_message(self, values: Dict[str, Any]):
        record = AgentMessageRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_messages(self, session_id: str) -> List[AgentMessageRecord]:
        result = await self.session.execute(
            select(AgentMessageRecord)
            .where(AgentMessageRecord.session_id == session_id)
            .order_by(AgentMessageRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def save_task(self, values: Dict[str, Any]):
        record = AgentTaskRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task(self, task_id: str) -> Optional[AgentTaskRecord]:
        result = await self.session.execute(
            select(AgentTaskRecord).where(AgentTaskRecord.id == task_id)
        )
        return result.scalar_one_or_none()

    async def get_task_for_update(
        self,
        task_id: str,
    ) -> Optional[AgentTaskRecord]:
        """Lock one AgentTask revision for an atomic multi-row admission.

        PostgreSQL/MySQL honor FOR UPDATE. SQLite safely compiles this as its
        dialect permits while the existing CAS/transaction fences remain the
        test/runtime backstop.
        """
        result = await self.session.execute(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.id == task_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def compare_and_set_task(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentTaskRecord)
            .where(
                AgentTaskRecord.id == task_id,
                AgentTaskRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task(task_id)

    async def compare_and_set_task_activity(
        self,
        task_id: str,
        expected_revision: int,
        *,
        target_state: str,
        wait_reasons: List[str],
    ):
        """CAS derived R8 Task activity against the live branch-head graph.

        Task row locks serialize row-locking databases. These EXISTS predicates
        are the SQLite/dialect backstop so a FORK/resume that commits a RUNNING
        current branch between snapshot and UPDATE makes a stale WAITING write
        lose instead of committing split-brain state.
        """

        target = str(target_state)
        if target not in {"RUNNING", "WAITING"}:
            raise ValueError(
                "Task activity target must be RUNNING or WAITING."
            )

        active_heads = (
            select(AgentTaskBranchRecord.branch_id)
            .join(
                AgentExecutionRecord,
                AgentExecutionRecord.id
                == AgentTaskBranchRecord.current_execution_id,
            )
            .where(
                AgentTaskBranchRecord.task_id == task_id,
                AgentTaskBranchRecord.resolution_state == "OPEN",
                AgentExecutionRecord.state.in_(("CREATED", "RUNNING")),
            )
        )
        waiting_heads = (
            select(AgentTaskBranchRecord.branch_id)
            .join(
                AgentExecutionRecord,
                AgentExecutionRecord.id
                == AgentTaskBranchRecord.current_execution_id,
            )
            .where(
                AgentTaskBranchRecord.task_id == task_id,
                AgentTaskBranchRecord.resolution_state == "OPEN",
                AgentExecutionRecord.state.in_(
                    ("WAITING", "WAITING_FOR_CONNECTION")
                ),
            )
        )

        statement = update(AgentTaskRecord).where(
            AgentTaskRecord.id == task_id,
            AgentTaskRecord.revision == expected_revision,
        )
        if target == "RUNNING":
            statement = statement.where(active_heads.exists())
        else:
            expected_reasons = tuple(sorted({str(item) for item in wait_reasons}))
            if not expected_reasons:
                raise ValueError(
                    "WAITING Task activity requires at least one wait reason."
                )

            normalized_reason = case(
                (
                    and_(
                        AgentExecutionRecord.wait_reason.is_not(None),
                        AgentExecutionRecord.wait_reason != "",
                    ),
                    AgentExecutionRecord.wait_reason,
                ),
                (
                    AgentExecutionRecord.state == "WAITING_FOR_CONNECTION",
                    "CONNECTION",
                ),
                else_=None,
            )
            live_waiting_reason_rows = (
                select(normalized_reason.label("normalized_wait_reason"))
                .select_from(AgentTaskBranchRecord)
                .join(
                    AgentExecutionRecord,
                    AgentExecutionRecord.id
                    == AgentTaskBranchRecord.current_execution_id,
                )
                .where(
                    AgentTaskBranchRecord.task_id == task_id,
                    AgentTaskBranchRecord.resolution_state == "OPEN",
                    AgentExecutionRecord.state.in_(
                        ("WAITING", "WAITING_FOR_CONNECTION")
                    ),
                    normalized_reason.is_not(None),
                )
            )

            # Exact-set proof: no live normalized reason may fall outside the
            # derived snapshot, and every expected reason must still be
            # represented by at least one current OPEN WAITING branch head.
            unexpected_reason = live_waiting_reason_rows.where(
                ~normalized_reason.in_(expected_reasons)
            )
            statement = statement.where(
                ~active_heads.exists(),
                waiting_heads.exists(),
                ~unexpected_reason.exists(),
            )
            for reason in expected_reasons:
                expected_reason_exists = live_waiting_reason_rows.where(
                    normalized_reason == reason
                )
                statement = statement.where(
                    expected_reason_exists.exists()
                )

        result = await self.session.execute(
            statement.values(
                status=target,
                wait_reasons=list(wait_reasons),
                revision=expected_revision + 1,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task(task_id)

    async def save_task_branch(self, values: Dict[str, Any]):
        record = AgentTaskBranchRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_branch(self, branch_id: str):
        result = await self.session.execute(
            select(AgentTaskBranchRecord).where(
                AgentTaskBranchRecord.branch_id == branch_id
            )
        )
        return result.scalar_one_or_none()

    async def get_task_branch_for_update(self, branch_id: str):
        result = await self.session.execute(
            select(AgentTaskBranchRecord)
            .where(AgentTaskBranchRecord.branch_id == branch_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_task_branches(self, task_id: str):
        result = await self.session.execute(
            select(AgentTaskBranchRecord)
            .where(AgentTaskBranchRecord.task_id == task_id)
            .order_by(
                AgentTaskBranchRecord.created_at.asc(),
                AgentTaskBranchRecord.branch_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def compare_and_set_task_branch(
        self,
        branch_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        unexpected = set(values) - _TASK_BRANCH_MUTABLE_FIELDS
        if unexpected:
            raise ValueError(
                "TaskBranch immutable fields cannot be changed by CAS: "
                + ", ".join(sorted(unexpected))
            )
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentTaskBranchRecord)
            .where(
                AgentTaskBranchRecord.branch_id == branch_id,
                AgentTaskBranchRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task_branch(branch_id)

    async def save_task_branch_context(self, values: Dict[str, Any]):
        record = AgentTaskBranchContextRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_branch_context(self, branch_id: str):
        result = await self.session.execute(
            select(AgentTaskBranchContextRecord).where(
                AgentTaskBranchContextRecord.branch_id == branch_id
            )
        )
        return result.scalar_one_or_none()

    async def get_task_branch_context_for_update(self, branch_id: str):
        result = await self.session.execute(
            select(AgentTaskBranchContextRecord)
            .where(AgentTaskBranchContextRecord.branch_id == branch_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def compare_and_set_task_branch_context(
        self,
        branch_id: str,
        expected_revision: int,
        overlay_messages: List[Dict[str, Any]],
    ):
        result = await self.session.execute(
            update(AgentTaskBranchContextRecord)
            .where(
                AgentTaskBranchContextRecord.branch_id == branch_id,
                AgentTaskBranchContextRecord.revision == expected_revision,
            )
            .values(
                overlay_messages=overlay_messages,
                revision=expected_revision + 1,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task_branch_context(branch_id)

    async def save_task_fork_admission(
        self,
        values: Dict[str, Any],
    ):
        record = AgentTaskForkAdmissionRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_fork_admission(
        self,
        task_id: str,
        fork_request_id: str,
    ):
        result = await self.session.execute(
            select(AgentTaskForkAdmissionRecord).where(
                AgentTaskForkAdmissionRecord.task_id == task_id,
                AgentTaskForkAdmissionRecord.fork_request_id
                == fork_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_task_fork_admission_by_execution(
        self,
        execution_id: str,
    ):
        result = await self.session.execute(
            select(AgentTaskForkAdmissionRecord).where(
                AgentTaskForkAdmissionRecord.execution_id == execution_id
            )
        )
        return result.scalar_one_or_none()

    async def list_task_fork_admissions(self, task_id: str):
        result = await self.session.execute(
            select(AgentTaskForkAdmissionRecord)
            .where(AgentTaskForkAdmissionRecord.task_id == task_id)
            .order_by(
                AgentTaskForkAdmissionRecord.created_at.asc(),
                AgentTaskForkAdmissionRecord.fork_request_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def save_task_retry_admission(
        self,
        values: Dict[str, Any],
    ):
        record = AgentTaskRetryAdmissionRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_retry_admission(
        self,
        task_id: str,
        retry_request_id: str,
    ):
        result = await self.session.execute(
            select(AgentTaskRetryAdmissionRecord).where(
                AgentTaskRetryAdmissionRecord.task_id == task_id,
                AgentTaskRetryAdmissionRecord.retry_request_id
                == retry_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_task_retry_admission_for_update(
        self,
        task_id: str,
        retry_request_id: str,
    ):
        result = await self.session.execute(
            select(AgentTaskRetryAdmissionRecord)
            .where(
                AgentTaskRetryAdmissionRecord.task_id == task_id,
                AgentTaskRetryAdmissionRecord.retry_request_id
                == retry_request_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_task_retry_admission_by_execution(
        self,
        execution_id: str,
    ):
        result = await self.session.execute(
            select(AgentTaskRetryAdmissionRecord).where(
                AgentTaskRetryAdmissionRecord.execution_id == execution_id
            )
        )
        return result.scalar_one_or_none()

    async def list_task_retry_admissions(self, task_id: str):
        result = await self.session.execute(
            select(AgentTaskRetryAdmissionRecord)
            .where(AgentTaskRetryAdmissionRecord.task_id == task_id)
            .order_by(
                AgentTaskRetryAdmissionRecord.created_at.asc(),
                AgentTaskRetryAdmissionRecord.retry_request_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def save_task_aggregate_admission(self, values: Dict[str, Any]):
        record = AgentTaskAggregateAdmissionRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_aggregate_admission(
        self, task_id: str, aggregate_request_id: str
    ):
        result = await self.session.execute(
            select(AgentTaskAggregateAdmissionRecord).where(
                AgentTaskAggregateAdmissionRecord.task_id == task_id,
                AgentTaskAggregateAdmissionRecord.aggregate_request_id
                == aggregate_request_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_task_aggregate_admission_by_execution(
        self, execution_id: str
    ):
        result = await self.session.execute(
            select(AgentTaskAggregateAdmissionRecord).where(
                AgentTaskAggregateAdmissionRecord.execution_id == execution_id
            )
        )
        return result.scalar_one_or_none()

    async def list_task_aggregate_admissions(self, task_id: str):
        result = await self.session.execute(
            select(AgentTaskAggregateAdmissionRecord)
            .where(AgentTaskAggregateAdmissionRecord.task_id == task_id)
            .order_by(
                AgentTaskAggregateAdmissionRecord.created_at.asc(),
                AgentTaskAggregateAdmissionRecord.aggregate_request_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def list_task_branches_for_update(self, task_id: str):
        """Lock all Task branches in the frozen deterministic branch_id order."""

        result = await self.session.execute(
            select(AgentTaskBranchRecord)
            .where(AgentTaskBranchRecord.task_id == task_id)
            .order_by(AgentTaskBranchRecord.branch_id.asc())
            .with_for_update()
        )
        return list(result.scalars().all())

    async def save_task_budget(self, values: Dict[str, Any]):
        record = TaskBudgetRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_budget(self, task_id: str):
        result = await self.session.execute(
            select(TaskBudgetRecord).where(
                TaskBudgetRecord.task_id == task_id
            )
        )
        return result.scalar_one_or_none()

    async def get_task_budget_for_update(self, task_id: str):
        result = await self.session.execute(
            select(TaskBudgetRecord)
            .where(TaskBudgetRecord.task_id == task_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def compare_and_set_task_budget(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(TaskBudgetRecord)
            .where(
                TaskBudgetRecord.task_id == task_id,
                TaskBudgetRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_task_budget(task_id)

    async def save_task_budget_reservation(
        self,
        values: Dict[str, Any],
    ):
        record = TaskBudgetReservationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_task_budget_reservation(
        self,
        task_id: str,
        kind: str,
        reservation_key: str,
    ):
        result = await self.session.execute(
            select(TaskBudgetReservationRecord).where(
                TaskBudgetReservationRecord.task_id == task_id,
                TaskBudgetReservationRecord.kind == kind,
                TaskBudgetReservationRecord.reservation_key
                == reservation_key,
            )
        )
        return result.scalar_one_or_none()

    async def has_execution_for_task(self, task_id: str) -> bool:
        result = await self.session.execute(
            select(AgentExecutionRecord.id)
            .where(AgentExecutionRecord.task_id == task_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def save_execution(self, values: Dict[str, Any]):
        record = AgentExecutionRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_execution(self, execution_id: str):
        result = await self.session.execute(
            select(AgentExecutionRecord).where(AgentExecutionRecord.id == execution_id)
        )
        return result.scalar_one_or_none()

    async def get_execution_for_update(self, execution_id: str):
        result = await self.session.execute(
            select(AgentExecutionRecord)
            .where(AgentExecutionRecord.id == execution_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def compare_and_set_fork_activation(
        self,
        execution_id: str,
        *,
        task_id: str,
        branch_id: str,
        base_execution_id: str,
        base_checkpoint_id: str,
        started_at: datetime,
    ):
        """Specialized R8-F RUNNING@1 -> RUNNING@2 activation CAS.

        The mutation surface is intentionally closed: callers can supply only
        the activation timestamp. State/lineage/budget/affinity cannot be
        rewritten through this authority primitive.
        """

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == 1,
                AgentExecutionRecord.state == "RUNNING",
                AgentExecutionRecord.current_checkpoint_id.is_(None),
                AgentExecutionRecord.task_id == task_id,
                AgentExecutionRecord.branch_id == branch_id,
                AgentExecutionRecord.base_execution_id == base_execution_id,
                AgentExecutionRecord.base_checkpoint_id == base_checkpoint_id,
                AgentExecutionRecord.retry_of_execution_id.is_(None),
                AgentExecutionRecord.bound_client_id.is_(None),
                AgentExecutionRecord.bound_connection_id.is_(None),
            )
            .values(
                revision=2,
                state="RUNNING",
                started_at=started_at,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def compare_and_set_aggregate_activation(
        self,
        execution_id: str,
        *,
        task_id: str,
        branch_id: str,
        base_execution_id: str,
        base_checkpoint_id: str | None,
        started_at: datetime,
    ):
        """Specialized R9-F RUNNING@1 -> RUNNING@2 aggregate activation CAS."""

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == 1,
                AgentExecutionRecord.state == "RUNNING",
                AgentExecutionRecord.current_checkpoint_id.is_(None),
                AgentExecutionRecord.task_id == task_id,
                AgentExecutionRecord.branch_id == branch_id,
                AgentExecutionRecord.base_execution_id == base_execution_id,
                AgentExecutionRecord.base_checkpoint_id == base_checkpoint_id,
                AgentExecutionRecord.retry_of_execution_id.is_(None),
                AgentExecutionRecord.bound_client_id.is_(None),
                AgentExecutionRecord.bound_connection_id.is_(None),
            )
            .values(
                revision=2,
                state="RUNNING",
                started_at=started_at,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def compare_and_set_retry_activation(
        self,
        execution_id: str,
        *,
        task_id: str,
        branch_id: str,
        source_execution_id: str,
        started_at: datetime,
    ):
        """Specialized R9-C RUNNING@1 -> RUNNING@2 retry activation CAS."""

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == 1,
                AgentExecutionRecord.state == "RUNNING",
                AgentExecutionRecord.current_checkpoint_id.is_(None),
                AgentExecutionRecord.task_id == task_id,
                AgentExecutionRecord.branch_id == branch_id,
                AgentExecutionRecord.retry_of_execution_id
                == source_execution_id,
                AgentExecutionRecord.bound_client_id.is_(None),
                AgentExecutionRecord.bound_connection_id.is_(None),
            )
            .values(
                revision=2,
                state="RUNNING",
                started_at=started_at,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def compare_and_set_retry_preactivation_cancel(
        self,
        execution_id: str,
        *,
        task_id: str,
        branch_id: str,
        source_execution_id: str,
        completed_at: datetime,
        error: str = "TASK_CANCELLED_BEFORE_RETRY_ACTIVATION",
    ):
        """Race Task cancellation against R9-C activation on revision 1."""

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == 1,
                AgentExecutionRecord.state == "RUNNING",
                AgentExecutionRecord.current_checkpoint_id.is_(None),
                AgentExecutionRecord.task_id == task_id,
                AgentExecutionRecord.branch_id == branch_id,
                AgentExecutionRecord.retry_of_execution_id
                == source_execution_id,
                AgentExecutionRecord.bound_client_id.is_(None),
                AgentExecutionRecord.bound_connection_id.is_(None),
            )
            .values(
                revision=2,
                state="CANCELLED",
                wait_reason=None,
                wait_expires_at=None,
                error=error,
                completed_at=completed_at,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def compare_and_set_fork_preactivation_cancel(
        self,
        execution_id: str,
        *,
        task_id: str,
        branch_id: str,
        base_execution_id: str,
        base_checkpoint_id: str,
        completed_at: datetime,
        error: str = "TASK_CANCELLED_BEFORE_FORK_ACTIVATION",
    ):
        """Race Task cancellation against R8-F activation on revision 1."""

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == 1,
                AgentExecutionRecord.state == "RUNNING",
                AgentExecutionRecord.current_checkpoint_id.is_(None),
                AgentExecutionRecord.task_id == task_id,
                AgentExecutionRecord.branch_id == branch_id,
                AgentExecutionRecord.base_execution_id == base_execution_id,
                AgentExecutionRecord.base_checkpoint_id == base_checkpoint_id,
                AgentExecutionRecord.retry_of_execution_id.is_(None),
                AgentExecutionRecord.bound_client_id.is_(None),
                AgentExecutionRecord.bound_connection_id.is_(None),
            )
            .values(
                revision=2,
                state="CANCELLED",
                wait_reason=None,
                wait_expires_at=None,
                error=error,
                completed_at=completed_at,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def list_legacy_waiting_executions_for_owner(
        self,
        *,
        owner_user_id: str,
    ):
        """Read legacy WAITING rows that still lack a normalized checkpoint."""

        from ..models.sql.chat_data.session import Session as ChatSessionRecord

        result = await self.session.execute(
            select(AgentExecutionRecord)
            .join(
                ChatSessionRecord,
                ChatSessionRecord.id == AgentExecutionRecord.session_id,
            )
            .where(
                ChatSessionRecord.user_id == owner_user_id,
                AgentExecutionRecord.state.in_(
                    ("WAITING", "WAITING_FOR_CONNECTION")
                ),
                or_(
                    AgentExecutionRecord.wait_reason == "CONNECTION",
                    AgentExecutionRecord.wait_reason.is_(None),
                ),
                AgentExecutionRecord.current_checkpoint_id.is_(None),
            )
            .order_by(
                AgentExecutionRecord.updated_at.asc(),
                AgentExecutionRecord.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def bind_legacy_checkpoint_pointer(
        self,
        execution_id: str,
        *,
        expected_revision: int,
        checkpoint_id: str,
        bound_client_id: str | None,
    ):
        """Bind one materialized checkpoint without changing lifecycle revision."""

        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == expected_revision,
                AgentExecutionRecord.state.in_(
                    ("WAITING", "WAITING_FOR_CONNECTION")
                ),
                or_(
                    AgentExecutionRecord.wait_reason == "CONNECTION",
                    AgentExecutionRecord.wait_reason.is_(None),
                ),
                AgentExecutionRecord.current_checkpoint_id.is_(None),
            )
            .values(
                state="WAITING",
                wait_reason="CONNECTION",
                current_checkpoint_id=checkpoint_id,
                bound_client_id=bound_client_id,
                bound_connection_id=None,
            )
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def list_waiting_executions_for_client(
        self,
        *,
        owner_user_id: str,
        client_id: str,
    ):
        """Read current normalized WAITING executions owned by one principal/client."""

        # Keep the conversation ORM import local to this R7-H-only query.
        # Importing it at repository module load time registers the sessions
        # table (and its users FK) into shared Base.metadata, breaking isolated
        # legacy/Phase 5.9 model tests that intentionally load only agent tables.
        from ..models.sql.chat_data.session import Session as ChatSessionRecord

        result = await self.session.execute(
            select(AgentExecutionRecord)
            .join(
                ChatSessionRecord,
                ChatSessionRecord.id == AgentExecutionRecord.session_id,
            )
            .where(
                ChatSessionRecord.user_id == owner_user_id,
                AgentExecutionRecord.state == "WAITING",
                AgentExecutionRecord.bound_client_id == client_id,
                AgentExecutionRecord.current_checkpoint_id.is_not(None),
            )
            .order_by(
                AgentExecutionRecord.updated_at.asc(),
                AgentExecutionRecord.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_iteration(self, iteration_id: str):
        result = await self.session.execute(
            select(AgentIterationRecord).where(AgentIterationRecord.id == iteration_id)
        )
        return result.scalar_one_or_none()

    async def save_iteration(self, values: Dict[str, Any]):
        record = AgentIterationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_iterations(self, execution_id: str):
        result = await self.session.execute(
            select(AgentIterationRecord)
            .where(AgentIterationRecord.execution_id == execution_id)
            .order_by(AgentIterationRecord.iteration.asc())
        )
        return list(result.scalars().all())

    async def save_tool_call(self, values: Dict[str, Any]):
        record = AgentToolCallRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def save_tool_result(self, values: Dict[str, Any]):
        record = AgentToolResultRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_tool_calls(self, execution_id: str, iteration_id: str | None = None):
        query = select(AgentToolCallRecord).where(AgentToolCallRecord.execution_id == execution_id)
        if iteration_id is not None:
            query = query.where(AgentToolCallRecord.iteration_id == iteration_id)
        query = query.order_by(AgentToolCallRecord.created_at.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_tool_result(self, execution_id: str, tool_call_id: str):
        result = await self.session.execute(
            select(AgentToolResultRecord).where(
                AgentToolResultRecord.execution_id == execution_id,
                AgentToolResultRecord.tool_call_id == tool_call_id
            )
        )
        return result.scalar_one_or_none()

    async def update_tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
        values: Dict[str, Any],
    ):
        record = await self.get_tool_result(execution_id, tool_call_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def get_tool_call(self, execution_id: str, tool_call_id: str):
        result = await self.session.execute(
            select(AgentToolCallRecord).where(
                AgentToolCallRecord.execution_id == execution_id,
                AgentToolCallRecord.tool_call_id == tool_call_id
            )
        )
        return result.scalar_one_or_none()

    async def update_iteration(self, iteration_id: str, values: Dict[str, Any]):
        record = await self.get_iteration(iteration_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_tool_call(self, tool_call_id: str, values: Dict[str, Any]):
        record = await self.get_tool_call(tool_call_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_execution(self, execution_id: str, values: Dict[str, Any]):
        record = await self.get_execution(execution_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def update_task(self, task_id: str, values: Dict[str, Any]):
        record = await self.get_task(task_id)
        if record is None:
            return None
        for key, value in values.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def compare_and_set_execution(
        self,
        execution_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        """Atomically mutate one execution when its revision still matches."""
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == expected_revision,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def compare_and_set_waiting_execution(
        self,
        execution_id: str,
        expected_revision: int,
        expected_checkpoint_id: str,
        expected_wait_reason: str,
        values: Dict[str, Any],
    ):
        """CAS one normalized WAITING execution using the full R7 fence."""

        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.revision == expected_revision,
                AgentExecutionRecord.state == "WAITING",
                AgentExecutionRecord.current_checkpoint_id
                == expected_checkpoint_id,
                AgentExecutionRecord.wait_reason == expected_wait_reason,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_execution(execution_id)

    async def get_transcript_chunk(self, chunk_id: str):
        result = await self.session.execute(
            select(AgentTranscriptChunkRecord).where(
                AgentTranscriptChunkRecord.chunk_id == chunk_id
            )
        )
        return result.scalar_one_or_none()

    async def save_transcript_chunk(self, values: Dict[str, Any]):
        payload = canonical_transcript_messages(
            list(values.get("payload") or [])
        )
        expected_chunk_id = transcript_chunk_id(payload)
        chunk_id = str(values.get("chunk_id") or "")
        if chunk_id != expected_chunk_id:
            raise ValueError("Transcript chunk identity does not match payload.")

        expected_count = len(payload)
        expected_bytes = len(canonical_json_bytes(payload))
        if int(values.get("message_count", -1)) != expected_count:
            raise ValueError("Transcript chunk message_count mismatch.")
        if int(values.get("canonical_bytes", -1)) != expected_bytes:
            raise ValueError("Transcript chunk canonical_bytes mismatch.")

        await self._insert_immutable_do_nothing(
            AgentTranscriptChunkRecord,
            {
                "chunk_id": chunk_id,
                "payload": payload,
                "message_count": expected_count,
                "canonical_bytes": expected_bytes,
            },
            conflict_columns=("chunk_id",),
        )
        existing = await self.get_transcript_chunk(chunk_id)
        if existing is None:
            raise ValueError(
                "Transcript chunk insert did not produce a readable row."
            )
        if (
            list(existing.payload) != payload
            or int(existing.message_count) != expected_count
            or int(existing.canonical_bytes) != expected_bytes
        ):
            raise ValueError(
                "Transcript chunk identity is already bound to "
                "different immutable content."
            )
        return existing

    async def get_transcript_payload_node(self, payload_root_ref: str):
        result = await self.session.execute(
            select(AgentTranscriptPayloadNodeRecord).where(
                AgentTranscriptPayloadNodeRecord.payload_root_ref
                == payload_root_ref
            )
        )
        return result.scalar_one_or_none()

    async def save_transcript_payload_node(self, values: Dict[str, Any]):
        chunk_id = str(values.get("chunk_id") or "")
        chunk = await self.get_transcript_chunk(chunk_id)
        if chunk is None:
            raise ValueError("Transcript payload node references missing chunk.")

        parent_ref = values.get("parent_payload_root_ref")
        parent = None
        parent_count = 0
        if parent_ref is not None:
            parent_ref = str(parent_ref)
            parent = await self.get_transcript_payload_node(parent_ref)
            if parent is None:
                raise ValueError(
                    "Transcript payload node references missing parent root."
                )
            parent_count = int(parent.logical_message_count)

        expected_count = parent_count + int(chunk.message_count)
        logical_count = int(values.get("logical_message_count", -1))
        if logical_count != expected_count:
            raise ValueError(
                "Transcript payload root logical_message_count mismatch."
            )

        expected_ref = transcript_payload_root_ref(
            parent_payload_root_ref=parent_ref,
            chunk_id=chunk_id,
            logical_message_count=logical_count,
        )
        payload_root_ref = str(values.get("payload_root_ref") or "")
        if payload_root_ref != expected_ref:
            raise ValueError("Transcript payload root identity mismatch.")

        await self._insert_immutable_do_nothing(
            AgentTranscriptPayloadNodeRecord,
            {
                "payload_root_ref": payload_root_ref,
                "parent_payload_root_ref": parent_ref,
                "chunk_id": chunk_id,
                "logical_message_count": logical_count,
            },
            conflict_columns=("payload_root_ref",),
        )
        existing = await self.get_transcript_payload_node(payload_root_ref)
        if existing is None:
            raise ValueError(
                "Transcript payload-root insert did not produce a readable row."
            )
        if (
            existing.parent_payload_root_ref != parent_ref
            or existing.chunk_id != chunk_id
            or int(existing.logical_message_count) != logical_count
        ):
            raise ValueError(
                "Transcript payload root is already bound to "
                "different immutable content."
            )
        return existing

    async def _materialize_transcript_payload_root(
        self,
        payload_root_ref: str,
    ) -> list[dict[str, Any]]:
        chain = []
        seen: set[str] = set()
        current_ref: str | None = payload_root_ref

        while current_ref is not None:
            if current_ref in seen:
                raise ValueError("Transcript payload-root ancestry cycle detected.")
            seen.add(current_ref)

            node = await self.get_transcript_payload_node(current_ref)
            if node is None:
                raise ValueError("Missing transcript payload-root node.")

            chunk = await self.get_transcript_chunk(node.chunk_id)
            if chunk is None:
                raise ValueError("Transcript payload-root references missing chunk.")

            payload = list(chunk.payload)
            if transcript_chunk_id(payload) != chunk.chunk_id:
                raise ValueError("Transcript chunk content is corrupt.")
            if int(chunk.message_count) != len(payload):
                raise ValueError("Transcript chunk message_count is corrupt.")
            if int(chunk.canonical_bytes) != len(canonical_json_bytes(payload)):
                raise ValueError("Transcript chunk canonical_bytes is corrupt.")

            expected_ref = transcript_payload_root_ref(
                parent_payload_root_ref=node.parent_payload_root_ref,
                chunk_id=node.chunk_id,
                logical_message_count=int(node.logical_message_count),
            )
            if expected_ref != node.payload_root_ref:
                raise ValueError("Transcript payload-root identity is corrupt.")

            chain.append((node, payload))
            current_ref = node.parent_payload_root_ref

        materialized: list[dict[str, Any]] = []
        for node, payload in reversed(chain):
            materialized.extend(payload)
            if int(node.logical_message_count) != len(materialized):
                raise ValueError(
                    "Transcript payload-root logical_message_count is corrupt."
                )
        return materialized

    async def _materialize_transcript_representation(
        self,
        transcript_ref: str,
        transcript_version: int,
        *,
        seen: set[tuple[str, int]] | None = None,
    ) -> list[dict[str, Any]]:
        identity = (transcript_ref, transcript_version)
        active = set() if seen is None else seen
        if identity in active:
            raise ValueError("Transcript representation ancestry cycle detected.")
        active.add(identity)
        try:
            record = await self.get_transcript_representation(
                transcript_ref,
                transcript_version,
            )
            if record is None:
                raise ValueError("Missing transcript representation.")

            kind = str(record.kind).upper()
            version = int(record.transcript_version)
            depth = int(record.delta_depth)
            parent_ref = record.parent_transcript_ref
            parent_version = record.parent_transcript_version

            expected_ref = transcript_representation_ref(
                transcript_version=version,
                kind=kind,
                parent_transcript_ref=parent_ref,
                parent_transcript_version=parent_version,
                delta_depth=depth,
                logical_message_count=int(record.logical_message_count),
                logical_transcript_fingerprint=(
                    record.logical_transcript_fingerprint
                ),
                payload_root_ref=record.payload_root_ref,
            )
            if expected_ref != record.transcript_ref:
                raise ValueError("Transcript representation identity is corrupt.")

            payload = await self._materialize_transcript_payload_root(
                record.payload_root_ref
            )
            if kind == "FULL":
                if (
                    version != 0
                    or depth != 0
                    or parent_ref is not None
                    or parent_version is not None
                ):
                    raise ValueError("Stored FULL representation shape is invalid.")
                materialized = payload
            elif kind == "DELTA":
                if (
                    parent_ref is None
                    or parent_version is None
                    or not 1 <= depth <= HARD_MAX_DELTA_DEPTH
                ):
                    raise ValueError("Stored DELTA representation shape is invalid.")
                parent = await self.get_transcript_representation(
                    parent_ref,
                    int(parent_version),
                )
                if parent is None:
                    raise ValueError(
                        "Stored DELTA representation parent is missing."
                    )
                if version != int(parent.transcript_version) + 1:
                    raise ValueError(
                        "Stored DELTA representation version ancestry is invalid."
                    )
                if depth != int(parent.delta_depth) + 1:
                    raise ValueError(
                        "Stored DELTA representation depth ancestry is invalid."
                    )
                if not payload:
                    raise ValueError("Stored DELTA representation suffix is empty.")
                parent_messages = await self._materialize_transcript_representation(
                    parent_ref,
                    int(parent_version),
                    seen=active,
                )
                materialized = parent_messages + payload
            else:
                raise ValueError("Stored transcript representation kind is invalid.")

            if int(record.logical_message_count) != len(materialized):
                raise ValueError(
                    "Transcript representation logical_message_count is corrupt."
                )
            if (
                logical_transcript_fingerprint(materialized)
                != record.logical_transcript_fingerprint
            ):
                raise ValueError(
                    "Transcript representation logical fingerprint is corrupt."
                )
            return materialized
        finally:
            active.remove(identity)

    async def get_transcript_representation(
        self,
        transcript_ref: str,
        transcript_version: int,
    ):
        result = await self.session.execute(
            select(AgentTranscriptRepresentationRecord).where(
                AgentTranscriptRepresentationRecord.transcript_ref
                == transcript_ref,
                AgentTranscriptRepresentationRecord.transcript_version
                == transcript_version,
            )
        )
        return result.scalar_one_or_none()

    async def save_transcript_representation(self, values: Dict[str, Any]):
        kind = str(values.get("kind") or "").upper()
        version = int(values.get("transcript_version", -1))
        delta_depth = int(values.get("delta_depth", -1))
        logical_count = int(values.get("logical_message_count", -1))
        logical_fingerprint = str(
            values.get("logical_transcript_fingerprint") or ""
        )
        payload_root_ref = str(values.get("payload_root_ref") or "")
        parent_ref = values.get("parent_transcript_ref")
        parent_version = values.get("parent_transcript_version")

        if len(logical_fingerprint) != 64:
            raise ValueError(
                "Transcript logical fingerprint must be a SHA-256 hex digest."
            )
        try:
            int(logical_fingerprint, 16)
        except ValueError as exc:
            raise ValueError(
                "Transcript logical fingerprint must be hexadecimal."
            ) from exc

        payload_root = await self.get_transcript_payload_node(payload_root_ref)
        if payload_root is None:
            raise ValueError(
                "Transcript representation references missing payload root."
            )

        parent = None
        if kind == "FULL":
            if (
                version != 0
                or delta_depth != 0
                or parent_ref is not None
                or parent_version is not None
            ):
                raise ValueError("Invalid FULL transcript representation shape.")
            if int(payload_root.logical_message_count) != logical_count:
                raise ValueError(
                    "FULL payload root must materialize the full logical count."
                )
            materialized = await self._materialize_transcript_payload_root(
                payload_root_ref
            )
        elif kind == "DELTA":
            if parent_ref is None or parent_version is None:
                raise ValueError(
                    "DELTA transcript representation requires exact parent pair."
                )
            parent_ref = str(parent_ref)
            parent_version = int(parent_version)
            parent = await self.get_transcript_representation(
                parent_ref,
                parent_version,
            )
            if parent is None:
                raise ValueError(
                    "DELTA transcript representation references missing parent."
                )
            if version != int(parent.transcript_version) + 1:
                raise ValueError("DELTA transcript_version must be parent + 1.")
            if delta_depth != int(parent.delta_depth) + 1:
                raise ValueError("DELTA depth must be parent depth + 1.")
            if not 1 <= delta_depth <= HARD_MAX_DELTA_DEPTH:
                raise ValueError("DELTA depth exceeds R11-B safety envelope.")
            suffix_count = logical_count - int(parent.logical_message_count)
            if suffix_count <= 0:
                raise ValueError("DELTA must append at least one message.")
            if int(payload_root.logical_message_count) != suffix_count:
                raise ValueError(
                    "DELTA payload root must contain only the append suffix."
                )
            parent_messages = await self._materialize_transcript_representation(
                parent_ref,
                parent_version,
            )
            suffix_messages = await self._materialize_transcript_payload_root(
                payload_root_ref
            )
            materialized = parent_messages + suffix_messages
        else:
            raise ValueError("Transcript representation kind must be FULL or DELTA.")

        if len(materialized) != logical_count:
            raise ValueError(
                "Transcript representation materialized message count mismatch."
            )
        if logical_transcript_fingerprint(materialized) != logical_fingerprint:
            raise ValueError(
                "Transcript representation logical fingerprint mismatch."
            )

        expected_ref = transcript_representation_ref(
            transcript_version=version,
            kind=kind,
            parent_transcript_ref=parent_ref,
            parent_transcript_version=parent_version,
            delta_depth=delta_depth,
            logical_message_count=logical_count,
            logical_transcript_fingerprint=logical_fingerprint,
            payload_root_ref=payload_root_ref,
        )
        transcript_ref = str(values.get("transcript_ref") or "")
        if transcript_ref != expected_ref:
            raise ValueError("Transcript representation identity mismatch.")

        await self._insert_immutable_do_nothing(
            AgentTranscriptRepresentationRecord,
            {
                "transcript_ref": transcript_ref,
                "transcript_version": version,
                "kind": kind,
                "parent_transcript_ref": parent_ref,
                "parent_transcript_version": parent_version,
                "delta_depth": delta_depth,
                "logical_message_count": logical_count,
                "logical_transcript_fingerprint": logical_fingerprint,
                "payload_root_ref": payload_root_ref,
            },
            conflict_columns=("transcript_ref", "transcript_version"),
        )
        existing = await self.get_transcript_representation(
            transcript_ref,
            version,
        )
        if existing is None:
            raise ValueError(
                "Transcript representation insert did not produce a readable row."
            )
        immutable_values = (
            ("kind", kind),
            ("parent_transcript_ref", parent_ref),
            ("parent_transcript_version", parent_version),
            ("delta_depth", delta_depth),
            ("logical_message_count", logical_count),
            ("logical_transcript_fingerprint", logical_fingerprint),
            ("payload_root_ref", payload_root_ref),
        )
        for field_name, expected in immutable_values:
            if getattr(existing, field_name) != expected:
                raise ValueError(
                    "Transcript representation identity is already bound "
                    "to different immutable content."
                )
        await self._materialize_transcript_representation(
            transcript_ref,
            version,
        )
        return existing

    async def save_execution_checkpoint(self, values: Dict[str, Any]):
        record = AgentExecutionCheckpointRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_execution_checkpoint(self, checkpoint_id: str):
        result = await self.session.execute(
            select(AgentExecutionCheckpointRecord).where(
                AgentExecutionCheckpointRecord.checkpoint_id == checkpoint_id
            )
        )
        return result.scalar_one_or_none()

    async def list_execution_checkpoints(self, execution_id: str):
        result = await self.session.execute(
            select(AgentExecutionCheckpointRecord)
            .where(AgentExecutionCheckpointRecord.execution_id == execution_id)
            .order_by(AgentExecutionCheckpointRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def save_checkpoint_pending_invocation(
        self,
        values: Dict[str, Any],
    ):
        record = AgentCheckpointPendingInvocationRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_checkpoint_pending_invocations(self, checkpoint_id: str):
        result = await self.session.execute(
            select(AgentCheckpointPendingInvocationRecord)
            .where(
                AgentCheckpointPendingInvocationRecord.checkpoint_id
                == checkpoint_id
            )
            .order_by(AgentCheckpointPendingInvocationRecord.ordinal.asc())
        )
        return list(result.scalars().all())

    async def save_resume_claim(self, values: Dict[str, Any]):
        record = AgentResumeClaimRecord(**values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_resume_claim(self, claim_id: str):
        result = await self.session.execute(
            select(AgentResumeClaimRecord).where(
                AgentResumeClaimRecord.claim_id == claim_id
            )
        )
        return result.scalar_one_or_none()

    async def get_resume_claim_by_request_id(self, resume_request_id: str):
        result = await self.session.execute(
            select(AgentResumeClaimRecord).where(
                AgentResumeClaimRecord.resume_request_id == resume_request_id
            )
        )
        return result.scalar_one_or_none()

    async def list_created_resume_claims_for_task_for_update(
        self,
        task_id: str,
    ):
        """Lock CREATED ResumeClaims whose executions belong to one Task."""

        result = await self.session.execute(
            select(AgentResumeClaimRecord)
            .join(
                AgentExecutionRecord,
                AgentExecutionRecord.id == AgentResumeClaimRecord.execution_id,
            )
            .where(
                AgentExecutionRecord.task_id == task_id,
                AgentResumeClaimRecord.state == "CREATED",
            )
            .order_by(
                AgentResumeClaimRecord.claim_id.asc(),
            )
            .with_for_update()
        )
        return list(result.scalars().all())

    async def compare_and_set_resume_claim(
        self,
        claim_id: str,
        expected_revision: int,
        expected_state: str,
        values: Dict[str, Any],
    ):
        """CAS one ResumeClaim without opening a second transaction.

        R7-D composes this primitive with AgentExecution and TaskBudget CAS in
        the same UnitOfWork.  R7-A intentionally does not expose a high-level
        DurableAgentStore method that would commit the claim independently.
        """

        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(AgentResumeClaimRecord)
            .where(
                AgentResumeClaimRecord.claim_id == claim_id,
                AgentResumeClaimRecord.revision == expected_revision,
                AgentResumeClaimRecord.state == expected_state,
            )
            .values(**next_values)
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get_resume_claim(claim_id)
