from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.multi_agent import AgentTaskForkRequest
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator


TASK = "task-r8-f-durable-api"
BRANCH = "branch-r8-f-durable-api"
USER = "user-r8-f-durable-api"


def _identity(user_id=USER):
    return Identity(
        user_id=user_id,
        auth_type="api_key",
        scopes={"*"},
    )


class _Store:
    def __init__(self):
        self.task = SimpleNamespace(
            id=TASK,
            created_by=USER,
            session_id="session-r8-f-durable-api",
        )
        self.branch = SimpleNamespace(
            branch_id=BRANCH,
            task_id=TASK,
            parent_branch_id=None,
            base_execution_id=None,
            base_checkpoint_id=None,
            current_execution_id="exec-r8-f-durable-api",
            resolution_state="OPEN",
            revision=0,
            created_by=USER,
            reason=None,
            created_at=None,
            updated_at=None,
        )

    async def load_task(self, task_id):
        return self.task if task_id == TASK else None

    async def list_task_branches(self, task_id):
        return [self.branch] if task_id == TASK else []

    async def load_task_branch(self, branch_id):
        return self.branch if branch_id == BRANCH else None


def _request():
    return AgentTaskForkRequest(
        fork_request_id="fork-r8-f-durable-api",
        source_branch_id="source-branch",
        source_execution_id="source-execution",
        source_checkpoint_id="source-checkpoint",
        overlay_messages=[{"role": "user", "content": "fork"}],
    )


@pytest.mark.asyncio
async def test_r8_f_branch_reads_are_durable_after_empty_coordinator_caches():
    store = _Store()
    coordinator = MultiAgentCoordinator(
        AgentRegistry(),
        durable_store=store,
    )

    # A fresh coordinator intentionally has no process-local session/task maps.
    assert coordinator._sessions == {}
    assert coordinator._tasks == {}

    branches = await coordinator.list_task_branches_durable(
        TASK,
        _identity(),
    )
    branch = await coordinator.get_task_branch_durable(
        BRANCH,
        _identity(),
    )

    assert [item.branch_id for item in branches] == [BRANCH]
    assert branch.branch_id == BRANCH
    assert branch.task_id == TASK


@pytest.mark.asyncio
async def test_r8_f_foreign_principal_cannot_read_durable_branches():
    coordinator = MultiAgentCoordinator(
        AgentRegistry(),
        durable_store=_Store(),
    )

    with pytest.raises(PermissionError):
        await coordinator.list_task_branches_durable(
            TASK,
            _identity("foreign"),
        )

    with pytest.raises(PermissionError):
        await coordinator.get_task_branch_durable(
            BRANCH,
            _identity("foreign"),
        )


@pytest.mark.asyncio
async def test_r8_f_fork_transport_principal_is_identity_owned():
    store = _Store()
    calls = []

    async def fork_executor(task_id, request, identity):
        calls.append((task_id, request.fork_request_id, identity.user_id))
        return {
            "task_id": task_id,
            "fork_request_id": request.fork_request_id,
            "branch_id": "new-branch",
            "execution_id": "new-execution",
            "execution_state": "RUNNING",
            "execution_revision": 2,
            "started": True,
        }

    coordinator = MultiAgentCoordinator(
        AgentRegistry(),
        durable_store=store,
        fork_executor=fork_executor,
    )

    response = await coordinator.fork_task(
        TASK,
        _request(),
        _identity(),
    )
    assert response.task_id == TASK
    assert calls == [(TASK, "fork-r8-f-durable-api", USER)]

    with pytest.raises(PermissionError):
        await coordinator.fork_task(
            TASK,
            _request(),
            _identity("foreign"),
        )
    assert len(calls) == 1
