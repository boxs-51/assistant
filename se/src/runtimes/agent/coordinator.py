import time
import uuid
import asyncio
import inspect
from datetime import datetime
from typing import Dict, List, Optional

from ...agent.registry import AgentRegistry
from ...domain.schemas.agent import AgentDefinition
from ...domain.schemas.identity import Identity
from ...domain.schemas.multi_agent import (
    AgentMessage,
    AgentMessageType,
    AgentSession,
    AgentSessionStatus,
    AgentTask,
    AgentTaskStatus,
)
from ...domain.schemas.agent_execution import (
    AgentExecution,
    AgentExecutionState,
    AgentExecutionWaitReason,
)
from ...domain.schemas.agent_execution import AgentExecutionLimits
from .state_machine import AgentExecutionStateMachine
from .ids import AgentExecutionIdFactory


class MultiAgentCoordinator:
    """Opt-in control plane for multi-agent sessions and delegation."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        durable_store=None,
        executor=None,
        execution_id_factory: AgentExecutionIdFactory | None = None,
        execution_supervisor=None,
        task_budget_service=None,
    ):
        self.agent_registry = agent_registry
        self.durable_store = durable_store
        self.executor = executor
        self.execution_supervisor = execution_supervisor
        self.task_budget_service = task_budget_service
        self.execution_id_factory = (
            execution_id_factory or AgentExecutionIdFactory()
        )
        self.agent_authorizer = None
        self._sessions: Dict[str, AgentSession] = {}
        self._tasks: Dict[str, AgentTask] = {}
        self._messages: Dict[str, List[AgentMessage]] = {}
        self._executions: Dict[str, AgentExecution] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}

    @staticmethod
    def _record_timestamp(value, fallback: float) -> float:
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, (int, float)):
            return float(value)
        return fallback

    def _sync_task_from_record(
        self,
        task: AgentTask,
        record,
    ) -> AgentTask:
        """Refresh the in-memory Task view from the durable CAS winner."""
        task.revision = int(getattr(record, "revision", task.revision))
        task.status = AgentTaskStatus(str(getattr(record, "status")))
        task.wait_reasons = list(
            getattr(record, "wait_reasons", None) or []
        )
        task.output = getattr(record, "output", None)
        task.error = getattr(record, "error", None)
        task.updated_at = self._record_timestamp(
            getattr(record, "updated_at", None),
            time.time(),
        )
        return task

    async def _persist(self, method: str, values: dict):
        if self.durable_store is not None:
            await getattr(self.durable_store, method)(values)

    def _require_session(self, session_id: str, identity: Identity) -> AgentSession:
        session = self._sessions.get(session_id)
        if not session or session.owner_user_id != identity.user_id:
            raise LookupError("Agent session not found or access denied.")
        return session

    def _require_agent(self, agent_id: str, identity: Identity) -> AgentDefinition:
        if self.agent_authorizer is not None and not self.agent_authorizer(
            identity, agent_id
        ):
            raise PermissionError(f"Agent '{agent_id}' is not permitted.")
        agent = self.agent_registry.get(agent_id)
        if not agent:
            raise LookupError(f"Agent '{agent_id}' is not registered.")
        return agent

    def create_session(self, identity: Identity, agent_ids: Optional[List[str]] = None) -> AgentSession:
        selected_agents = agent_ids or []
        for agent_id in selected_agents:
            self._require_agent(agent_id, identity)
        now = time.time()
        session = AgentSession(
            session_id=f"as_{uuid.uuid4().hex}",
            owner_user_id=identity.user_id or "anonymous",
            agent_ids=selected_agents,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session.session_id] = session
        self._messages[session.session_id] = []
        return session

    async def create_session_async(self, identity: Identity, agent_ids: Optional[List[str]] = None) -> AgentSession:
        session = self.create_session(identity, agent_ids)
        await self.durable_store.create_session(
            session.session_id, session.owner_user_id, session.agent_ids
        ) if self.durable_store else None
        return session

    def add_agent(self, session_id: str, agent_id: str, identity: Identity) -> AgentSession:
        session = self._require_session(session_id, identity)
        self._require_agent(agent_id, identity)
        if agent_id not in session.agent_ids:
            session.agent_ids.append(agent_id)
            session.updated_at = time.time()
        return session

    def send_message(
        self,
        session_id: str,
        sender_id: str,
        message_type: AgentMessageType,
        payload: dict,
        identity: Identity,
        recipient_id: Optional[str] = None,
    ) -> AgentMessage:
        session = self._require_session(session_id, identity)
        if sender_id != identity.user_id and sender_id not in session.agent_ids:
            raise PermissionError("Sender is not a member of this agent session.")
        if recipient_id and recipient_id not in session.agent_ids:
            raise PermissionError("Recipient is not a member of this agent session.")
        message = AgentMessage(
            message_id=f"msg_{uuid.uuid4().hex}",
            session_id=session_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_type=message_type,
            payload=payload,
            created_at=time.time(),
        )
        self._messages[session_id].append(message)
        session.updated_at = time.time()
        return message

    async def send_message_async(self, *args, **kwargs) -> AgentMessage:
        message = self.send_message(*args, **kwargs)
        await self._persist("save_message", {
            "id": message.message_id,
            "session_id": message.session_id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "message_type": message.message_type.value,
            "payload": message.payload,
        })
        return message

    def list_messages(self, session_id: str, identity: Identity) -> List[AgentMessage]:
        self._require_session(session_id, identity)
        return list(self._messages[session_id])

    def create_task(
        self,
        session_id: str,
        assigned_agent_id: str,
        task_input: dict,
        identity: Identity,
        parent_task_id: Optional[str] = None,
        connection_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> AgentTask:
        session = self._require_session(session_id, identity)
        self._require_agent(assigned_agent_id, identity)
        if assigned_agent_id not in session.agent_ids:
            raise PermissionError("Assigned agent is not a member of this session.")
        now = time.time()
        task = AgentTask(
            task_id=f"task_{uuid.uuid4().hex}",
            session_id=session_id,
            created_by=identity.user_id or "anonymous",
            assigned_agent_id=assigned_agent_id,
            parent_task_id=parent_task_id,
            connection_id=connection_id,
            client_id=client_id,
            status=AgentTaskStatus.ASSIGNED,
            input=task_input,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.task_id] = task
        return task

    async def create_task_async(self, *args, **kwargs) -> AgentTask:
        task = self.create_task(*args, **kwargs)
        values = {
            "id": task.task_id,
            "session_id": task.session_id,
            "created_by": task.created_by,
            "assigned_agent_id": task.assigned_agent_id,
            "revision": task.revision,
            "parent_task_id": task.parent_task_id,
            "connection_id": task.connection_id,
            "client_id": task.client_id,
            "status": task.status.value,
            "wait_reasons": task.wait_reasons,
            "input": task.input,
        }
        try:
            if self.task_budget_service is not None:
                await self.task_budget_service.create_task_with_budget(values)
            else:
                await self._persist("save_task", values)
        except BaseException:
            if self._tasks.get(task.task_id) is task:
                self._tasks.pop(task.task_id, None)
            raise
        return task

    def get_task(self, task_id: str, identity: Identity) -> AgentTask:
        task = self._tasks.get(task_id)
        if not task:
            raise LookupError("Agent task not found.")
        self._require_session(task.session_id, identity)
        return task

    def cancel_task(self, task_id: str, identity: Identity) -> AgentTask:
        """Compatibility facade.

        Production transport uses ``cancel_task_and_wait`` so process-local
        runner and Agent execution ownership are drained before returning.
        """
        if self.task_budget_service is not None:
            raise RuntimeError(
                "Durable AgentTask cancellation requires "
                "cancel_task_and_wait()."
            )
        task = self.get_task(task_id, identity)
        running = self._running_tasks.get(task_id)
        if running is not None and not running.done():
            running.cancel()
        task.status = AgentTaskStatus.CANCELLED
        task.updated_at = time.time()
        return task

    async def cancel_task_and_wait(
        self,
        task_id: str,
        identity: Identity,
    ) -> AgentTask:
        task = self.get_task(task_id, identity)
        runner = self._running_tasks.get(task_id)

        if self.task_budget_service is not None:
            durable = await self.task_budget_service.cancel_task(
                task_id,
                values={
                    "wait_reasons": task.wait_reasons,
                    "output": task.output,
                    "error": task.error,
                },
            )
            self._sync_task_from_record(task, durable)
            # COMPLETED/FAILED is an already-authoritative terminal winner.
            if task.status is not AgentTaskStatus.CANCELLED:
                return task
        else:
            task.status = AgentTaskStatus.CANCELLED
            task.updated_at = time.time()

        if runner is not None and not runner.done():
            runner.cancel()

        supervisor = self.execution_supervisor
        if supervisor is not None:
            await supervisor.cancel_task(task_id)

        if runner is not None:
            await asyncio.gather(runner, return_exceptions=True)

        if self.durable_store and self.task_budget_service is None:
            await self.durable_store.update_task(
                task.task_id,
                {
                    "status": task.status.value,
                    "wait_reasons": task.wait_reasons,
                    "output": task.output,
                    "error": task.error,
                },
            )
        return task

    async def start_task(self, task_id: str, identity: Identity, executor) -> AgentTask:
        task = self.get_task(task_id, identity)
        if task_id in self._running_tasks and not self._running_tasks[task_id].done():
            raise ValueError(f"Agent task '{task_id}' is already running.")
        if task.status in {
            AgentTaskStatus.WAITING,
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        }:
            raise ValueError(
                f"Agent task '{task_id}' cannot start from {task.status.value}."
            )

        runner = asyncio.create_task(
            self.execute_task(task_id, identity, executor),
            name=f"agent-task:{task_id}",
        )
        self._running_tasks[task_id] = runner

        def cleanup(_completed):
            if self._running_tasks.get(task_id) is runner:
                self._running_tasks.pop(task_id, None)
            if not _completed.cancelled():
                try:
                    _completed.exception()
                except Exception:
                    pass

        runner.add_done_callback(cleanup)
        await asyncio.sleep(0)
        return task

    async def shutdown(self) -> None:
        runners = list(self._running_tasks.values())
        for runner in runners:
            if not runner.done():
                runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)
        self._running_tasks.clear()

    def close_session(self, session_id: str, identity: Identity) -> AgentSession:
        session = self._require_session(session_id, identity)
        session.status = AgentSessionStatus.CANCELLED
        session.updated_at = time.time()
        return session

    async def execute_task(
        self,
        task_id: str,
        identity: Identity,
        executor,
        limits=None,
        parent_execution_id: Optional[str] = None,
    ) -> AgentExecution:
        task = self.get_task(task_id, identity)
        if task.status is AgentTaskStatus.WAITING:
            raise ValueError(
                f"Agent task '{task_id}' is WAITING and must resume its "
                "existing AgentExecution."
            )
        if task.status in {
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        }:
            raise ValueError(f"Agent task '{task_id}' is already terminal.")

        if self.task_budget_service is not None:
            durable = await self.task_budget_service.transition_task(
                task_id,
                allowed_source_states=("ASSIGNED",),
                target_state="RUNNING",
                values={
                    "wait_reasons": [],
                    "output": None,
                    "error": None,
                },
            )
            self._sync_task_from_record(task, durable)
            if task.status is not AgentTaskStatus.RUNNING:
                raise ValueError(
                    f"Agent task '{task_id}' is already terminal."
                )
        else:
            task.status = AgentTaskStatus.RUNNING

        execution = AgentExecution(
            execution_id=self.execution_id_factory.new_id(),
            session_id=task.session_id,
            agent_id=task.assigned_agent_id,
            task_id=task.task_id,
            parent_execution_id=parent_execution_id,
            correlation_id=f"corr_{uuid.uuid4().hex}",
            request=task.input,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._executions[execution.execution_id] = execution
        execution_limits = limits or AgentExecutionLimits()
        target_status = AgentTaskStatus.RUNNING
        target_wait_reasons: list[str] = []
        try:
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.RUNNING
            )
            try:
                executor_signature = inspect.signature(executor)
                accepts_kwargs = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in executor_signature.parameters.values()
                )
                accepts_identity = "identity" in executor_signature.parameters or accepts_kwargs
                runtime_owned = "execution_id" in executor_signature.parameters or accepts_kwargs
            except (TypeError, ValueError):
                accepts_identity = False
                runtime_owned = False
            if self.durable_store is not None and not runtime_owned:
                raise RuntimeError(
                    "Durable Agent execution requires an AgentRuntime-owned "
                    "executor accepting execution_id."
                )
            executor_kwargs = {}
            if accepts_identity:
                executor_kwargs["identity"] = identity
            if runtime_owned:
                executor_kwargs.update(
                    execution_id=execution.execution_id,
                    correlation_id=execution.correlation_id,
                    parent_execution_id=parent_execution_id,
                )
            result_value = executor(task, **executor_kwargs)
            if inspect.isawaitable(result_value):
                result = await asyncio.wait_for(
                    result_value, timeout=execution_limits.timeout_seconds
                )
            else:
                result = result_value
            execution.result = result if isinstance(result, dict) else {"value": result}
            if execution.result.get("error_code") == "WAITING_FOR_CONNECTION":
                target_status = AgentTaskStatus.WAITING
                target_wait_reasons = [
                    AgentExecutionWaitReason.CONNECTION.value
                ]
                AgentExecutionStateMachine.transition(
                    execution.state,
                    AgentExecutionState.WAITING,
                )
                execution = AgentExecution.model_validate({
                    **execution.model_dump(mode="python"),
                    "state": AgentExecutionState.WAITING,
                    "wait_reason": AgentExecutionWaitReason.CONNECTION,
                })
                self._executions[execution.execution_id] = execution
            elif str(execution.result.get("state", "")).upper() == "WAITING":
                wait_reason = execution.result.get("wait_reason")
                if wait_reason is None:
                    raise ValueError(
                        "Canonical WAITING result requires explicit wait_reason."
                    )
                wait_reason = AgentExecutionWaitReason(wait_reason).value
                target_status = AgentTaskStatus.WAITING
                target_wait_reasons = [wait_reason]
                AgentExecutionStateMachine.transition(
                    execution.state,
                    AgentExecutionState.WAITING,
                )
                execution = AgentExecution.model_validate({
                    **execution.model_dump(mode="python"),
                    "state": AgentExecutionState.WAITING,
                    "wait_reason": wait_reason,
                })
                self._executions[execution.execution_id] = execution
            elif (
                execution.result.get("error_code")
                or str(execution.result.get("state", "")).upper()
                in {"FAILED", "TIMEOUT", "CANCELLED"}
            ):
                execution.error = (
                    execution.result.get("error_message")
                    or execution.result.get("error_code")
                    or "Agent execution failed."
                )
                target_status = AgentTaskStatus.FAILED
                execution.state = AgentExecutionStateMachine.transition(
                    execution.state, AgentExecutionState.FAILED
                )
            else:
                target_status = AgentTaskStatus.COMPLETED
                execution.state = AgentExecutionStateMachine.transition(
                    execution.state, AgentExecutionState.COMPLETED
                )
        except asyncio.TimeoutError:
            execution.error = "Agent execution timed out."
            target_status = AgentTaskStatus.FAILED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.TIMEOUT
            )
        except asyncio.CancelledError:
            execution.error = "Agent execution cancelled."
            target_status = AgentTaskStatus.CANCELLED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.CANCELLED
            )
        except Exception as exc:
            execution.error = str(exc)
            target_status = AgentTaskStatus.FAILED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.FAILED
            )
        execution.updated_at = time.time()
        target_values = {
            "wait_reasons": target_wait_reasons,
            "output": execution.result,
            "error": execution.error,
        }
        if self.task_budget_service is not None:
            if target_status is AgentTaskStatus.WAITING:
                durable = await self.task_budget_service.transition_task(
                    task_id,
                    allowed_source_states=("RUNNING",),
                    target_state=AgentTaskStatus.WAITING.value,
                    values=target_values,
                )
            else:
                durable = await self.task_budget_service.terminalize_task(
                    task_id,
                    allowed_source_states=("RUNNING",),
                    target_state=target_status.value,
                    values=target_values,
                )
            self._sync_task_from_record(task, durable)
        else:
            task.status = target_status
            task.wait_reasons = target_wait_reasons
            task.output = execution.result
            task.error = execution.error

        if self.durable_store and self.task_budget_service is None:
            await self.durable_store.update_task(task.task_id, {
                "status": task.status.value,
                "wait_reasons": task.wait_reasons,
                "output": task.output,
                "error": task.error,
            })
        return execution

    async def execute_parallel(self, task_ids: List[str], identity: Identity, executor, max_parallel: int = 4):
        semaphore = asyncio.Semaphore(max_parallel)

        async def run(task_id):
            async with semaphore:
                return await self.execute_task(task_id, identity, executor)

        return await asyncio.gather(*(run(task_id) for task_id in task_ids))

    async def execute_supervisor(self, task_ids: List[str], identity: Identity, executor):
        results = []
        for task_id in task_ids:
            results.append(await self.execute_task(task_id, identity, executor))
            if results[-1].state is not AgentExecutionState.COMPLETED:
                break
        return results

    def get_execution(self, execution_id: str, identity: Identity) -> AgentExecution:
        execution = self._executions.get(execution_id)
        if not execution:
            raise LookupError("Agent execution not found.")
        self._require_session(execution.session_id, identity)
        return execution