import time
import uuid
import asyncio
import inspect
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
from ...domain.schemas.agent_execution import AgentExecution, AgentExecutionState
from ...domain.schemas.agent_execution import AgentExecutionLimits
from .state_machine import AgentExecutionStateMachine


class MultiAgentCoordinator:
    """Opt-in control plane for multi-agent sessions and delegation."""

    def __init__(self, agent_registry: AgentRegistry, durable_store=None, executor=None):
        self.agent_registry = agent_registry
        self.durable_store = durable_store
        self.executor = executor
        self._sessions: Dict[str, AgentSession] = {}
        self._tasks: Dict[str, AgentTask] = {}
        self._messages: Dict[str, List[AgentMessage]] = {}
        self._executions: Dict[str, AgentExecution] = {}

    async def _persist(self, method: str, values: dict):
        if self.durable_store is not None:
            await getattr(self.durable_store, method)(values)

    def _require_session(self, session_id: str, identity: Identity) -> AgentSession:
        session = self._sessions.get(session_id)
        if not session or session.owner_user_id != identity.user_id:
            raise LookupError("Agent session not found or access denied.")
        return session

    def _require_agent(self, agent_id: str) -> AgentDefinition:
        agent = self.agent_registry.get(agent_id)
        if not agent:
            raise LookupError(f"Agent '{agent_id}' is not registered.")
        return agent

    def create_session(self, identity: Identity, agent_ids: Optional[List[str]] = None) -> AgentSession:
        selected_agents = agent_ids or []
        for agent_id in selected_agents:
            self._require_agent(agent_id)
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
        self._require_agent(agent_id)
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
    ) -> AgentTask:
        session = self._require_session(session_id, identity)
        self._require_agent(assigned_agent_id)
        if assigned_agent_id not in session.agent_ids:
            raise PermissionError("Assigned agent is not a member of this session.")
        now = time.time()
        task = AgentTask(
            task_id=f"task_{uuid.uuid4().hex}",
            session_id=session_id,
            created_by=identity.user_id or "anonymous",
            assigned_agent_id=assigned_agent_id,
            parent_task_id=parent_task_id,
            status=AgentTaskStatus.ASSIGNED,
            input=task_input,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.task_id] = task
        return task

    async def create_task_async(self, *args, **kwargs) -> AgentTask:
        task = self.create_task(*args, **kwargs)
        await self._persist("save_task", {
            "id": task.task_id,
            "session_id": task.session_id,
            "created_by": task.created_by,
            "assigned_agent_id": task.assigned_agent_id,
            "parent_task_id": task.parent_task_id,
            "status": task.status.value,
            "input": task.input,
        })
        return task

    def get_task(self, task_id: str, identity: Identity) -> AgentTask:
        task = self._tasks.get(task_id)
        if not task:
            raise LookupError("Agent task not found.")
        self._require_session(task.session_id, identity)
        return task

    def cancel_task(self, task_id: str, identity: Identity) -> AgentTask:
        task = self.get_task(task_id, identity)
        task.status = AgentTaskStatus.CANCELLED
        task.updated_at = time.time()
        return task

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
        task.status = AgentTaskStatus.RUNNING
        execution = AgentExecution(
            execution_id=f"exec_{uuid.uuid4().hex}",
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
        await self._persist("save_execution", {
            "id": execution.execution_id,
            "session_id": execution.session_id,
            "agent_id": execution.agent_id,
            "task_id": execution.task_id,
            "parent_execution_id": execution.parent_execution_id,
            "correlation_id": execution.correlation_id,
            "state": execution.state.value,
            "request": execution.request,
        })
        execution_limits = limits or AgentExecutionLimits()
        try:
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.RUNNING
            )
            result_value = executor(task)
            if inspect.isawaitable(result_value):
                result = await asyncio.wait_for(
                    result_value, timeout=execution_limits.timeout_seconds
                )
            else:
                result = result_value
            execution.result = result if isinstance(result, dict) else {"value": result}
            task.status = AgentTaskStatus.COMPLETED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.COMPLETED
            )
        except asyncio.TimeoutError:
            execution.error = "Agent execution timed out."
            task.status = AgentTaskStatus.FAILED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.TIMEOUT
            )
        except asyncio.CancelledError:
            execution.error = "Agent execution cancelled."
            task.status = AgentTaskStatus.CANCELLED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.CANCELLED
            )
        except Exception as exc:
            execution.error = str(exc)
            task.status = AgentTaskStatus.FAILED
            execution.state = AgentExecutionStateMachine.transition(
                execution.state, AgentExecutionState.FAILED
            )
        execution.updated_at = time.time()
        task.output = execution.result
        task.error = execution.error
        if self.durable_store:
            await self.durable_store.update_execution(execution.execution_id, {
                "state": execution.state.value,
                "result": execution.result,
                "error": execution.error,
            })
            await self.durable_store.update_task(task.task_id, {
                "status": task.status.value,
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
