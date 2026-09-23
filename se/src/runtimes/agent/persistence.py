from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Mapping, Optional
import uuid

from sqlalchemy.exc import IntegrityError, OperationalError

from ...domain.schemas.agent_execution import AgentExecutionLimits
from ...domain.schemas.identity import Identity
from ...infrastructure.storage.repositories.agent import AgentRepository
from ..capability.contracts.definition import CapabilityIdempotency
from ..capability.contracts.invocation import (
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
    TERMINAL_INVOCATION_STATES,
)
from .contracts.clock import ExecutionClock
from .contracts.context import AgentExecutionContext
from .contracts.fork import (
    ForkActivationResult,
    ForkAdmission,
    ForkExecutionBootstrap,
    ForkReplayResult,
    ForkRuntimeSeed,
    fork_overlay_fingerprint,
    fork_request_fingerprint,
    fork_runtime_seed_fingerprint,
    fork_runtime_seed_payload,
    fork_transcript_fingerprint,
)
from .contracts.inference import InferenceMessage, InferenceUsage
from .contracts.resume import (
    CheckpointPendingInvocation,
    DurableExecutionCheckpoint,
    ResumeClaim,
    ResumeClaimConsumeResult,
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeClaimState,
    ResumeInvocationActionKind,
    ResumePlan,
    ResumeTriggerType,
    normalize_resume_trigger_type,
    resume_plan_fingerprint,
)
from .resume_claim import ResumeClaimDeferred, ResumeClaimError, ResumeClaimRejected
from .serialization import to_json_safe
from .task_budget import (
    prepare_resume_capacity_in_uow,
    reconcile_multibranch_task_activity_in_uow,
)
from .waiting_checkpoint import (
    WaitingCheckpointConflictError,
    stage_waiting_checkpoint,
    verify_committed_waiting_checkpoint,
)
from .waiting_ticket import build_waiting_ticket_payload
from .legacy_materialization import (
    LegacyCheckpointMaterializationError,
    parse_legacy_checkpoint_source,
    sanitize_legacy_transcript,
)


_EXECUTION_JSON_FIELDS = frozenset({
    "request",
    "result",
    "context_state",
    "transcript",
    "inference_request",
    "inference_response",
})
_TASK_JSON_FIELDS = frozenset({"wait_reasons", "input", "output"})
_BRANCH_CONTEXT_JSON_FIELDS = frozenset({"overlay_messages"})
_MESSAGE_JSON_FIELDS = frozenset({"payload"})
_ITERATION_JSON_FIELDS = frozenset({
    "tool_call_ids",
    "transcript",
    "inference_request",
    "inference_response",
})
_TOOL_CALL_JSON_FIELDS = frozenset({"arguments", "extra_metadata"})
_TOOL_RESULT_JSON_FIELDS = frozenset({"output", "extra_metadata"})


def _normalize_json_fields(
    values: Dict[str, Any],
    fields: frozenset[str],
    *,
    path: str,
) -> Dict[str, Any]:
    normalized = dict(values)
    for field in fields:
        if field in normalized:
            normalized[field] = to_json_safe(
                normalized[field],
                path=f"{path}.{field}",
            )
    return normalized


class ExecutionConflictError(RuntimeError):
    """A durable execution create or revision compare-and-set lost a race."""


class TaskConflictError(RuntimeError):
    """A durable AgentTask revision compare-and-set lost a race."""


class TaskBudgetConflictError(RuntimeError):
    """A durable TaskBudget revision compare-and-set lost a race."""


class BranchConflictError(RuntimeError):
    """A durable TaskBranch or BranchContext revision CAS lost a race."""


