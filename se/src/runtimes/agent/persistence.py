from typing import Any, Dict, List, Optional

from ...domain.schemas.agent_execution import AgentExecutionLimits
from ...domain.schemas.identity import Identity
from ...infrastructure.storage.repositories.agent import AgentRepository
from .contracts.context import AgentExecutionContext
from .serialization import to_json_safe


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

    async def save_tool_call(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TOOL_CALL_JSON_FIELDS, path="agent_tool_calls"
        )
        async with self.uow_factory() as uow:
            existing = await uow.agents.get_tool_call(
                values["execution_id"], values["tool_call_id"]
            )
            record = existing or await uow.agents.save_tool_call(values)
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

    async def save_tool_result(self, values: Dict[str, Any]):
        values = _normalize_json_fields(
            values, _TOOL_RESULT_JSON_FIELDS, path="agent_tool_results"
        )
        async with self.uow_factory() as uow:
            existing = await uow.agents.get_tool_result(
                values["execution_id"], values["tool_call_id"]
            )
            record = existing or await uow.agents.save_tool_result(values)
            await uow.commit()
            return record

    async def load_tool_result(self, execution_id: str, tool_call_id: str):
        async with self.uow_factory() as uow:
            record = await uow.agents.get_tool_result(execution_id, tool_call_id)
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

    async def resume_execution(
        self,
        execution_id: str,
        *,
        identity: Identity | None = None,
        limits: AgentExecutionLimits | None = None,
        agent=None,
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
            pending_tool_calls = []
            if latest_iteration is not None:
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
                    for item in await uow.agents.list_tool_calls(
                        execution_id,
                        latest_iteration.id,
                    )
                    if item.status != "COMPLETED"
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
                parent_execution_id=state.get("parent_execution_id"),
                workflow_id=state.get("workflow_id"),
                agent=agent,
                input=execution.request,
                metadata=state.get("metadata", {}),
                causation_id=state.get("causation_id"),
                trace_id=state.get("trace_id"),
                connection_id=state.get("connection_id"),
            )
            context.iteration = latest_iteration.iteration if latest_iteration else 0
            context.resume_transcript = getattr(execution, "transcript", None) or (
                getattr(latest_iteration, "transcript", None)
                if latest_iteration
                else []
            )
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