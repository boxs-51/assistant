from typing import Any, Dict, List, Optional

from ...domain.schemas.agent_execution import AgentExecutionLimits
from ...domain.schemas.identity import Identity
from ...infrastructure.storage.repositories.agent import AgentRepository
from .contracts.clock import ExecutionClock
from .contracts.context import AgentExecutionContext
from .contracts.resume import (
    CheckpointPendingInvocation,
    DurableExecutionCheckpoint,
)
from .serialization import to_json_safe
from .waiting_checkpoint import (
    WaitingCheckpointConflictError,
    stage_waiting_checkpoint,
    verify_committed_waiting_checkpoint,
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
                current_state = to_json_safe(
                    getattr(execution, "context_state", None) or {},
                    path="agent_executions.context_state",
                )
                continuation = current_state.get("continuation")
                merged_state = {**current_state, **incoming_state}
                if continuation is not None and "continuation" not in incoming_state:
                    merged_state["continuation"] = continuation
                values["context_state"] = merged_state

            record = await uow.agents.update_execution(execution_id, values)
            await uow.commit()
            return record

    async def save_continuation_state(
        self,
        execution_id: str,
        state: Dict[str, Any],
    ):
        """Persist Phase 6.9 continuation data in the existing JSON column."""
        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                raise KeyError(f"Unknown agent execution: {execution_id}")
            context_state = to_json_safe(
                getattr(execution, "context_state", None) or {},
                path="agent_executions.context_state",
            )
            context_state["continuation"] = to_json_safe(
                state,
                path="agent_executions.context_state.continuation",
            )
            record = await uow.agents.update_execution(
                execution_id,
                {"context_state": context_state},
            )
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

    async def load_continuation_state(
        self,
        execution_id: str,
    ) -> Dict[str, Any] | None:
        async with self.uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            await uow.commit()
            if execution is None:
                return None
            context_state = dict(getattr(execution, "context_state", None) or {})
            continuation = context_state.get("continuation")
            return dict(continuation) if continuation else None

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