class ForkControlError(RuntimeError):
    """R8-F durable replay/activation authority error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_TASK_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class DurableAgentStore:
    """Adapter that persists multi-agent records through the existing UoW."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def create_session(self, session_id: str, owner_user_id: str, agent_ids: List[str]):
        async with self.uow_factory() as uow:
            record = await uow.agents.create_session(session_id, owner_user_id, agent_ids)
            await uow.commit()
            return record

    async def save_message(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _MESSAGE_JSON_FIELDS, path="agent_messages"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.save_message(values)
            await uow.commit()
            return record

    async def save_task(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TASK_JSON_FIELDS, path="agent_tasks"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.save_task(values)
            await uow.commit()
            return record

    async def load_task(self, task_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_task(task_id)
            await uow.commit()
            return record

    async def compare_and_set_task(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        values = _normalize_json_fields(
            values, _TASK_JSON_FIELDS, path="agent_tasks"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.compare_and_set_task(
                task_id,
                expected_revision,
                values,
            )
            if record is None:
                raise TaskConflictError(
                    f"Stale AgentTask revision: {task_id}@{expected_revision}"
                )
            await uow.commit()
            return record

    async def save_task_branch(self, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.save_task_branch(dict(values))
            await uow.commit()
            return record

    async def load_task_branch(self, branch_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_task_branch(branch_id)
            await uow.commit()
            return record

    async def list_task_branches(self, task_id: str):
        async with self.uow_factory() as uow:
            records = await uow.agents.list_task_branches(task_id)
            await uow.commit()
            return records

    async def compare_and_set_task_branch(
        self,
        branch_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        async with self.uow_factory() as uow:
            record = await uow.agents.compare_and_set_task_branch(
                branch_id,
                expected_revision,
                dict(values),
            )
            if record is None:
                raise BranchConflictError(
                    f"Stale TaskBranch revision: "
                    f"{branch_id}@{expected_revision}"
                )
            await uow.commit()
            return record

    async def save_task_branch_context(self, values: Dict[str, Any]):
        normalized = _normalize_json_fields(
            values,
            _BRANCH_CONTEXT_JSON_FIELDS,
            path="agent_task_branch_contexts",
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.save_task_branch_context(normalized)
            await uow.commit()
            return record

    async def load_task_branch_context(self, branch_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_task_branch_context(branch_id)
            await uow.commit()
            return record

    async def compare_and_set_task_branch_context(
        self,
        branch_id: str,
        expected_revision: int,
        overlay_messages: List[Dict[str, Any]],
    ):
        normalized = _normalize_json_fields(
            {"overlay_messages": overlay_messages},
            _BRANCH_CONTEXT_JSON_FIELDS,
            path="agent_task_branch_contexts",
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.compare_and_set_task_branch_context(
                branch_id,
                expected_revision,
                normalized["overlay_messages"],
            )
            if record is None:
                raise BranchConflictError(
                    f"Stale TaskBranchContext revision: "
                    f"{branch_id}@{expected_revision}"
                )
            await uow.commit()
            return record

    async def load_fork_replay(
        self,
        *,
        task_id: str,
        fork_request_id: str,
        source_branch_id: str,
        source_execution_id: str,
        source_checkpoint_id: str,
        target_user_id: str,
        overlay_messages,
    ) -> ForkReplayResult | None:
        """Replay one committed FORK without requiring the source to stay current."""

        async with self.uow_factory() as uow:
            receipt = await uow.agents.get_task_fork_admission(
                task_id,
                fork_request_id,
            )
            if receipt is None:
                await uow.commit()
                return None

            task = await uow.agents.get_task(task_id)
            branch = await uow.agents.get_task_branch(receipt.branch_id)
            branch_context = await uow.agents.get_task_branch_context(
                receipt.branch_id
            )
            execution = await uow.agents.get_execution(receipt.execution_id)
            budget = await uow.agents.get_task_budget(task_id)

            if task is None or branch is None or branch_context is None or execution is None or budget is None:
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "Committed ForkAdmission durable graph is incomplete.",
                )
            if str(task.created_by) != str(target_user_id):
                raise ForkControlError(
                    "FORK_FOREIGN_PRINCIPAL",
                    "Authenticated principal does not own the AgentTask.",
                )
            if (
                receipt.source_branch_id != source_branch_id
                or receipt.source_execution_id != source_execution_id
                or receipt.source_checkpoint_id != source_checkpoint_id
            ):
                raise ForkControlError(
                    "FORK_REQUEST_SEMANTIC_CONFLICT",
                    "fork_request_id was reused with different source semantics.",
                )
            if (
                receipt.task_id != task_id
                or receipt.created_by != target_user_id
                or branch.task_id != task_id
                or branch.branch_id != receipt.branch_id
                or branch.parent_branch_id != receipt.source_branch_id
                or branch.base_execution_id != receipt.source_execution_id
                or branch.base_checkpoint_id != receipt.source_checkpoint_id
                or execution.id != receipt.execution_id
                or execution.task_id != task_id
                or execution.branch_id != receipt.branch_id
                or execution.base_execution_id != receipt.source_execution_id
                or execution.base_checkpoint_id != receipt.source_checkpoint_id
                or execution.retry_of_execution_id is not None
            ):
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "Committed ForkAdmission no longer matches Branch/Execution lineage.",
                )

            try:
                seed_payload = fork_runtime_seed_payload(
                    receipt.runtime_seed_json or {}
                )
                if (
                    receipt.runtime_seed_fingerprint is None
                    or fork_runtime_seed_fingerprint(seed_payload)
                    != receipt.runtime_seed_fingerprint
                ):
                    raise ValueError("runtime seed fingerprint mismatch")
                seed = ForkRuntimeSeed(**seed_payload)
                requested_overlay_fingerprint = fork_overlay_fingerprint(
                    list(overlay_messages or [])
                )
                durable_overlay_fingerprint = fork_overlay_fingerprint(
                    list(branch_context.overlay_messages or [])
                )
            except Exception as exc:
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "Committed FORK runtime/overlay evidence is invalid.",
                ) from exc

            if requested_overlay_fingerprint != seed.overlay_fingerprint:
                raise ForkControlError(
                    "FORK_REQUEST_SEMANTIC_CONFLICT",
                    "fork_request_id was reused with a different overlay.",
                )
            if (
                durable_overlay_fingerprint != seed.overlay_fingerprint
                or int(branch_context.revision)
                != int(seed.branch_context_revision)
            ):
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "BranchContext differs from immutable ForkAdmission evidence.",
                )

            admission = ForkAdmission(
                task_id=task_id,
                fork_request_id=fork_request_id,
                plan_fingerprint=receipt.plan_fingerprint,
                branch_id=receipt.branch_id,
                branch_revision=int(branch.revision),
                execution_id=receipt.execution_id,
                execution_revision=int(execution.revision),
                task_revision=int(task.revision),
                task_budget_revision=int(budget.revision),
            )
            result = ForkReplayResult(
                admission=admission,
                execution_state=str(execution.state),
                execution_revision=int(execution.revision),
            )
            await uow.commit()
            return result

    async def activate_fork_execution(
        self,
        bootstrap: ForkExecutionBootstrap,
        *,
        identity: Identity,
        now_utc: datetime | None = None,
    ) -> ForkActivationResult:
        """Atomically acquire R8-F RUNNING@1 -> RUNNING@2 authority."""

        if int(bootstrap.expected_execution_revision) != 1:
            raise ForkControlError(
                "FORK_ACTIVATION_CONFLICT",
                "Fork bootstrap is not preactivation revision 1.",
            )
        principal = str(identity.user_id or "")
        if not principal:
            raise ForkControlError(
                "FORK_FOREIGN_PRINCIPAL",
                "Authenticated principal is required for FORK activation.",
            )

        context = bootstrap.context
        if (
            context.execution_id != bootstrap.execution_id
            or context.task_id != bootstrap.task_id
            or context.branch_id != bootstrap.branch_id
            or context.branch_runtime_seed_fingerprint
            != bootstrap.runtime_seed_fingerprint
            or context.connection_id is not None
            or context.active_budget_running
        ):
            raise ForkControlError(
                "FORK_ACTIVATION_CONTEXT_CONFLICT",
                "Fork bootstrap context differs from immutable handoff identity.",
            )

        activated_at = _utc_datetime(now_utc) or datetime.now(timezone.utc)

        async with self.uow_factory() as uow:
            # Mandatory lock order frozen by R8-F:
            # Task -> TaskBudget -> Branch -> BranchContext -> immutable receipt -> E2 CAS.
            task = await uow.agents.get_task_for_update(bootstrap.task_id)
            if task is None:
                raise ForkControlError(
                    "FORK_TASK_NOT_FOUND",
                    "Fork AgentTask no longer exists.",
                )
            budget = await uow.agents.get_task_budget_for_update(
                bootstrap.task_id
            )
            branch = await uow.agents.get_task_branch_for_update(
                bootstrap.branch_id
            )
            branch_context = await uow.agents.get_task_branch_context_for_update(
                bootstrap.branch_id
            )
            receipt = await uow.agents.get_task_fork_admission_by_execution(
                bootstrap.execution_id
            )
            execution = await uow.agents.get_execution(
                bootstrap.execution_id
            )

            if str(task.created_by) != principal:
                raise ForkControlError(
                    "FORK_FOREIGN_PRINCIPAL",
                    "Authenticated principal does not own the AgentTask.",
                )
            if str(task.status) in _TASK_TERMINAL_STATES:
                raise ForkControlError(
                    "FORK_TASK_TERMINAL",
                    "Terminal AgentTask cannot activate a fork execution.",
                )
            if str(task.status) != "RUNNING":
                raise ForkControlError(
                    "FORK_TASK_CONFLICT",
                    "Fork activation requires the AgentTask to be RUNNING.",
                    retryable=True,
                )
            if budget is None or str(budget.state) != "OPEN":
                raise ForkControlError(
                    "FORK_TASK_BUDGET_CLOSED",
                    "TaskBudget is not OPEN for fork activation.",
                )
            if branch is None or branch_context is None or receipt is None or execution is None:
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "Fork activation durable graph is incomplete.",
                )

            if (
                receipt.task_id != bootstrap.task_id
                or receipt.branch_id != bootstrap.branch_id
                or receipt.execution_id != bootstrap.execution_id
                or receipt.fork_request_id != bootstrap.fork_request_id
                or receipt.plan_fingerprint != bootstrap.plan_fingerprint
                or receipt.runtime_seed_fingerprint
                != bootstrap.runtime_seed_fingerprint
                or receipt.created_by != principal
            ):
                raise ForkControlError(
                    "FORK_ADMISSION_CORRUPT",
                    "ForkAdmission differs from the R8-E bootstrap.",
                )
            if (
                branch.task_id != bootstrap.task_id
                or str(branch.resolution_state) != "OPEN"
                or branch.current_execution_id != bootstrap.execution_id
                or branch.parent_branch_id != receipt.source_branch_id
                or branch.base_execution_id != receipt.source_execution_id
                or branch.base_checkpoint_id != receipt.source_checkpoint_id
            ):
                raise ForkControlError(
                    "FORK_BRANCH_CONFLICT",
                    "TaskBranch authority changed before activation.",
                )

            try:
                seed_payload = fork_runtime_seed_payload(
                    receipt.runtime_seed_json or {}
                )
                if (
                    receipt.runtime_seed_fingerprint is None
                    or fork_runtime_seed_fingerprint(seed_payload)
                    != receipt.runtime_seed_fingerprint
                ):
                    raise ValueError("runtime seed fingerprint mismatch")
                seed = ForkRuntimeSeed(**seed_payload)
                overlay = [
                    InferenceMessage.model_validate(item)
                    for item in (branch_context.overlay_messages or [])
                ]
                branch_transcript = [
                    InferenceMessage.model_validate(item)
                    for item in (context.branch_base_transcript or [])
                ]
            except Exception as exc:
                raise ForkControlError(
                    "FORK_RUNTIME_SEED_INVALID",
                    "Fork runtime/bootstrap evidence is invalid.",
                ) from exc

            if (
                int(branch_context.revision)
                != int(bootstrap.branch_context_revision)
                or int(branch_context.revision)
                != int(seed.branch_context_revision)
                or fork_overlay_fingerprint(
                    [item.model_dump(mode="json") for item in overlay]
                )
                != seed.overlay_fingerprint
            ):
                raise ForkControlError(
                    "FORK_BRANCH_CONTEXT_CHANGED",
                    "BranchContext changed before fork activation.",
                )

            overlay_count = len(overlay)
            if overlay_count:
                base_messages = branch_transcript[:-overlay_count]
                overlay_tail = branch_transcript[-overlay_count:]
            else:
                base_messages = branch_transcript
                overlay_tail = []
            if (
                fork_transcript_fingerprint(base_messages)
                != seed.base_transcript_fingerprint
                or [
                    item.model_dump(mode="json") for item in overlay_tail
                ]
                != [item.model_dump(mode="json") for item in overlay]
            ):
                raise ForkControlError(
                    "FORK_ACTIVATION_CONTEXT_CONFLICT",
                    "Fork bootstrap transcript differs from immutable seed.",
                )

            remaining = getattr(
                execution,
                "remaining_active_budget_seconds",
                None,
            )
            context_remaining = context.remaining_active_budget_seconds
            if (
                str(execution.state) != "RUNNING"
                or int(execution.revision) != 1
                or execution.current_checkpoint_id is not None
                or execution.task_id != bootstrap.task_id
                or execution.branch_id != bootstrap.branch_id
                or execution.base_execution_id != receipt.source_execution_id
                or execution.base_checkpoint_id != receipt.source_checkpoint_id
                or execution.retry_of_execution_id is not None
                or execution.bound_client_id is not None
                or execution.bound_connection_id is not None
                or execution.session_id != context.session_id
                or execution.agent_id != context.agent_id
                or execution.correlation_id != context.correlation_id
                or fork_request_fingerprint(dict(execution.request or {}))
                != seed.request_fingerprint
                or remaining is None
                or not math.isfinite(float(remaining))
                or float(remaining)
                != float(seed.remaining_active_budget_seconds)
                or context_remaining is None
                or float(context_remaining)
                != float(seed.remaining_active_budget_seconds)
                or context.limits.model_dump(mode="json") != dict(seed.limits)
                or dict(context.metadata) != dict(seed.metadata)
                or context.request_id != seed.request_id
                or context.workflow_id != seed.workflow_id
                or context.causation_id != seed.causation_id
                or context.trace_id != seed.trace_id
            ):
                raise ForkControlError(
                    "FORK_ACTIVATION_CONTEXT_CONFLICT",
                    "Fork Execution/context no longer matches immutable runtime seed.",
                )

            activated = await uow.agents.compare_and_set_fork_activation(
                bootstrap.execution_id,
                task_id=bootstrap.task_id,
                branch_id=bootstrap.branch_id,
                base_execution_id=receipt.source_execution_id,
                base_checkpoint_id=receipt.source_checkpoint_id,
                started_at=activated_at,
            )
            if activated is None:
                await uow.rollback()
                raise ForkControlError(
                    "FORK_ACTIVATION_CONFLICT",
                    "Fork activation CAS lost.",
                    retryable=True,
                )

            result = ForkActivationResult(
                task_id=bootstrap.task_id,
                branch_id=bootstrap.branch_id,
                execution_id=bootstrap.execution_id,
                source_execution_revision=1,
                activated_execution_revision=2,
                remaining_active_budget_seconds=float(remaining),
            )
            await uow.commit()
            return result

    async def save_task_budget(self, values: Dict[str, Any]):
        async with self.uow_factory() as uow:
            record = await uow.agents.save_task_budget(values)
            await uow.commit()
            return record

    async def load_task_budget(self, task_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_task_budget(task_id)
            await uow.commit()
            return record

    async def compare_and_set_task_budget(
        self,
        task_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        async with self.uow_factory() as uow:
            record = await uow.agents.compare_and_set_task_budget(
                task_id,
                expected_revision,
                values,
            )
            if record is None:
                raise TaskBudgetConflictError(
                    f"Stale TaskBudget revision: "
                    f"{task_id}@{expected_revision}"
                )
            await uow.commit()
            return record

    async def load_task_budget_reservation(
        self,
        task_id: str,
        kind: str,
        reservation_key: str,
    ):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_task_budget_reservation(
                task_id,
                kind,
                reservation_key,
            )
            await uow.commit()
            return record

    async def task_has_execution_history(self, task_id: str) -> bool:
        async with self.uow_factory() as uow:
            found = await uow.agents.has_execution_for_task(task_id)
            await uow.commit()
            return found

    async def save_execution(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _EXECUTION_JSON_FIELDS, path="agent_executions"
        )
        context_state = values.get("context_state")
        if (
            isinstance(context_state, dict)
            and "continuation" in context_state
        ):
            raise ExecutionConflictError(
                "LEGACY_CONTINUATION_READ_ONLY: "
                "new AgentExecution rows cannot write legacy continuation JSON."
            )
        async with self.uow_factory() as uow:
            record = await uow.agents.save_execution(values)
            await uow.commit()
            return record

    async def update_checkpoint(self, execution_id: str, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _EXECUTION_JSON_FIELDS, path="agent_executions"
        )
        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                raise KeyError(f"Unknown agent execution: {execution_id}")

            incoming_state = values.get("context_state")
            if incoming_state is not None:
                if "continuation" in incoming_state:
                    raise ExecutionConflictError(
                        "LEGACY_CONTINUATION_READ_ONLY: "
                        "context_state['continuation'] cannot be written after R7-I."
                    )
                current_state = to_json_safe(
                    getattr(execution, "context_state", None) or {},
                    path="agent_executions.context_state",
                )
                # Historical Phase 6.9 continuation JSON remains inert data.
                # Ordinary context checkpoint updates preserve it without ever
                # treating it as runtime authority.
                values["context_state"] = {**current_state, **incoming_state}

            record = await uow.agents.update_execution(execution_id, values)
            await uow.commit()
            return record

    async def compare_and_set_execution(
        self,
        execution_id: str,
        expected_revision: int,
        values: Dict[str, Any],
    ):
        values = _normalize_json_fields(
            values, _EXECUTION_JSON_FIELDS, path="agent_executions"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.compare_and_set_execution(
                execution_id,
                expected_revision,
                values,
            )
            if record is None:
                raise ExecutionConflictError(
                    f"Stale AgentExecution revision: {execution_id}@{expected_revision}"
                )
            await uow.commit()
            return record

    async def commit_waiting_checkpoint(
        self,
        execution_id: str,
        expected_revision: int,
        values: Dict[str, Any],
        *,
        checkpoint_values: Dict[str, Any],
        pending_invocations: List[Dict[str, Any]],
    ):
        """Atomically persist one non-task normalized WAITING safe point."""
        values = _normalize_json_fields(
            values, _EXECUTION_JSON_FIELDS, path="agent_executions"
        )
        target_revision = expected_revision + 1
        checkpoint_id = str(checkpoint_values["checkpoint_id"])

        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                raise ExecutionConflictError(
                    f"Unknown AgentExecution: {execution_id}"
                )

            if execution.revision == target_revision:
                try:
                    await verify_committed_waiting_checkpoint(
                        uow,
                        execution=execution,
                        source_revision=expected_revision,
                        checkpoint_values=checkpoint_values,
                        pending_invocations=pending_invocations,
                    )
                except WaitingCheckpointConflictError as exc:
                    raise ExecutionConflictError(str(exc)) from exc
                await uow.commit()
                return execution

            if (
                execution.revision != expected_revision
                or str(execution.state) != "RUNNING"
            ):
                raise ExecutionConflictError(
                    f"Stale AgentExecution revision/state: "
                    f"{execution_id}@{expected_revision}"
                )

            try:
                transition_values = await stage_waiting_checkpoint(
                    uow,
                    execution=execution,
                    source_revision=expected_revision,
                    transition_values=values,
                    checkpoint_values=checkpoint_values,
                    pending_invocations=pending_invocations,
                )
            except WaitingCheckpointConflictError as exc:
                raise ExecutionConflictError(str(exc)) from exc

            record = await uow.agents.compare_and_set_execution(
                execution_id,
                expected_revision,
                transition_values,
            )
            if record is None:
                await uow.rollback()
                raise ExecutionConflictError(
                    f"Stale AgentExecution revision: "
                    f"{execution_id}@{expected_revision}"
                )
            if record.current_checkpoint_id != checkpoint_id:
                await uow.rollback()
                raise ExecutionConflictError(
                    "WAITING CAS did not retain normalized checkpoint pointer."
                )
            await uow.commit()
            return record

    async def save_iteration(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _ITERATION_JSON_FIELDS, path="agent_iterations"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.save_iteration(values)
            await uow.commit()
            return record

    async def update_iteration(self, iteration_id: str, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _ITERATION_JSON_FIELDS, path="agent_iterations"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.update_iteration(iteration_id, values)
            await uow.commit()
            return record

    @staticmethod
    def _validate_existing_tool_call_identity(existing, values) -> None:
        expected = {
            "execution_id": existing.execution_id,
            "iteration_id": existing.iteration_id,
            "invocation_id": existing.invocation_id,
            "tool_call_id": existing.tool_call_id,
            "capability_id": existing.capability_id,
        }
        for key, durable in expected.items():
            supplied = values.get(key)
            if supplied != durable:
                raise ExecutionConflictError(
                    f"Conflicting durable tool-call {key}: "
                    f"{supplied!r} != {durable!r}."
                )
        if dict(values.get("arguments") or {}) != dict(existing.arguments or {}):
            raise ExecutionConflictError(
                "Conflicting durable tool-call arguments for reused tool_call_id."
            )

    async def save_tool_call(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TOOL_CALL_JSON_FIELDS, path="agent_tool_calls"
        )
        async with self.uow_factory() as uow:
            existing = await uow.agents.get_tool_call(
                values["execution_id"], values["tool_call_id"]
            )
            if existing is not None:
                self._validate_existing_tool_call_identity(existing, values)
                record = existing
            else:
                record = await uow.agents.save_tool_call(values)
            await uow.commit()
            return record

    async def update_tool_call(self, tool_call_id: str, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TOOL_CALL_JSON_FIELDS, path="agent_tool_calls"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.update_tool_call(tool_call_id, values)
            await uow.commit()
            return record

    @staticmethod
    def _validate_existing_tool_result_identity(existing, values) -> None:
        expected = {
            "execution_id": existing.execution_id,
            "iteration_id": existing.iteration_id,
            "invocation_id": existing.invocation_id,
            "tool_call_id": existing.tool_call_id,
            "capability_id": existing.capability_id,
        }
        for key, durable in expected.items():
            supplied = values.get(key)
            if supplied != durable:
                raise ExecutionConflictError(
                    f"Conflicting durable tool-result {key}: "
                    f"{supplied!r} != {durable!r}."
                )

    @staticmethod
    def _validate_committed_tool_result_content(existing, values) -> None:
        comparable = {
            "success": existing.success,
            "output": existing.output,
            "error_code": existing.error_code,
            "error_message": existing.error_message,
            "retryable": existing.retryable,
        }
        for key, durable in comparable.items():
            supplied = values.get(key)
            if supplied != durable:
                raise ExecutionConflictError(
                    f"Conflicting COMMITTED tool-result {key}: "
                    f"{supplied!r} != {durable!r}."
                )

    @staticmethod
    def _validate_tool_result_invocation_identity(values, invocation) -> None:
        expected = {
            "invocation_id": invocation.invocation_id,
            "capability_id": invocation.capability_id,
            "execution_id": invocation.execution_id,
            "tool_call_id": invocation.tool_call_id,
        }
        for key, authoritative in expected.items():
            supplied = values.get(key)
            if authoritative is not None and supplied != authoritative:
                raise ExecutionConflictError(
                    f"Tool-result {key} does not match CapabilityInvocation authority."
                )

    @staticmethod
    def _terminal_projection_from_invocation(values, invocation) -> Dict[str, Any]:
        projected = dict(values)
        error = dict(invocation.error or {})
        succeeded = str(invocation.state) == "COMPLETED"
        projected.update(
            {
                "success": succeeded,
                "output": invocation.output,
                "error_code": None if succeeded else (
                    error.get("error_code") or error.get("code")
                ),
                "error_message": None if succeeded else (
                    error.get("error_message") or error.get("message")
                ),
                "retryable": False if succeeded else bool(error.get("retryable", False)),
                "commit_state": "COMMITTED",
                "extra_metadata": {
                    **dict(projected.get("extra_metadata") or {}),
                    "r7_commit_authority": "CAPABILITY_INVOCATION",
                    "r7_invocation_revision": invocation.revision,
                },
            }
        )
        return projected

    async def save_tool_result(self, values: Dict[str, Any]):
        """Persist one projection without treating transport ambiguity as truth.

        Local/server outcomes commit immediately. A linked remote invocation is
        model-consumable only after R6 marks its outcome TERMINAL_COMMITTED.
        """
        values = _normalize_json_fields(
            values, _TOOL_RESULT_JSON_FIELDS, path="agent_tool_results"
        )
        async with self.uow_factory() as uow:
            existing = await uow.agents.get_tool_result(
                values["execution_id"], values["tool_call_id"]
            )
            invocation = None
            invocation_repo = getattr(uow, "capability_invocations", None)
            if invocation_repo is not None and values.get("invocation_id"):
                invocation = await invocation_repo.get_record(values["invocation_id"])

            if invocation_repo is None:
                # Compatibility-only stores used by older tests/adapters have
                # no R6 authority surface. Production SqlAlchemyUnitOfWork
                # always exposes capability_invocations.
                values["commit_state"] = "COMMITTED"
            elif invocation is None:
                authority = dict(values.get("extra_metadata") or {}).get(
                    "r7_commit_authority"
                )
                if authority == "AGENT_PRE_DISPATCH":
                    values["commit_state"] = "COMMITTED"
                else:
                    values["commit_state"] = "PROVISIONAL"
            else:
                self._validate_tool_result_invocation_identity(values, invocation)
                remote_state = getattr(invocation, "remote_outcome_state", None)
                if remote_state is None:
                    values["commit_state"] = "COMMITTED"
                elif str(remote_state) == "TERMINAL_COMMITTED":
                    values = self._terminal_projection_from_invocation(
                        values, invocation
                    )
                else:
                    values["commit_state"] = "PROVISIONAL"

            if existing is None:
                record = await uow.agents.save_tool_result(values)
            else:
                self._validate_existing_tool_result_identity(existing, values)
                if getattr(existing, "commit_state", "PROVISIONAL") == "COMMITTED":
                    self._validate_committed_tool_result_content(existing, values)
                    record = existing
                elif values["commit_state"] == "COMMITTED":
                    record = await uow.agents.update_tool_result(
                        values["execution_id"],
                        values["tool_call_id"],
                        values,
                    )
                else:
                    record = existing
            await uow.commit()
            return record

    async def load_tool_result(self, execution_id: str, tool_call_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_tool_result(execution_id, tool_call_id)
            await uow.commit()
            return record

    async def load_committed_tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
    ):
        """Return only model-consumable result projections.

        A PROVISIONAL remote projection may be promoted exactly once when R6
        already carries TERMINAL_COMMITTED authority. Promotion uses the R6
        terminal output/error rather than the stale transport projection.
        """
        async with self.uow_factory() as uow:
            record = await uow.agents.get_tool_result(execution_id, tool_call_id)
            if record is None:
                await uow.commit()
                return None
            if getattr(record, "commit_state", "PROVISIONAL") == "COMMITTED":
                await uow.commit()
                return record

            invocation_repo = getattr(uow, "capability_invocations", None)
            if invocation_repo is None:
                await uow.commit()
                return None
            invocation = await invocation_repo.get_record(record.invocation_id)
            if invocation is None:
                await uow.commit()
                return None
            values = {
                "id": record.id,
                "execution_id": record.execution_id,
                "iteration_id": record.iteration_id,
                "tool_call_id": record.tool_call_id,
                "invocation_id": record.invocation_id,
                "capability_id": record.capability_id,
                "success": record.success,
                "output": record.output,
                "error_code": record.error_code,
                "error_message": record.error_message,
                "retryable": record.retryable,
                "extra_metadata": dict(record.extra_metadata or {}),
                "attempt": record.attempt,
            }
            self._validate_tool_result_invocation_identity(values, invocation)
            if str(getattr(invocation, "remote_outcome_state", None)) != "TERMINAL_COMMITTED":
                await uow.commit()
                return None

            values = _normalize_json_fields(
                self._terminal_projection_from_invocation(values, invocation),
                _TOOL_RESULT_JSON_FIELDS,
                path="agent_tool_results",
            )
            record = await uow.agents.update_tool_result(
                execution_id,
                tool_call_id,
                values,
            )
            await uow.commit()
            return record

    async def load_fork_capability_invocations(
        self,
        execution_id: str,
    ) -> tuple[Any, ...]:
        """Read all durable CapabilityInvocation rows for R8-C planning.

        This method deliberately uses the transaction-scoped repository and
        performs no reconciliation, projection promotion, or lifecycle write.
        """
        async with self.uow_factory() as uow:
            repository = getattr(uow, "capability_invocations", None)
            if repository is None:
                raise ExecutionConflictError(
                    "FORK_INVOCATION_STORE_UNAVAILABLE: "
                    "shared UoW has no capability invocation repository."
                )
            rows = await repository.list_records_for_execution(execution_id)
            await uow.commit()
            return tuple(rows)

    async def load_fork_committed_tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
    ):
        """Read an already-COMMITTED tool projection without promoting it."""
        async with self.uow_factory() as uow:
            record = await uow.agents.get_tool_result(
                execution_id,
                tool_call_id,
            )
            if (
                record is None
                or getattr(record, "commit_state", "PROVISIONAL")
                != "COMMITTED"
            ):
                await uow.commit()
                return None
            await uow.commit()
            return record

    @staticmethod
    def _fork_tool_result_message(record) -> InferenceMessage:
        return InferenceMessage(
            role="tool",
            name=record.capability_id,
            tool_call_id=record.tool_call_id,
            content=(
                record.output
                if record.success
                else {
                    "error_code": record.error_code,
                    "error_message": record.error_message,
                }
            ),
            metadata={
                "success": record.success,
                "retryable": record.retryable,
            },
        )

    async def load_fork_safe_checkpoint_transcript(
        self,
        execution_id: str,
        checkpoint_id: str,
    ) -> tuple[InferenceMessage, ...]:
        """Strict R8-C transcript proof with zero durable mutation.

        Unlike the R7 resume-prefix reader, this helper never silently drops a
        tool projection. Every source tool message must already be backed by a
        COMMITTED AgentToolResult and the checkpoint active batch is rebuilt in
        canonical AgentIteration.tool_call_ids order.
        """

        async with self.uow_factory() as uow:
            checkpoint = await uow.agents.get_execution_checkpoint(
                checkpoint_id
            )
            if (
                checkpoint is None
                or checkpoint.execution_id != execution_id
            ):
                raise ExecutionConflictError(
                    "FORK_CHECKPOINT_LINEAGE_CONFLICT: "
                    "normalized checkpoint does not belong to source execution."
                )
            if checkpoint.transcript_snapshot is None:
                raise ExecutionConflictError(
                    "FORK_CHECKPOINT_TRANSCRIPT_UNAVAILABLE: "
                    "inline transcript snapshot is required."
                )

            pending = await uow.agents.list_checkpoint_pending_invocations(
                checkpoint_id
            )
            if pending:
                raise ExecutionConflictError(
                    "FORK_PENDING_INVOCATIONS: source checkpoint was cut "
                    "with unresolved invocation snapshots."
                )

            iterations = await uow.agents.list_iterations(execution_id)
            iteration = next(
                (
                    item
                    for item in iterations
                    if int(item.iteration) == int(checkpoint.iteration)
                ),
                None,
            )
            if iteration is None:
                raise ExecutionConflictError(
                    "FORK_TRANSCRIPT_UNSAFE: checkpoint iteration is missing."
                )

            ordered_tool_call_ids = tuple(
                str(item)
                for item in (iteration.tool_call_ids or ())
            )
            active_ids = set(ordered_tool_call_ids)
            committed: dict[str, Any] = {}

            async def require_committed(tool_call_id: str):
                cached = committed.get(tool_call_id)
                if cached is not None:
                    return cached
                record = await uow.agents.get_tool_result(
                    execution_id,
                    tool_call_id,
                )
                if (
                    record is None
                    or getattr(record, "commit_state", "PROVISIONAL")
                    != "COMMITTED"
                ):
                    raise ExecutionConflictError(
                        "FORK_TRANSCRIPT_UNSAFE: tool result "
                        f"{tool_call_id!r} is not durably COMMITTED."
                    )
                if (
                    record.execution_id != execution_id
                    or record.tool_call_id != tool_call_id
                ):
                    raise ExecutionConflictError(
                        "FORK_COMMITTED_RESULT_CONFLICT: tool-result identity "
                        "does not match source execution."
                    )

                invocation_repository = getattr(
                    uow,
                    "capability_invocations",
                    None,
                )
                if invocation_repository is None:
                    raise ExecutionConflictError(
                        "FORK_INVOCATION_STORE_UNAVAILABLE: shared UoW has "
                        "no capability invocation repository."
                    )
                invocation = await invocation_repository.get_record(
                    record.invocation_id
                )
                if (
                    invocation is None
                    or invocation.execution_id != execution_id
                    or invocation.tool_call_id != tool_call_id
                    or invocation.capability_id != record.capability_id
                ):
                    raise ExecutionConflictError(
                        "FORK_COMMITTED_RESULT_CONFLICT: COMMITTED tool result "
                        "has no matching CapabilityInvocation authority."
                    )

                committed[tool_call_id] = record
                return record

            result: list[InferenceMessage] = []
            seen_active: set[str] = set()
            for raw in checkpoint.transcript_snapshot:
                message = InferenceMessage.model_validate(raw)
                if message.role != "tool":
                    result.append(message)
                    continue

                tool_call_id = message.tool_call_id
                if not tool_call_id:
                    raise ExecutionConflictError(
                        "FORK_TRANSCRIPT_UNSAFE: tool message has no "
                        "tool_call_id."
                    )
                durable = await require_committed(tool_call_id)
                canonical = self._fork_tool_result_message(durable)
                if (
                    message.model_dump(mode="json")
                    != canonical.model_dump(mode="json")
                ):
                    raise ExecutionConflictError(
                        "FORK_TRANSCRIPT_UNSAFE: raw tool message differs "
                        "from durable COMMITTED projection."
                    )

                if tool_call_id in active_ids:
                    if tool_call_id in seen_active:
                        raise ExecutionConflictError(
                            "FORK_TRANSCRIPT_UNSAFE: active tool projection "
                            "appears more than once."
                        )
                    seen_active.add(tool_call_id)
                    continue
                result.append(canonical)

            for tool_call_id in ordered_tool_call_ids:
                durable = await require_committed(tool_call_id)
                if durable.iteration_id != iteration.id:
                    raise ExecutionConflictError(
                        "FORK_COMMITTED_RESULT_CONFLICT: active-batch tool "
                        "result belongs to another iteration."
                    )
                result.append(self._fork_tool_result_message(durable))

            await uow.commit()
            return tuple(result)

    async def prepare_fork_execution_context(
        self,
        execution_id: str,
        *,
        identity: Identity,
        agent,
        clock: ExecutionClock | None = None,
    ) -> ForkExecutionBootstrap:
        """Reconstruct R8-E branch context without acquiring execution authority."""

        def fail(code: str, message: str):
            raise ExecutionConflictError(f"{code}: {message}")

        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                fail("FORK_EXECUTION_NOT_FOUND", "Fork execution does not exist.")
            if int(execution.revision) > 1:
                fail(
                    "FORK_EXECUTION_ALREADY_ACTIVATED",
                    "Fork execution is no longer RUNNING@1 pre-activation authority.",
                )
            if (
                str(execution.state) != "RUNNING"
                or int(execution.revision) != 1
                or execution.current_checkpoint_id is not None
                or execution.task_id is None
                or execution.branch_id is None
                or execution.base_execution_id is None
                or execution.base_checkpoint_id is None
                or execution.retry_of_execution_id is not None
                or execution.bound_client_id is not None
                or execution.bound_connection_id is not None
            ):
                fail(
                    "FORK_EXECUTION_LINEAGE_CONFLICT",
                    "Fork execution is not an unactivated R8-D output.",
                )

            receipt = await uow.agents.get_task_fork_admission_by_execution(
                execution_id
            )
            if receipt is None:
                fail(
                    "FORK_ADMISSION_NOT_FOUND",
                    "Fork execution has no immutable ForkAdmission.",
                )
            if (
                receipt.runtime_seed_json is None
                or receipt.runtime_seed_fingerprint is None
            ):
                fail(
                    "FORK_RUNTIME_SEED_MISSING",
                    "ForkAdmission predates immutable runtime-seed evidence.",
                )
            try:
                seed_payload = fork_runtime_seed_payload(
                    receipt.runtime_seed_json
                )
                if int(seed_payload["version"]) != 1:
                    raise ValueError("unsupported seed version")
                if (
                    fork_runtime_seed_fingerprint(seed_payload)
                    != receipt.runtime_seed_fingerprint
                ):
                    raise ValueError("runtime seed fingerprint mismatch")
                seed = ForkRuntimeSeed(**seed_payload)
                limits = AgentExecutionLimits.model_validate(seed.limits)
            except Exception as exc:
                raise ExecutionConflictError(
                    "FORK_RUNTIME_SEED_INVALID: immutable runtime seed is invalid."
                ) from exc

            if any(
                key in seed.metadata
                for key in (
                    "client_id",
                    "connection_id",
                    "origin_client_id",
                    "origin_connection_id",
                    "routing_connection_id",
                )
            ):
                fail(
                    "FORK_RUNTIME_SEED_INVALID",
                    "Fork runtime seed contains inherited transport affinity.",
                )

            task = await uow.agents.get_task(receipt.task_id)
            if task is None:
                fail("FORK_ADMISSION_CORRUPT", "ForkAdmission task is missing.")
            if str(task.created_by) != identity.user_id:
                fail(
                    "FORK_FOREIGN_PRINCIPAL",
                    "Authenticated identity does not own this AgentTask.",
                )
            task_status = str(getattr(task.status, "value", task.status))
            if task_status not in {"RUNNING", "WAITING"}:
                fail(
                    "FORK_ADMISSION_CORRUPT",
                    "Fork task is no longer RUNNING/WAITING.",
                )

            branch = await uow.agents.get_task_branch(receipt.branch_id)
            if branch is None:
                fail("FORK_BRANCH_NOT_FOUND", "Fork TaskBranch is missing.")
            if str(getattr(branch.resolution_state, "value", branch.resolution_state)) != "OPEN":
                fail("FORK_BRANCH_NOT_OPEN", "Fork TaskBranch is not OPEN.")
            branch_context = await uow.agents.get_task_branch_context(
                receipt.branch_id
            )
            if branch_context is None:
                fail(
                    "FORK_BRANCH_CONTEXT_MISSING",
                    "Fork TaskBranchContext is missing.",
                )

            admission_corrupt = (
                receipt.execution_id != execution.id
                or receipt.branch_id != execution.branch_id
                or receipt.task_id != execution.task_id
                or receipt.created_by != identity.user_id
                or task.id != receipt.task_id
                or task.session_id != execution.session_id
            )
            if admission_corrupt:
                fail(
                    "FORK_ADMISSION_CORRUPT",
                    "ForkAdmission no longer matches task/execution authority.",
                )
            if (
                branch.task_id != receipt.task_id
                or branch.current_execution_id != execution.id
                or branch.parent_branch_id != receipt.source_branch_id
                or branch.base_execution_id != receipt.source_execution_id
                or branch.base_checkpoint_id != receipt.source_checkpoint_id
                or execution.base_execution_id != receipt.source_execution_id
                or execution.base_checkpoint_id != receipt.source_checkpoint_id
            ):
                fail(
                    "FORK_BRANCH_LINEAGE_CONFLICT",
                    "Fork Branch/Execution lineage differs from admission authority.",
                )

            if (
                int(branch_context.revision)
                != int(seed.branch_context_revision)
            ):
                fail(
                    "FORK_BRANCH_CONTEXT_CHANGED",
                    "Fork BranchContext revision changed after admission.",
                )
            try:
                overlay = [
                    InferenceMessage.model_validate(item)
                    for item in (branch_context.overlay_messages or [])
                ]
            except Exception as exc:
                raise ExecutionConflictError(
                    "FORK_BRANCH_CONTEXT_CHANGED: overlay is invalid."
                ) from exc
            if any(item.role != "user" for item in overlay):
                fail(
                    "FORK_OVERLAY_ROLE_INVALID",
                    "Initial R8 FORK overlay must contain only user messages.",
                )
            if (
                fork_overlay_fingerprint(
                    [item.model_dump(mode="json") for item in overlay]
                )
                != seed.overlay_fingerprint
            ):
                fail(
                    "FORK_BRANCH_CONTEXT_CHANGED",
                    "Fork overlay differs from immutable admission evidence.",
                )

            checkpoint = await uow.agents.get_execution_checkpoint(
                receipt.source_checkpoint_id
            )
            if checkpoint is None:
                fail(
                    "FORK_BASE_CHECKPOINT_MISSING",
                    "Fork base checkpoint is missing.",
                )
            if (
                checkpoint.execution_id != receipt.source_execution_id
                or checkpoint.session_id != execution.session_id
                or checkpoint.task_id != receipt.task_id
                or checkpoint.branch_id != receipt.source_branch_id
                or int(checkpoint.iteration) != int(seed.checkpoint_iteration)
            ):
                fail(
                    "FORK_BASE_CHECKPOINT_CONFLICT",
                    "Fork base checkpoint lineage changed.",
                )

            from .fork_planning import (
                ForkPlanError,
                _load_fork_safe_transcript_in_uow,
            )
            try:
                base_transcript = await _load_fork_safe_transcript_in_uow(
                    uow,
                    receipt.source_execution_id,
                    checkpoint,
                )
            except ForkPlanError as exc:
                raise ExecutionConflictError(
                    f"FORK_BASE_TRANSCRIPT_CHANGED: {exc}"
                ) from exc
            if (
                fork_transcript_fingerprint(base_transcript)
                != seed.base_transcript_fingerprint
            ):
                fail(
                    "FORK_BASE_TRANSCRIPT_CHANGED",
                    "Fork base transcript differs from immutable seed.",
                )

            if (
                fork_request_fingerprint(dict(execution.request or {}))
                != seed.request_fingerprint
            ):
                fail(
                    "FORK_RUNTIME_CONTEXT_CONFLICT",
                    "Fork execution request differs from immutable seed.",
                )
            remaining = execution.remaining_active_budget_seconds
            if (
                remaining is None
                or not math.isfinite(float(remaining))
                or float(remaining) < 0
                or float(remaining)
                != float(seed.remaining_active_budget_seconds)
            ):
                fail(
                    "FORK_RUNTIME_CONTEXT_CONFLICT",
                    "Fork execution active budget differs from immutable seed.",
                )

            if agent is None or getattr(agent, "name", None) != execution.agent_id:
                fail(
                    "FORK_EXECUTION_LINEAGE_CONFLICT",
                    "Current AgentDefinition does not match fork execution agent.",
                )

            branch_base_transcript = [
                item.model_dump(mode="json") for item in base_transcript
            ] + [item.model_dump(mode="json") for item in overlay]
            context = AgentExecutionContext.create(
                execution_id=execution.id,
                agent_id=execution.agent_id,
                session_id=execution.session_id,
                correlation_id=execution.correlation_id,
                identity=identity,
                limits=limits,
                request_id=seed.request_id,
                task_id=execution.task_id,
                branch_id=execution.branch_id,
                parent_execution_id=execution.parent_execution_id,
                retry_of_execution_id=None,
                base_execution_id=execution.base_execution_id,
                base_checkpoint_id=execution.base_checkpoint_id,
                workflow_id=seed.workflow_id,
                connection_id=None,
                agent=agent,
                input=dict(execution.request or {}),
                metadata=dict(seed.metadata),
                causation_id=seed.causation_id,
                trace_id=seed.trace_id,
                branch_base_transcript=branch_base_transcript,
                branch_runtime_seed_fingerprint=(
                    receipt.runtime_seed_fingerprint
                ),
                remaining_active_budget_seconds=float(remaining),
                wait_expires_at=None,
                clock=clock,
                activate_budget=False,
            )
            context.validate_context_seed()
            bootstrap = ForkExecutionBootstrap(
                execution_id=execution.id,
                expected_execution_revision=1,
                task_id=execution.task_id,
                branch_id=execution.branch_id,
                fork_request_id=receipt.fork_request_id,
                plan_fingerprint=receipt.plan_fingerprint,
                runtime_seed_fingerprint=receipt.runtime_seed_fingerprint,
                branch_context_revision=int(branch_context.revision),
                context=context,
            )
            await uow.commit()
            return bootstrap

    async def update_execution(self, execution_id: str, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _EXECUTION_JSON_FIELDS, path="agent_executions"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.update_execution(execution_id, values)
            await uow.commit()
            return record

    async def load_execution(self, execution_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_execution(execution_id)
            await uow.commit()
            return record

    async def load_iteration(self, execution_id: str, *, iteration_id: str | None = None, iteration_number: int | None = None):
        async with self.uow_factory() as uow:
            if iteration_id is not None:
                record = await uow.agents.get_iteration(iteration_id)
            else:
                iterations = await uow.agents.list_iterations(execution_id)
                if iteration_number is not None:
                    record = next(
                        (item for item in iterations if item.iteration == iteration_number),
                        None,
                    )
                else:
                    record = max(iterations, key=lambda item: item.iteration, default=None)
            await uow.commit()
            return record

    @staticmethod
    def _checkpoint_contract(record) -> DurableExecutionCheckpoint:
        return DurableExecutionCheckpoint(
            checkpoint_id=record.checkpoint_id,
            execution_id=record.execution_id,
            execution_revision=record.execution_revision,
            session_id=record.session_id,
            task_id=record.task_id,
            branch_id=record.branch_id,
            parent_checkpoint_id=record.parent_checkpoint_id,
            iteration=record.iteration,
            wait_reason=record.wait_reason,
            remaining_active_budget_seconds=record.remaining_active_budget_seconds,
            wait_expires_at=record.wait_expires_at,
            origin_client_id=record.origin_client_id,
            origin_connection_id=record.origin_connection_id,
            transcript_snapshot=(
                tuple(dict(item) for item in record.transcript_snapshot)
                if record.transcript_snapshot is not None
                else None
            ),
            transcript_ref=record.transcript_ref,
            transcript_version=record.transcript_version,
            side_effect_watermark=record.side_effect_watermark,
            legacy_source_key=record.legacy_source_key,
            metadata=dict(record.metadata_json or {}),
            created_at=record.created_at,
        )

    @staticmethod
    def _pending_invocation_contract(record) -> CheckpointPendingInvocation:
        return CheckpointPendingInvocation(
            checkpoint_id=record.checkpoint_id,
            ordinal=record.ordinal,
            invocation_id=record.invocation_id,
            invocation_revision=record.invocation_revision,
            tool_call_id=record.tool_call_id,
            capability_id=record.capability_id,
            capability_version=record.capability_version,
            request_fingerprint=record.request_fingerprint,
            idempotency=record.idempotency,
            observed_remote_outcome_state=record.observed_remote_outcome_state,
            origin_client_id=record.origin_client_id,
            origin_connection_id=record.origin_connection_id,
        )

    @staticmethod
    def _resume_claim_contract(record) -> ResumeClaim:
        return ResumeClaim(
            claim_id=record.claim_id,
            execution_id=record.execution_id,
            checkpoint_id=record.checkpoint_id,
            resume_request_id=record.resume_request_id,
            expected_execution_revision=record.expected_execution_revision,
            user_id=record.user_id,
            wait_reason=record.wait_reason,
            trigger_type=normalize_resume_trigger_type(record.trigger_type).value,
            plan_fingerprint=record.plan_fingerprint,
            claim_expires_at=_utc_datetime(record.claim_expires_at),
            client_id=record.client_id,
            connection_id=record.connection_id,
            state=ResumeClaimState(record.state),
            revision=record.revision,
            rejection_code=record.rejection_code,
            consumed_execution_revision=record.consumed_execution_revision,
            metadata=dict(record.metadata_json or {}),
            created_at=_utc_datetime(record.created_at),
            consumed_at=_utc_datetime(record.consumed_at),
            rejected_at=_utc_datetime(record.rejected_at),
            expired_at=_utc_datetime(record.expired_at),
        )

    @staticmethod
    def _claim_intent_matches(record, intent: ResumeClaimIntent) -> bool:
        return (
            record.execution_id == intent.execution_id
            and record.checkpoint_id == intent.checkpoint_id
            and record.expected_execution_revision
            == intent.expected_execution_revision
            and record.plan_fingerprint == intent.plan_fingerprint
            and record.user_id == intent.user_id
            and record.client_id == intent.client_id
            and record.connection_id == intent.connection_id
            and record.wait_reason == intent.wait_reason
            and normalize_resume_trigger_type(record.trigger_type)
            is normalize_resume_trigger_type(intent.trigger_type)
        )

    async def load_resume_claim_by_request_id(
        self,
        resume_request_id: str,
    ) -> ResumeClaim | None:
        async with self.uow_factory() as uow:
            record = await uow.agents.get_resume_claim_by_request_id(
                resume_request_id
            )
            result = (
                self._resume_claim_contract(record)
                if record is not None
                else None
            )
            await uow.commit()
            return result

    async def record_resume_claim_handoff(
        self,
        claim_id: str,
        *,
        status: str,
        payload: Mapping[str, Any],
    ) -> ResumeClaim:
        """Durably record the R7-G process-local handoff outcome.

        ResumeClaim state deliberately remains CONSUMED.  The metadata marker
        is the durable ACK-loss replay authority: ACCEPTED means supervisor
        ownership was established before the wire ACK; FAILED means authority
        was acquired but activation was recovered/terminalized without ACK.
        """

        normalized_status = str(status).upper()
        if normalized_status not in {"ACCEPTED", "FAILED"}:
            raise ValueError("handoff status must be ACCEPTED or FAILED")
        handoff = {
            "status": normalized_status,
            **to_json_safe(
                dict(payload),
                path="agent_resume_claims.metadata.r7_g_handoff",
            ),
        }

        for _ in range(8):
            try:
                async with self.uow_factory() as uow:
                    record = await uow.agents.get_resume_claim(claim_id)
                    if record is None:
                        await uow.commit()
                        raise ResumeClaimRejected(
                            "STALE_RESUME_CLAIM",
                            "ResumeClaim does not exist.",
                        )
                    if record.state != ResumeClaimState.CONSUMED.value:
                        await uow.commit()
                        raise ResumeClaimRejected(
                            "STALE_RESUME_CLAIM",
                            "ResumeClaim handoff requires CONSUMED authority.",
                        )

                    metadata = dict(record.metadata_json or {})
                    existing = metadata.get("r7_g_handoff")
                    if existing is not None:
                        if existing != handoff:
                            await uow.commit()
                            raise ResumeClaimRejected(
                                "RESUME_REQUEST_CONFLICT",
                                "ResumeClaim already has a different handoff outcome.",
                            )
                        result = self._resume_claim_contract(record)
                        await uow.commit()
                        return result

                    metadata["r7_g_handoff"] = handoff
                    updated = await uow.agents.compare_and_set_resume_claim(
                        claim_id,
                        record.revision,
                        ResumeClaimState.CONSUMED.value,
                        {"metadata_json": metadata},
                    )
                    if updated is None:
                        await uow.rollback()
                        continue
                    result = self._resume_claim_contract(updated)
                    await uow.commit()
                    return result
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise ResumeClaimDeferred(
            "RESUME_CONFLICT",
            "ResumeClaim handoff recording conflicts exhausted.",
            retryable=True,
        )

    async def get_or_create_resume_claim(
        self,
        intent: ResumeClaimIntent,
    ) -> ResumeClaim:
        """Persist one idempotent CREATED resume intent.

        Retrying the same resume_request_id never extends claim TTL and never
        acquires AgentExecution or TaskBudget authority.
        """

        if not intent.resume_request_id:
            raise ValueError("resume_request_id must be non-empty")
        if not intent.execution_id or not intent.checkpoint_id:
            raise ValueError("execution_id/checkpoint_id must be non-empty")
        if intent.expected_execution_revision < 0:
            raise ValueError("expected_execution_revision must be non-negative")
        expires_at = _utc_datetime(intent.claim_expires_at)
        if expires_at is None:
            raise ValueError("claim_expires_at must be set")

        values = {
            "claim_id": f"resume-claim-{uuid.uuid4().hex}",
            "execution_id": intent.execution_id,
            "checkpoint_id": intent.checkpoint_id,
            "resume_request_id": intent.resume_request_id,
            "expected_execution_revision": intent.expected_execution_revision,
            "user_id": intent.user_id,
            "client_id": intent.client_id,
            "connection_id": intent.connection_id,
            "wait_reason": intent.wait_reason,
            "trigger_type": normalize_resume_trigger_type(
                intent.trigger_type
            ).value,
            "state": ResumeClaimState.CREATED.value,
            "revision": 0,
            "plan_fingerprint": intent.plan_fingerprint,
            "claim_expires_at": expires_at,
            "metadata_json": to_json_safe(
                dict(intent.metadata),
                path="agent_resume_claims.metadata",
            ),
        }

        for _ in range(8):
            try:
                async with self.uow_factory() as uow:
                    existing = await uow.agents.get_resume_claim_by_request_id(
                        intent.resume_request_id
                    )
                    if existing is not None:
                        if not self._claim_intent_matches(existing, intent):
                            raise ResumeClaimRejected(
                                "RESUME_REQUEST_CONFLICT",
                                "resume_request_id was reused with different semantics.",
                            )
                        result = self._resume_claim_contract(existing)
                        await uow.commit()
                        return result

                    record = await uow.agents.save_resume_claim(values)
                    result = self._resume_claim_contract(record)
                    await uow.commit()
                    return result
            except IntegrityError:
                # UNIQUE(resume_request_id) is the durable creation fence.
                # Re-read the winner in a fresh transaction.
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise ResumeClaimDeferred(
            "RESUME_CONFLICT",
            "ResumeClaim creation conflicts exhausted.",
            retryable=True,
        )

    @staticmethod
    def _claim_matches_plan(record, spec: ResumeClaimConsumeSpec) -> bool:
        plan = spec.plan
        return (
            record.resume_request_id == spec.resume_request_id
            and record.execution_id == plan.execution_id
            and record.checkpoint_id == plan.checkpoint_id
            and record.expected_execution_revision
            == plan.expected_execution_revision
            and record.plan_fingerprint == plan.plan_fingerprint
            and record.user_id == plan.target_user_id
            and record.client_id == plan.target_client_id
            and record.connection_id == plan.target_connection_id
            and record.wait_reason == "CONNECTION"
            and normalize_resume_trigger_type(record.trigger_type)
            is ResumeTriggerType.CLIENT_RECONNECT
        )

    async def _reject_created_claim_in_uow(
        self,
        uow,
        claim,
        *,
        code: str,
        now_utc: datetime,
    ) -> ResumeClaimError:
        rejected = await uow.agents.compare_and_set_resume_claim(
            claim.claim_id,
            claim.revision,
            ResumeClaimState.CREATED.value,
            {
                "state": ResumeClaimState.REJECTED.value,
                "rejection_code": code,
                "rejected_at": now_utc,
            },
        )
        if rejected is None:
            return ResumeClaimRejected(
                "STALE_RESUME_CLAIM",
                "ResumeClaim changed while rejection was being recorded.",
            )
        return ResumeClaimRejected(code, code)

    async def _consume_resume_claim_once(
        self,
        spec: ResumeClaimConsumeSpec,
    ) -> ResumeClaimConsumeResult | ResumeClaimError:
        plan = spec.plan
        now_utc = _utc_datetime(spec.now_utc)
        if now_utc is None:
            raise ValueError("now_utc must be set")
        if not plan.target_user_id:
            raise ResumeClaimRejected(
                "FOREIGN_PRINCIPAL",
                "CLIENT_RECONNECT plan requires a target principal.",
            )
        if not plan.target_client_id:
            raise ResumeClaimRejected(
                "FOREIGN_CLIENT",
                "CLIENT_RECONNECT plan requires a stable target client_id.",
            )
        if not plan.target_connection_id:
            raise ResumeClaimRejected(
                "CONNECTION_NOT_READY",
                "CLIENT_RECONNECT plan requires a target connection_id.",
            )

        async with self.uow_factory() as uow:
            claim = await uow.agents.get_resume_claim(spec.claim_id)
            if claim is None:
                await uow.commit()
                return ResumeClaimRejected(
                    "STALE_RESUME_CLAIM",
                    "ResumeClaim does not exist.",
                )
            if not self._claim_matches_plan(claim, spec):
                await uow.commit()
                return ResumeClaimRejected(
                    "RESUME_REQUEST_CONFLICT",
                    "ResumeClaim semantics do not match the frozen ResumePlan.",
                )

            computed_fingerprint = resume_plan_fingerprint(plan)
            if computed_fingerprint != plan.plan_fingerprint:
                if claim.state == ResumeClaimState.CREATED.value:
                    error = await self._reject_created_claim_in_uow(
                        uow,
                        claim,
                        code="STALE_RESUME_CLAIM",
                        now_utc=now_utc,
                    )
                    await uow.commit()
                    return error
                await uow.commit()
                return ResumeClaimRejected(
                    "STALE_RESUME_CLAIM",
                    "ResumePlan fingerprint does not match its semantics.",
                )

            if claim.state == ResumeClaimState.CONSUMED.value:
                if claim.consumed_execution_revision is None:
                    await uow.commit()
                    return ResumeClaimRejected(
                        "STALE_RESUME_CLAIM",
                        "CONSUMED ResumeClaim has no consumed execution revision.",
                    )
                result = ResumeClaimConsumeResult(
                    claim_id=claim.claim_id,
                    resume_request_id=claim.resume_request_id,
                    execution_id=claim.execution_id,
                    checkpoint_id=claim.checkpoint_id,
                    source_execution_revision=claim.expected_execution_revision,
                    consumed_execution_revision=claim.consumed_execution_revision,
                    remaining_active_budget_seconds=plan.remaining_active_budget_seconds,
                    bound_client_id=claim.client_id,
                    bound_connection_id=claim.connection_id,
                    already_consumed=True,
                )
                await uow.commit()
                return result
            if claim.state == ResumeClaimState.EXPIRED.value:
                await uow.commit()
                return ResumeClaimRejected("CLAIM_EXPIRED", "ResumeClaim expired.")
            if claim.state == ResumeClaimState.REJECTED.value:
                code = claim.rejection_code or "STALE_RESUME_CLAIM"
                await uow.commit()
                return ResumeClaimRejected(code, code)
            if (
                claim.state != ResumeClaimState.CREATED.value
                or claim.revision != spec.expected_claim_revision
            ):
                await uow.commit()
                return ResumeClaimRejected(
                    "STALE_RESUME_CLAIM",
                    "ResumeClaim state/revision is stale.",
                )

            claim_expires_at = _utc_datetime(claim.claim_expires_at)
            if claim_expires_at is None or now_utc >= claim_expires_at:
                expired = await uow.agents.compare_and_set_resume_claim(
                    claim.claim_id,
                    claim.revision,
                    ResumeClaimState.CREATED.value,
                    {
                        "state": ResumeClaimState.EXPIRED.value,
                        "expired_at": now_utc,
                    },
                )
                if expired is None:
                    await uow.rollback()
                    return ResumeClaimRejected(
                        "STALE_RESUME_CLAIM",
                        "ResumeClaim changed while expiring.",
                    )
                await uow.commit()
                return ResumeClaimRejected("CLAIM_EXPIRED", "ResumeClaim expired.")

            execution = await uow.agents.get_execution(plan.execution_id)
            if execution is None:
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="RESUME_CONFLICT", now_utc=now_utc
                )
                await uow.commit()
                return error
            if (
                str(execution.state) != "WAITING"
                or execution.revision != plan.expected_execution_revision
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="RESUME_CONFLICT", now_utc=now_utc
                )
                await uow.commit()
                return error
            if (
                execution.session_id != plan.session_id
                or execution.agent_id != plan.agent_id
                or execution.task_id != plan.task_id
                or execution.branch_id != plan.branch_id
                or execution.parent_execution_id != plan.parent_execution_id
                or execution.retry_of_execution_id != plan.retry_of_execution_id
                or execution.base_execution_id != plan.base_execution_id
                or execution.base_checkpoint_id != plan.base_checkpoint_id
                or execution.correlation_id != plan.correlation_id
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="RESUME_CONFLICT", now_utc=now_utc
                )
                await uow.commit()
                return error
            if (
                execution.current_checkpoint_id != plan.checkpoint_id
                or str(execution.wait_reason) != "CONNECTION"
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="STALE_CHECKPOINT", now_utc=now_utc
                )
                await uow.commit()
                return error

            checkpoint = await uow.agents.get_execution_checkpoint(
                plan.checkpoint_id
            )
            if (
                checkpoint is None
                or checkpoint.execution_id != plan.execution_id
                or checkpoint.execution_revision != execution.revision
                or checkpoint.session_id != plan.session_id
                or checkpoint.task_id != plan.task_id
                or checkpoint.branch_id != plan.branch_id
                or checkpoint.iteration != plan.iteration
                or checkpoint.wait_reason != str(execution.wait_reason)
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="STALE_CHECKPOINT", now_utc=now_utc
                )
                await uow.commit()
                return error

            remaining = checkpoint.remaining_active_budget_seconds
            if (
                remaining is None
                or not math.isfinite(float(remaining))
                or float(remaining) <= 0.0
                or float(remaining) != float(plan.remaining_active_budget_seconds)
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="RESUME_CONFLICT", now_utc=now_utc
                )
                await uow.commit()
                return error

            execution_wait_expires_at = _utc_datetime(execution.wait_expires_at)
            checkpoint_wait_expires_at = _utc_datetime(checkpoint.wait_expires_at)
            plan_wait_expires_at = _utc_datetime(plan.wait_expires_at)
            if (
                execution_wait_expires_at != checkpoint_wait_expires_at
                or checkpoint_wait_expires_at != plan_wait_expires_at
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="STALE_CHECKPOINT", now_utc=now_utc
                )
                await uow.commit()
                return error
            if (
                execution_wait_expires_at is not None
                and now_utc >= execution_wait_expires_at
            ):
                timed_out = await uow.agents.compare_and_set_waiting_execution(
                    plan.execution_id,
                    plan.expected_execution_revision,
                    plan.checkpoint_id,
                    "CONNECTION",
                    {
                        "state": "TIMEOUT",
                        "wait_reason": None,
                        "wait_expires_at": None,
                        "remaining_active_budget_seconds": float(remaining),
                        "error": "WAIT_TTL_EXPIRED",
                        "completed_at": now_utc,
                    },
                )
                if timed_out is None:
                    await uow.rollback()
                    return ResumeClaimRejected(
                        "RESUME_CONFLICT",
                        "Execution changed while applying WAIT expiry.",
                    )
                rejected = await uow.agents.compare_and_set_resume_claim(
                    claim.claim_id,
                    claim.revision,
                    ResumeClaimState.CREATED.value,
                    {
                        "state": ResumeClaimState.REJECTED.value,
                        "rejection_code": "WAIT_EXPIRED",
                        "rejected_at": now_utc,
                    },
                )
                if rejected is None:
                    await uow.rollback()
                    return ResumeClaimRejected(
                        "STALE_RESUME_CLAIM",
                        "ResumeClaim changed during WAIT expiry.",
                    )
                await uow.commit()
                return ResumeClaimRejected(
                    "WAIT_EXPIRED",
                    "WAITING execution TTL expired.",
                )

            if checkpoint.origin_client_id != plan.target_client_id:
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="FOREIGN_CLIENT", now_utc=now_utc
                )
                await uow.commit()
                return error
            if (
                checkpoint.origin_connection_id
                and checkpoint.origin_connection_id == plan.target_connection_id
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="CONNECTION_NOT_READY", now_utc=now_utc
                )
                await uow.commit()
                return error

            pending = await uow.agents.list_checkpoint_pending_invocations(
                plan.checkpoint_id
            )
            actions = tuple(plan.invocation_actions)
            if (
                len(pending) != len(actions)
                or {item.invocation_id for item in pending}
                != {item.invocation_id for item in actions}
                or {item.tool_call_id for item in pending}
                != {item.tool_call_id for item in actions}
                or len({item.invocation_id for item in actions}) != len(actions)
                or len({item.tool_call_id for item in actions}) != len(actions)
            ):
                error = await self._reject_created_claim_in_uow(
                    uow, claim, code="STALE_CHECKPOINT", now_utc=now_utc
                )
                await uow.commit()
                return error
            pending_by_invocation = {item.invocation_id: item for item in pending}
            terminal_states = {
                item.value for item in TERMINAL_INVOCATION_STATES
            }
            safe_replay = {
                CapabilityIdempotency.IDEMPOTENT.value,
                CapabilityIdempotency.DEDUPLICATED.value,
            }

            action_tool_call_ids = {
                item.tool_call_id for item in actions
            }
            for tool_call_id in plan.ordered_tool_call_ids:
                if tool_call_id in action_tool_call_ids:
                    continue
                tool_call = await uow.agents.get_tool_call(
                    plan.execution_id,
                    tool_call_id,
                )
                tool_result = await uow.agents.get_tool_result(
                    plan.execution_id,
                    tool_call_id,
                )
                if (
                    tool_call is None
                    or tool_result is None
                    or getattr(tool_result, "commit_state", "PROVISIONAL")
                    != "COMMITTED"
                    or tool_result.invocation_id != tool_call.invocation_id
                    or tool_result.capability_id != tool_call.capability_id
                ):
                    error = await self._reject_created_claim_in_uow(
                        uow,
                        claim,
                        code="STALE_RECONCILIATION_SNAPSHOT",
                        now_utc=now_utc,
                    )
                    await uow.commit()
                    return error

            for action in actions:
                snapshot = pending_by_invocation.get(action.invocation_id)
                if (
                    snapshot is None
                    or snapshot.ordinal != action.ordinal
                    or snapshot.tool_call_id != action.tool_call_id
                    or snapshot.capability_id != action.capability_id
                    or snapshot.capability_version != action.capability_version
                    or snapshot.request_fingerprint != action.request_fingerprint
                    or snapshot.idempotency != action.idempotency.value
                    or snapshot.origin_client_id != plan.target_client_id
                ):
                    error = await self._reject_created_claim_in_uow(
                        uow,
                        claim,
                        code="STALE_CHECKPOINT",
                        now_utc=now_utc,
                    )
                    await uow.commit()
                    return error

                invocation = await uow.capability_invocations.get_record(
                    action.invocation_id
                )
                expected_outcome = (
                    action.expected_remote_outcome_state.value
                    if action.expected_remote_outcome_state is not None
                    else None
                )
                if (
                    invocation is None
                    or invocation.execution_id != plan.execution_id
                    or invocation.tool_call_id != action.tool_call_id
                    or invocation.capability_id != action.capability_id
                    or invocation.capability_version != action.capability_version
                    or invocation.request_fingerprint != action.request_fingerprint
                    or invocation.idempotency != action.idempotency.value
                    or invocation.owner_user_id != plan.target_user_id
                    or invocation.origin_client_id != plan.target_client_id
                    or invocation.revision != action.expected_invocation_revision
                    or invocation.state != action.expected_invocation_state.value
                    or invocation.remote_outcome_state != expected_outcome
                ):
                    error = await self._reject_created_claim_in_uow(
                        uow,
                        claim,
                        code="STALE_RECONCILIATION_SNAPSHOT",
                        now_utc=now_utc,
                    )
                    await uow.commit()
                    return error

                if action.action is ResumeInvocationActionKind.REUSE_COMMITTED:
                    tool_result = await uow.agents.get_tool_result(
                        plan.execution_id,
                        action.tool_call_id,
                    )
                    if (
                        invocation.state not in terminal_states
                        or invocation.remote_outcome_state
                        != RemoteOutcomeState.TERMINAL_COMMITTED.value
                        or tool_result is None
                        or getattr(tool_result, "commit_state", "PROVISIONAL")
                        != "COMMITTED"
                        or tool_result.invocation_id != action.invocation_id
                        or tool_result.capability_id != action.capability_id
                    ):
                        error = await self._reject_created_claim_in_uow(
                            uow,
                            claim,
                            code="STALE_RECONCILIATION_SNAPSHOT",
                            now_utc=now_utc,
                        )
                        await uow.commit()
                        return error
                elif action.action is ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED:
                    if (
                        invocation.state != CapabilityInvocationState.WAITING.value
                        or invocation.wait_reason
                        != CapabilityWaitReason.CONNECTION.value
                        or invocation.remote_outcome_state
                        != RemoteOutcomeState.NOT_DISPATCHED.value
                    ):
                        error = await self._reject_created_claim_in_uow(
                            uow,
                            claim,
                            code="STALE_RECONCILIATION_SNAPSHOT",
                            now_utc=now_utc,
                        )
                        await uow.commit()
                        return error
                elif action.action is ResumeInvocationActionKind.REPLAY_SAFE:
                    if (
                        invocation.state != CapabilityInvocationState.WAITING.value
                        or invocation.wait_reason
                        != CapabilityWaitReason.CONNECTION.value
                        or invocation.remote_outcome_state
                        not in {
                            RemoteOutcomeState.IN_FLIGHT.value,
                            RemoteOutcomeState.OUTCOME_UNKNOWN.value,
                        }
                        or invocation.idempotency not in safe_replay
                    ):
                        error = await self._reject_created_claim_in_uow(
                            uow,
                            claim,
                            code="STALE_RECONCILIATION_SNAPSHOT",
                            now_utc=now_utc,
                        )
                        await uow.commit()
                        return error

            locked_task = None
            if plan.task_id is not None:
                # Preserve the canonical R8-F lock order for any path that
                # may later write aggregate Task activity:
                # Task -> TaskBudget / execution -> Task activity.
                locked_task = await uow.agents.get_task_for_update(plan.task_id)
                if (
                    locked_task is None
                    or locked_task.session_id != plan.session_id
                    or locked_task.created_by != plan.target_user_id
                ):
                    error = await self._reject_created_claim_in_uow(
                        uow, claim, code="TASK_TERMINAL", now_utc=now_utc
                    )
                    await uow.commit()
                    return error
                if str(locked_task.status) in _TASK_TERMINAL_STATES:
                    error = await self._reject_created_claim_in_uow(
                        uow, claim, code="TASK_TERMINAL", now_utc=now_utc
                    )
                    await uow.commit()
                    return error

                # R7 resume changes current branch activity WAITING -> RUNNING.
                # Advance the Task activity epoch before TaskBudget/execution
                # writes so SQLite cannot commit a stale WAITING reconciler
                # snapshot after this resume authority wins.
                activity_task = await uow.agents.compare_and_set_task(
                    plan.task_id,
                    int(locked_task.revision),
                    {
                        "status": "RUNNING",
                        "wait_reasons": [],
                    },
                )
                if activity_task is None:
                    await uow.rollback()
                    return ResumeClaimRejected(
                        "RESUME_CONFLICT",
                        "AgentTask activity epoch changed during resume.",
                    )
                locked_task = activity_task

                await prepare_resume_capacity_in_uow(
                    uow,
                    task_id=plan.task_id,
                    execution_id=plan.execution_id,
                    source_revision=plan.expected_execution_revision,
                    delegated=plan.parent_execution_id is not None,
                )

            updated_execution = await uow.agents.compare_and_set_waiting_execution(
                plan.execution_id,
                plan.expected_execution_revision,
                plan.checkpoint_id,
                "CONNECTION",
                {
                    "state": "RUNNING",
                    "wait_reason": None,
                    "wait_expires_at": None,
                    "bound_client_id": plan.target_client_id,
                    "bound_connection_id": plan.target_connection_id,
                    "started_at": now_utc,
                },
            )
            if updated_execution is None:
                await uow.rollback()
                return ResumeClaimRejected(
                    "RESUME_CONFLICT",
                    "AgentExecution claim CAS lost.",
                )

            if plan.task_id is not None:
                activity = await reconcile_multibranch_task_activity_in_uow(
                    uow,
                    task_id=plan.task_id,
                    locked_task=locked_task,
                )
                if activity is None:
                    await uow.rollback()
                    return ResumeClaimRejected(
                        "RESUME_CONFLICT",
                        "AgentTask activity CAS lost during resume.",
                    )

            consumed_revision = plan.expected_execution_revision + 1
            consumed = await uow.agents.compare_and_set_resume_claim(
                claim.claim_id,
                claim.revision,
                ResumeClaimState.CREATED.value,
                {
                    "state": ResumeClaimState.CONSUMED.value,
                    "consumed_at": now_utc,
                    "consumed_execution_revision": consumed_revision,
                },
            )
            if consumed is None:
                await uow.rollback()
                return ResumeClaimRejected(
                    "STALE_RESUME_CLAIM",
                    "ResumeClaim consumption CAS lost.",
                )

            result = ResumeClaimConsumeResult(
                claim_id=claim.claim_id,
                resume_request_id=claim.resume_request_id,
                execution_id=plan.execution_id,
                checkpoint_id=plan.checkpoint_id,
                source_execution_revision=plan.expected_execution_revision,
                consumed_execution_revision=consumed_revision,
                remaining_active_budget_seconds=float(remaining),
                bound_client_id=plan.target_client_id,
                bound_connection_id=plan.target_connection_id,
                already_consumed=False,
            )
            await uow.commit()
            return result

    async def consume_resume_claim(
        self,
        spec: ResumeClaimConsumeSpec,
    ) -> ResumeClaimConsumeResult:
        """Atomically acquire R7-F durable resume authority.

        The transaction revalidates the normalized checkpoint and every R7-D
        invocation snapshot, then couples TaskBudget reacquisition (when
        task-scoped), AgentExecution WAITING -> RUNNING and ResumeClaim
        CREATED -> CONSUMED.
        """

        for _ in range(8):
            try:
                outcome = await self._consume_resume_claim_once(spec)
                if isinstance(outcome, ResumeClaimError):
                    raise outcome
                return outcome
            except IntegrityError:
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise ResumeClaimDeferred(
            "RESUME_CONFLICT",
            "ResumeClaim SQL conflicts exhausted.",
            retryable=True,
        )

    async def materialize_legacy_checkpoint(
        self,
        execution_id: str,
        *,
        requested_checkpoint_id: str | None = None,
        target_user_id: str | None = None,
        target_client_id: str | None = None,
    ) -> DurableExecutionCheckpoint | None:
        """Materialize one Phase 6.9 WAITING safe point into normalized R7 rows.

        The conversion is read-only with respect to legacy JSON and does not
        advance AgentExecution.revision. R6 CapabilityInvocation rows are the
        only replay-safety authority for unresolved active-batch calls.
        """

        for attempt in range(3):
            try:
                async with self.uow_factory() as uow:
                    execution = await uow.agents.get_execution(execution_id)
                    if execution is None:
                        await uow.commit()
                        return None

                    current_id = getattr(execution, "current_checkpoint_id", None)
                    if current_id:
                        if requested_checkpoint_id and current_id != requested_checkpoint_id:
                            raise LegacyCheckpointMaterializationError(
                                "STALE_CHECKPOINT",
                                "Requested checkpoint differs from normalized current checkpoint.",
                            )
                        checkpoint = await uow.agents.get_execution_checkpoint(current_id)
                        if (
                            checkpoint is None
                            or checkpoint.execution_id != execution.id
                            or checkpoint.execution_revision != execution.revision
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "CHECKPOINT_INCOMPLETE",
                                "Normalized current checkpoint is missing or stale.",
                            )
                        result = self._checkpoint_contract(checkpoint)
                        await uow.commit()
                        return result

                    source = parse_legacy_checkpoint_source(
                        execution,
                        requested_checkpoint_id=requested_checkpoint_id,
                        target_user_id=target_user_id,
                        target_client_id=target_client_id,
                    )
                    if not source.origin_client_id:
                        raise LegacyCheckpointMaterializationError(
                            "RESUME_ORIGIN_CLIENT_MISSING",
                            "Legacy CONNECTION checkpoint has no stable origin client.",
                        )
                    if (
                        getattr(execution, "bound_client_id", None)
                        and execution.bound_client_id != source.origin_client_id
                    ):
                        raise LegacyCheckpointMaterializationError(
                            "FOREIGN_CLIENT",
                            "Durable execution binding conflicts with legacy origin client.",
                        )

                    iterations = await uow.agents.list_iterations(execution_id)
                    iteration = next(
                        (
                            item
                            for item in iterations
                            if item.iteration == source.iteration
                        ),
                        None,
                    )
                    if iteration is None:
                        raise LegacyCheckpointMaterializationError(
                            "CHECKPOINT_INCOMPLETE",
                            "Legacy checkpoint iteration is absent from durable history.",
                        )
                    ordered_tool_call_ids = tuple(
                        getattr(iteration, "tool_call_ids", None) or ()
                    )
                    if not ordered_tool_call_ids:
                        raise LegacyCheckpointMaterializationError(
                            "CHECKPOINT_INCOMPLETE",
                            "Legacy checkpoint iteration has no canonical tool-call order.",
                        )

                    if source.pending_tool_call_id not in ordered_tool_call_ids:
                        raise LegacyCheckpointMaterializationError(
                            "LEGACY_CHECKPOINT_UNSAFE",
                            "Legacy pending tool call is outside the canonical active batch.",
                        )

                    invocation_repo = getattr(uow, "capability_invocations", None)
                    if invocation_repo is None:
                        raise LegacyCheckpointMaterializationError(
                            "LEGACY_CHECKPOINT_UNSAFE",
                            "R6 CapabilityInvocation authority is unavailable.",
                        )

                    pending_values: list[dict[str, Any]] = []
                    for ordinal, tool_call_id in enumerate(ordered_tool_call_ids):
                        tool_call = await uow.agents.get_tool_call(
                            execution_id,
                            tool_call_id,
                        )
                        if tool_call is None:
                            raise LegacyCheckpointMaterializationError(
                                "CHECKPOINT_INCOMPLETE",
                                f"Missing durable tool call {tool_call_id!r}.",
                            )
                        result_row = await uow.agents.get_tool_result(
                            execution_id,
                            tool_call_id,
                        )
                        was_legacy_pending = (
                            tool_call_id == source.pending_tool_call_id
                            or tool_call.invocation_id
                            == source.pending_invocation_id
                        )
                        if (
                            result_row is not None
                            and getattr(result_row, "commit_state", "PROVISIONAL")
                            == "COMMITTED"
                            and not was_legacy_pending
                        ):
                            continue

                        invocation = await invocation_repo.get_record(
                            tool_call.invocation_id
                        )
                        if invocation is None:
                            raise LegacyCheckpointMaterializationError(
                                "CHECKPOINT_INCOMPLETE",
                                f"Missing R6 invocation for {tool_call_id!r}.",
                            )
                        if was_legacy_pending and (
                            tool_call.invocation_id
                            != source.pending_invocation_id
                            or tool_call_id != source.pending_tool_call_id
                            or tool_call.capability_id
                            != source.pending_capability_id
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Legacy pending identity conflicts with canonical active batch.",
                            )
                        if (
                            invocation.execution_id != execution_id
                            or invocation.tool_call_id != tool_call_id
                            or invocation.capability_id != tool_call.capability_id
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Legacy tool call conflicts with R6 invocation identity.",
                            )
                        if (
                            target_user_id
                            and invocation.owner_user_id != target_user_id
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "FOREIGN_PRINCIPAL",
                                "Pending R6 invocation belongs to another principal.",
                            )
                        if (
                            not invocation.origin_client_id
                            or invocation.origin_client_id != source.origin_client_id
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "FOREIGN_CLIENT",
                                "Pending R6 invocation lacks matching stable client authority.",
                            )
                        if (
                            source.origin_connection_id
                            and invocation.connection_id
                            and invocation.connection_id
                            != source.origin_connection_id
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Legacy checkpoint connection differs from R6 invocation authority.",
                            )
                        if invocation.remote_outcome_state is None:
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Unresolved legacy call has no R6 remote outcome authority.",
                            )
                        if (
                            not invocation.capability_version
                            or not invocation.request_fingerprint
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "CHECKPOINT_INCOMPLETE",
                                "Pending R6 invocation lacks version/fingerprint watermark.",
                            )

                        pending_values.append(
                            {
                                "checkpoint_id": source.checkpoint_id,
                                "ordinal": ordinal,
                                "invocation_id": invocation.invocation_id,
                                "invocation_revision": invocation.revision,
                                "tool_call_id": tool_call_id,
                                "capability_id": invocation.capability_id,
                                "capability_version": invocation.capability_version,
                                "request_fingerprint": invocation.request_fingerprint,
                                "idempotency": invocation.idempotency,
                                "observed_remote_outcome_state": (
                                    invocation.remote_outcome_state
                                ),
                                "origin_client_id": invocation.origin_client_id,
                                "origin_connection_id": invocation.connection_id,
                            }
                        )

                    if not pending_values:
                        raise LegacyCheckpointMaterializationError(
                            "CHECKPOINT_INCOMPLETE",
                            "Legacy CONNECTION checkpoint has no unresolved R6 invocation.",
                        )

                    transcript_snapshot = sanitize_legacy_transcript(
                        source.transcript,
                        active_tool_call_ids=ordered_tool_call_ids,
                    )
                    checkpoint_values = {
                        "checkpoint_id": source.checkpoint_id,
                        "execution_id": execution.id,
                        "execution_revision": execution.revision,
                        "session_id": execution.session_id,
                        "task_id": execution.task_id,
                        "branch_id": execution.branch_id,
                        "parent_checkpoint_id": source.parent_checkpoint_id,
                        "iteration": source.iteration,
                        "wait_reason": "CONNECTION",
                        "remaining_active_budget_seconds": (
                            execution.remaining_active_budget_seconds
                        ),
                        "wait_expires_at": execution.wait_expires_at,
                        "origin_client_id": source.origin_client_id,
                        "origin_connection_id": source.origin_connection_id,
                        "transcript_snapshot": list(transcript_snapshot),
                        "legacy_source_key": source.legacy_source_key,
                        "metadata_json": {
                            "legacy_materialized": True,
                            "legacy_owner_user_id": source.owner_user_id,
                        },
                    }
                    existing = await uow.agents.get_execution_checkpoint(
                        source.checkpoint_id
                    )
                    if existing is not None:
                        comparable_checkpoint = {
                            "execution_id": execution.id,
                            "execution_revision": execution.revision,
                            "session_id": execution.session_id,
                            "task_id": execution.task_id,
                            "branch_id": execution.branch_id,
                            "parent_checkpoint_id": source.parent_checkpoint_id,
                            "iteration": source.iteration,
                            "wait_reason": "CONNECTION",
                            "origin_client_id": source.origin_client_id,
                            "origin_connection_id": source.origin_connection_id,
                            "legacy_source_key": source.legacy_source_key,
                        }
                        for field, expected in comparable_checkpoint.items():
                            if getattr(existing, field) != expected:
                                raise LegacyCheckpointMaterializationError(
                                    "LEGACY_CHECKPOINT_UNSAFE",
                                    "Legacy checkpoint id collides with different "
                                    f"normalized {field} semantics.",
                                )
                        if tuple(existing.transcript_snapshot or ()) != tuple(
                            checkpoint_values["transcript_snapshot"] or ()
                        ):
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Legacy checkpoint transcript snapshot differs.",
                            )

                        persisted_pending = (
                            await uow.agents.list_checkpoint_pending_invocations(
                                source.checkpoint_id
                            )
                        )
                        expected_pending = tuple(
                            (
                                int(item["ordinal"]),
                                str(item["invocation_id"]),
                                int(item["invocation_revision"]),
                                str(item["tool_call_id"]),
                                str(item["capability_id"]),
                                item["capability_version"],
                                item["request_fingerprint"],
                                str(item["idempotency"]),
                                (
                                    str(item["observed_remote_outcome_state"])
                                    if item["observed_remote_outcome_state"] is not None
                                    else None
                                ),
                                item["origin_client_id"],
                                item["origin_connection_id"],
                            )
                            for item in pending_values
                        )
                        actual_pending = tuple(
                            (
                                item.ordinal,
                                item.invocation_id,
                                item.invocation_revision,
                                item.tool_call_id,
                                item.capability_id,
                                item.capability_version,
                                item.request_fingerprint,
                                item.idempotency,
                                item.observed_remote_outcome_state,
                                item.origin_client_id,
                                item.origin_connection_id,
                            )
                            for item in persisted_pending
                        )
                        if actual_pending != expected_pending:
                            raise LegacyCheckpointMaterializationError(
                                "LEGACY_CHECKPOINT_UNSAFE",
                                "Legacy checkpoint pending invocation snapshot differs.",
                            )
                    else:
                        await uow.agents.save_execution_checkpoint(
                            checkpoint_values
                        )
                        for values in pending_values:
                            await uow.agents.save_checkpoint_pending_invocation(
                                values
                            )

                    bound = await uow.agents.bind_legacy_checkpoint_pointer(
                        execution.id,
                        expected_revision=execution.revision,
                        checkpoint_id=source.checkpoint_id,
                        bound_client_id=source.origin_client_id,
                    )
                    if bound is None:
                        await uow.rollback()
                        raise ExecutionConflictError(
                            "Legacy checkpoint materialization lost the WAITING pointer race."
                        )
                    if bound.revision != execution.revision:
                        await uow.rollback()
                        raise ExecutionConflictError(
                            "Legacy materialization must not advance execution revision."
                        )
                    checkpoint = await uow.agents.get_execution_checkpoint(
                        source.checkpoint_id
                    )
                    result = self._checkpoint_contract(checkpoint)
                    await uow.commit()
                    return result
            except LegacyCheckpointMaterializationError:
                raise
            except IntegrityError:
                current = await self.load_current_checkpoint(execution_id)
                if (
                    current is not None
                    and (
                        requested_checkpoint_id is None
                        or current.checkpoint_id == requested_checkpoint_id
                    )
                ):
                    return current
                raise LegacyCheckpointMaterializationError(
                    "LEGACY_CHECKPOINT_UNSAFE",
                    "Concurrent legacy materialization produced conflicting rows.",
                )
            except ExecutionConflictError:
                current = await self.load_current_checkpoint(execution_id)
                if (
                    current is not None
                    and (
                        requested_checkpoint_id is None
                        or current.checkpoint_id == requested_checkpoint_id
                    )
                ):
                    return current
                if attempt < 2:
                    continue
                raise
            except OperationalError as exc:
                message = str(exc).lower()
                if attempt < 2 and ("locked" in message or "busy" in message):
                    continue
                raise

        raise ExecutionConflictError(
            "Legacy checkpoint materialization conflicts exhausted."
        )

    async def materialize_legacy_waiting_for_client(
        self,
        *,
        owner_user_id: str,
        client_id: str,
    ) -> tuple[DurableExecutionCheckpoint, ...]:
        """Best-effort read-only legacy ingestion before R7-H ticket replay."""

        async with self.uow_factory() as uow:
            executions = await uow.agents.list_legacy_waiting_executions_for_owner(
                owner_user_id=owner_user_id,
            )
            execution_ids = tuple(item.id for item in executions)
            await uow.commit()

        materialized: list[DurableExecutionCheckpoint] = []
        for execution_id in execution_ids:
            try:
                checkpoint = await self.materialize_legacy_checkpoint(
                    execution_id,
                    target_user_id=owner_user_id,
                    target_client_id=client_id,
                )
            except LegacyCheckpointMaterializationError:
                # One unsafe or foreign legacy row must not suppress canonical
                # ticket replay for other executions. Explicit resume surfaces
                # the stable rejection code.
                continue
            if checkpoint is not None:
                materialized.append(checkpoint)
        return tuple(materialized)

    async def load_pending_resume_tickets(
        self,
        *,
        owner_user_id: str,
        client_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Read canonical WAITING tickets for one principal/client.

        R7-I may first materialize a legacy Phase 6.9 safe point. Materializing
        representation is not a WAITING -> RUNNING lifecycle transition and
        never creates/consumes a ResumeClaim.
        """

        await self.materialize_legacy_waiting_for_client(
            owner_user_id=owner_user_id,
            client_id=client_id,
        )

        async with self.uow_factory() as uow:
            executions = await uow.agents.list_waiting_executions_for_client(
                owner_user_id=owner_user_id,
                client_id=client_id,
            )
            tickets: list[dict[str, Any]] = []
            for execution in executions:
                checkpoint_id = getattr(execution, "current_checkpoint_id", None)
                if not checkpoint_id:
                    continue
                checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
                if (
                    checkpoint is None
                    or checkpoint.execution_id != execution.id
                    or checkpoint.execution_revision != execution.revision
                ):
                    raise ExecutionConflictError(
                        "AgentExecution current normalized checkpoint is missing or stale."
                    )
                pending = await uow.agents.list_checkpoint_pending_invocations(
                    checkpoint_id
                )
                tickets.append(
                    build_waiting_ticket_payload(
                        execution,
                        checkpoint,
                        pending,
                    )
                )
            await uow.commit()
            return tuple(tickets)

    async def load_current_checkpoint(
        self,
        execution_id: str,
    ) -> DurableExecutionCheckpoint | None:
        """Load only the normalized checkpoint named by AgentExecution."""

        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                await uow.commit()
                return None
            checkpoint_id = getattr(execution, "current_checkpoint_id", None)
            if not checkpoint_id:
                await uow.commit()
                return None
            checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
            if (
                checkpoint is None
                or checkpoint.execution_id != execution.id
                or checkpoint.execution_revision != execution.revision
            ):
                raise ExecutionConflictError(
                    "AgentExecution current normalized checkpoint is missing or stale."
                )
            result = self._checkpoint_contract(checkpoint)
            await uow.commit()
            return result

    async def load_checkpoint_pending_invocations(
        self,
        checkpoint_id: str,
    ) -> tuple[CheckpointPendingInvocation, ...]:
        async with self.uow_factory() as uow:
            checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
            if checkpoint is None:
                await uow.commit()
                return ()
            rows = await uow.agents.list_checkpoint_pending_invocations(
                checkpoint_id
            )
            result = tuple(
                self._pending_invocation_contract(item)
                for item in rows
            )
            await uow.commit()
            return result

    async def load_committed_checkpoint_transcript(
        self,
        execution_id: str,
        checkpoint_id: str,
        *,
        active_tool_call_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], ...]:
        """Return the R7-C safe prefix for one normalized checkpoint."""

        active_ids = set(active_tool_call_ids)
        async with self.uow_factory() as uow:
            checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
            if checkpoint is None or checkpoint.execution_id != execution_id:
                raise ExecutionConflictError(
                    "Normalized checkpoint does not belong to execution."
                )
            if checkpoint.transcript_snapshot is None:
                raise ExecutionConflictError(
                    "R7-D requires an inline reconstructable transcript snapshot."
                )

            result: list[dict[str, Any]] = []
            for raw in checkpoint.transcript_snapshot:
                message = dict(raw)
                if message.get("role") != "tool":
                    result.append(message)
                    continue
                tool_call_id = message.get("tool_call_id")
                if not tool_call_id or tool_call_id in active_ids:
                    continue
                durable = await uow.agents.get_tool_result(
                    execution_id,
                    tool_call_id,
                )
                if (
                    durable is None
                    or getattr(durable, "commit_state", "PROVISIONAL")
                    != "COMMITTED"
                ):
                    continue
                result.append(
                    {
                        "role": "tool",
                        "content": (
                            durable.output
                            if durable.success
                            else {
                                "error_code": durable.error_code,
                                "error_message": durable.error_message,
                            }
                        ),
                        "tool_calls": [],
                        "name": durable.capability_id,
                        "tool_call_id": durable.tool_call_id,
                        "metadata": {
                            "success": durable.success,
                            "retryable": durable.retryable,
                        },
                    }
                )
            await uow.commit()
            return tuple(result)

    async def prepare_resume_plan_context(
        self,
        plan: ResumePlan,
        *,
        identity: Identity,
        limits: AgentExecutionLimits | None = None,
        agent=None,
        clock: ExecutionClock | None = None,
    ) -> AgentExecutionContext:
        """Reconstruct the exact read-only R7-D context before claim consume.

        This method never acquires execution authority. It exists so R7-G can
        reserve process-local ownership using the same cancellation scope
        before F3 atomically consumes the ResumeClaim.
        """

        if resume_plan_fingerprint(plan) != plan.plan_fingerprint:
            raise ExecutionConflictError(
                "STALE_RESUME_PLAN: plan fingerprint does not match semantics."
            )
        if identity.user_id != plan.target_user_id:
            raise ExecutionConflictError(
                "FOREIGN_PRINCIPAL: resume identity differs from plan."
            )
        if not plan.target_client_id or not plan.target_connection_id:
            raise ExecutionConflictError(
                "CONNECTION_NOT_READY: resume plan has no K2 identity."
            )

        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(plan.execution_id)
            if execution is None:
                raise ExecutionConflictError(
                    f"Unknown AgentExecution: {plan.execution_id}"
                )
            if (
                str(execution.state) != "WAITING"
                or execution.revision != plan.expected_execution_revision
                or execution.current_checkpoint_id != plan.checkpoint_id
                or str(execution.wait_reason) != "CONNECTION"
            ):
                raise ExecutionConflictError(
                    "STALE_RESUME_PLAN: execution is no longer the planned "
                    "WAITING authority."
                )
            if (
                execution.session_id != plan.session_id
                or execution.agent_id != plan.agent_id
                or execution.task_id != plan.task_id
                or execution.branch_id != plan.branch_id
                or execution.parent_execution_id != plan.parent_execution_id
                or execution.retry_of_execution_id != plan.retry_of_execution_id
                or execution.base_execution_id != plan.base_execution_id
                or execution.base_checkpoint_id != plan.base_checkpoint_id
                or execution.correlation_id != plan.correlation_id
            ):
                raise ExecutionConflictError(
                    "STALE_RESUME_PLAN: execution lineage differs from plan."
                )

            checkpoint = await uow.agents.get_execution_checkpoint(
                plan.checkpoint_id
            )
            if (
                checkpoint is None
                or checkpoint.execution_id != plan.execution_id
                or checkpoint.execution_revision
                != plan.expected_execution_revision
                or checkpoint.session_id != plan.session_id
                or checkpoint.task_id != plan.task_id
                or checkpoint.branch_id != plan.branch_id
                or checkpoint.iteration != plan.iteration
                or checkpoint.wait_reason != "CONNECTION"
                or checkpoint.origin_client_id != plan.target_client_id
            ):
                raise ExecutionConflictError(
                    "STALE_CHECKPOINT: normalized checkpoint differs from plan."
                )

            execution_remaining = getattr(
                execution,
                "remaining_active_budget_seconds",
                None,
            )
            checkpoint_remaining = getattr(
                checkpoint,
                "remaining_active_budget_seconds",
                None,
            )
            if (
                execution_remaining is None
                or checkpoint_remaining is None
                or float(execution_remaining)
                != float(plan.remaining_active_budget_seconds)
                or float(checkpoint_remaining)
                != float(plan.remaining_active_budget_seconds)
            ):
                raise ExecutionConflictError(
                    "STALE_RESUME_PLAN: active budget differs from checkpoint."
                )
            if (
                _utc_datetime(getattr(execution, "wait_expires_at", None))
                != _utc_datetime(plan.wait_expires_at)
                or _utc_datetime(getattr(checkpoint, "wait_expires_at", None))
                != _utc_datetime(plan.wait_expires_at)
            ):
                raise ExecutionConflictError(
                    "STALE_CHECKPOINT: wait expiry differs from plan."
                )

            state = getattr(execution, "context_state", None) or {}
            restored_limits = limits or AgentExecutionLimits.model_validate(
                state.get("limits", {})
            )
            metadata = dict(state.get("metadata", {}) or {})
            metadata["client_id"] = plan.target_client_id
            metadata["r7_resume_plan_fingerprint"] = plan.plan_fingerprint

            context = AgentExecutionContext.create(
                execution_id=plan.execution_id,
                agent_id=plan.agent_id,
                session_id=plan.session_id,
                correlation_id=plan.correlation_id,
                identity=identity,
                limits=restored_limits,
                request_id=plan.request_id,
                task_id=plan.task_id,
                branch_id=plan.branch_id,
                parent_execution_id=plan.parent_execution_id,
                retry_of_execution_id=plan.retry_of_execution_id,
                base_execution_id=plan.base_execution_id,
                base_checkpoint_id=plan.base_checkpoint_id,
                workflow_id=state.get("workflow_id"),
                connection_id=plan.target_connection_id,
                agent=agent,
                input=dict(execution.request or {}),
                metadata=metadata,
                causation_id=state.get("causation_id"),
                trace_id=plan.trace_id,
                remaining_active_budget_seconds=(
                    plan.remaining_active_budget_seconds
                ),
                wait_expires_at=plan.wait_expires_at,
                clock=clock,
                activate_budget=False,
            )
            context.iteration = plan.iteration

            # Execution-scoped budgets/accounting survive process restart.
            # A reconnect must not mint fresh retry capacity or discard model
            # usage accumulated before the WAITING checkpoint.
            durable_iterations = await uow.agents.list_iterations(
                plan.execution_id
            )
            usage_totals: dict[str, int | float] = {}
            for iteration in durable_iterations:
                response = getattr(iteration, "inference_response", None)
                raw_usage = (
                    dict(response.get("usage") or {})
                    if isinstance(response, dict)
                    else {}
                )
                for key, value in raw_usage.items():
                    if isinstance(value, bool) or not isinstance(
                        value, (int, float)
                    ):
                        continue
                    usage_totals[key] = usage_totals.get(key, 0) + value
            context.usage = InferenceUsage.model_validate(usage_totals)

            durable_tool_calls = await uow.agents.list_tool_calls(
                plan.execution_id
            )
            context.tool_calls_used = len(durable_tool_calls)
            retry_attempts_used = 0
            for tool_call in durable_tool_calls:
                result = await uow.agents.get_tool_result(
                    plan.execution_id,
                    tool_call.tool_call_id,
                )
                if result is None:
                    continue
                attempt = getattr(result, "attempt", 1)
                if isinstance(attempt, int) and not isinstance(attempt, bool):
                    retry_attempts_used += max(0, attempt - 1)
            context.retry_attempts_used = retry_attempts_used

            context.resume_transcript = [
                item.model_dump(mode="json")
                for item in plan.transcript_snapshot
            ]
            # Canonical R7-F4 never sends pending calls through the legacy
            # ordinary execute_capability() resume path.
            context.resume_pending_tool_calls = []
            context.resume_revision = plan.expected_execution_revision
            await uow.commit()
            return context

    async def resume_execution(
        self,
        execution_id: str,
        *,
        identity: Identity | None = None,
        limits: AgentExecutionLimits | None = None,
        agent=None,
        clock: ExecutionClock | None = None,
    ) -> AgentExecutionContext | None:
        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                await uow.commit()
                return None

            iterations = await uow.agents.list_iterations(execution_id)
            latest_iteration = max(
                iterations,
                key=lambda item: item.iteration,
                default=None,
            )
            checkpoint = None
            checkpoint_id = getattr(execution, "current_checkpoint_id", None)
            if checkpoint_id:
                checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
                if (
                    checkpoint is None
                    or checkpoint.execution_id != execution.id
                    or checkpoint.execution_revision != execution.revision
                ):
                    raise ExecutionConflictError(
                        "AgentExecution current checkpoint is missing or stale."
                    )
                latest_iteration = next(
                    (
                        item for item in iterations
                        if item.iteration == checkpoint.iteration
                    ),
                    None,
                )
                if latest_iteration is None:
                    raise ExecutionConflictError(
                        "Checkpoint iteration is missing from durable history."
                    )

            async def sanitize_transcript(raw):
                sanitized = []
                for message in list(raw or []):
                    if message.get("role") != "tool":
                        sanitized.append(message)
                        continue
                    tool_call_id = message.get("tool_call_id")
                    if not tool_call_id:
                        continue
                    result = await uow.agents.get_tool_result(
                        execution_id,
                        tool_call_id,
                    )
                    if (
                        result is None
                        or getattr(result, "commit_state", "PROVISIONAL")
                        != "COMMITTED"
                    ):
                        continue
                    sanitized.append(
                        {
                            "role": "tool",
                            "content": (
                                result.output
                                if result.success
                                else {
                                    "error_code": result.error_code,
                                    "error_message": result.error_message,
                                }
                            ),
                            "tool_calls": [],
                            "name": result.capability_id,
                            "tool_call_id": result.tool_call_id,
                            "metadata": {
                                "success": result.success,
                                "retryable": result.retryable,
                            },
                        }
                    )
                return sanitized

            resume_transcript = await sanitize_transcript(
                (
                    checkpoint.transcript_snapshot
                    if checkpoint is not None
                    else getattr(execution, "transcript", None) or (
                        getattr(latest_iteration, "transcript", None)
                        if latest_iteration
                        else []
                    )
                )
            )

            pending_tool_calls = []
            if latest_iteration is not None:
                calls = await uow.agents.list_tool_calls(
                    execution_id,
                    latest_iteration.id,
                )
                by_id = {item.tool_call_id: item for item in calls}
                canonical_ids = list(
                    getattr(latest_iteration, "tool_call_ids", None) or []
                )
                if checkpoint is not None and not canonical_ids:
                    raise ExecutionConflictError(
                        "Checkpointed tool batch has no canonical tool_call_ids."
                    )
                ordered_calls = []
                for tool_call_id in canonical_ids:
                    item = by_id.get(tool_call_id)
                    if item is None:
                        raise ExecutionConflictError(
                            f"Missing durable tool call {tool_call_id!r} "
                            "referenced by checkpoint iteration."
                        )
                    ordered_calls.append(item)
                if checkpoint is None and not canonical_ids:
                    ordered_calls = calls

                pending_tool_calls = [
                    {
                        "execution_id": item.execution_id,
                        "iteration": latest_iteration.iteration,
                        "invocation_id": item.invocation_id,
                        "tool_call_id": item.tool_call_id,
                        "capability_id": item.capability_id,
                        "arguments": item.arguments,
                        "connection_id": (
                            (getattr(item, "extra_metadata", None) or {}).get(
                                "connection_id"
                            )
                        ),
                        "metadata": {
                            **dict(getattr(item, "extra_metadata", None) or {}),
                            "iteration_id": item.iteration_id,
                            "persisted_status": item.status,
                        },
                    }
                    for item in ordered_calls
                ]

            if latest_iteration is not None:
                current_batch_ids = set(
                    getattr(latest_iteration, "tool_call_ids", None) or []
                )
                if current_batch_ids:
                    resume_transcript = [
                        message
                        for message in resume_transcript
                        if not (
                            message.get("role") == "tool"
                            and message.get("tool_call_id") in current_batch_ids
                        )
                    ]

            state = getattr(execution, "context_state", None) or {}
            restored_limits = limits or AgentExecutionLimits.model_validate(
                state.get("limits", {})
            )
            context = AgentExecutionContext.create(
                execution_id=execution.id,
                agent_id=execution.agent_id,
                session_id=execution.session_id,
                correlation_id=execution.correlation_id,
                identity=identity or Identity(
                    user_id="resume",
                    auth_type="api_key",
                    scopes={"*"},
                ),
                limits=restored_limits,
                request_id=state.get("request_id"),
                task_id=execution.task_id,
                branch_id=getattr(execution, "branch_id", None),
                parent_execution_id=getattr(
                    execution,
                    "parent_execution_id",
                    state.get("parent_execution_id"),
                ),
                retry_of_execution_id=getattr(
                    execution, "retry_of_execution_id", None
                ),
                base_execution_id=getattr(execution, "base_execution_id", None),
                base_checkpoint_id=getattr(execution, "base_checkpoint_id", None),
                workflow_id=state.get("workflow_id"),
                agent=agent,
                input=execution.request,
                metadata=state.get("metadata", {}),
                causation_id=state.get("causation_id"),
                trace_id=state.get("trace_id"),
                connection_id=state.get("connection_id"),
                remaining_active_budget_seconds=getattr(
                    execution,
                    "remaining_active_budget_seconds",
                    None,
                ),
                wait_expires_at=getattr(execution, "wait_expires_at", None),
                clock=clock,
                # Rehydration is read-only timing reconstruction.  Do not
                # consume active budget until the WAITING -> RUNNING CAS wins.
                activate_budget=False,
            )
            context.iteration = latest_iteration.iteration if latest_iteration else 0
            context.resume_transcript = resume_transcript
            context.resume_pending_tool_calls = pending_tool_calls
            context.resume_revision = getattr(execution, "revision", 0)
            await uow.commit()
            return context

    async def update_task(self, task_id: str, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TASK_JSON_FIELDS, path="agent_tasks"
        )
        async with self.uow_factory() as uow:
            record = await uow.agents.update_task(task_id, values)
            await uow.commit()
            return record