from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.fork_planning import ForkPlanDeferred
from se.src.runtimes.agent.task_budget import ForkConsumeConflict
from se.src.transport.gateway.api.v1 import multi_agent_router
from se.src.transport.gateway.authentication.dependency import (
    get_current_identity,
)


USER = "user-r8-g"
TASK = "task-r8-g"


def _identity(user_id: str = USER) -> Identity:
    return Identity(
        user_id=user_id,
        auth_type="api_key",
        scopes={"*"},
    )


def _fork_body() -> dict:
    return {
        "fork_request_id": "fork-r8-g",
        "source_branch_id": "branch-r8-g-source",
        "source_execution_id": "exec-r8-g-source",
        "source_checkpoint_id": "cp-r8-g-source",
        "overlay_messages": [{"role": "user", "content": "fork"}],
    }


class _ForkCoordinator:
    def __init__(self, error: Exception):
        self.error = error

    async def fork_task(self, task_id, request, identity):
        assert task_id == TASK
        assert request.fork_request_id == "fork-r8-g"
        assert identity.user_id == USER
        raise self.error


def _fork_app(error: Exception) -> FastAPI:
    app = FastAPI()
    app.include_router(multi_agent_router.router)
    coordinator = _ForkCoordinator(error)
    app.dependency_overrides[multi_agent_router.get_coordinator] = (
        lambda: coordinator
    )
    app.dependency_overrides[get_current_identity] = lambda: _identity()
    return app


def test_r8_g_public_fork_capacity_defer_preserves_code_and_retryability():
    app = _fork_app(
        ForkPlanDeferred(
            "FORK_BRANCH_CAPACITY_UNAVAILABLE",
            "TaskBudget max_active_branches is exhausted.",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            f"/v1/multi-agent/tasks/{TASK}/fork",
            json=_fork_body(),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "FORK_BRANCH_CAPACITY_UNAVAILABLE",
        "message": "TaskBudget max_active_branches is exhausted.",
        "retryable": True,
    }


def test_r8_g_public_fork_semantic_conflict_is_structured_nonretryable():
    app = _fork_app(
        ForkConsumeConflict(
            "FORK_REQUEST_SEMANTIC_CONFLICT",
            "fork_request_id already committed with different semantics.",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            f"/v1/multi-agent/tasks/{TASK}/fork",
            json=_fork_body(),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "FORK_REQUEST_SEMANTIC_CONFLICT",
        "message": "fork_request_id already committed with different semantics.",
        "retryable": False,
    }


class _DurableTaskStore:
    def __init__(self):
        self.task = SimpleNamespace(
            id=TASK,
            session_id="session-r8-g",
            created_by=USER,
            assigned_agent_id="agent-r8-g",
            revision=3,
            parent_task_id=None,
            connection_id=None,
            client_id=None,
            status="RUNNING",
            wait_reasons=[],
            input={"goal": "prove restart-safe cancel"},
            output=None,
            error=None,
            created_at=1.0,
            updated_at=2.0,
        )

    async def load_task(self, task_id):
        return self.task if task_id == TASK else None


class _BudgetService:
    def __init__(self, source):
        self.source = source
        self.calls = []

    async def cancel_task(self, task_id, *, values):
        self.calls.append((task_id, values))
        return SimpleNamespace(
            **{
                **self.source.__dict__,
                "revision": 4,
                "status": "CANCELLED",
                "updated_at": 3.0,
            }
        )


class _Supervisor:
    def __init__(self):
        self.cancelled = []

    async def cancel_task(self, task_id):
        self.cancelled.append(task_id)


@pytest.mark.asyncio
async def test_r8_g_durable_cancel_works_with_empty_coordinator_caches():
    store = _DurableTaskStore()
    budget = _BudgetService(store.task)
    supervisor = _Supervisor()
    coordinator = MultiAgentCoordinator(
        AgentRegistry(),
        durable_store=store,
        task_budget_service=budget,
        execution_supervisor=supervisor,
    )

    assert coordinator._sessions == {}
    assert coordinator._tasks == {}

    result = await coordinator.cancel_task_and_wait(TASK, _identity())

    assert result.task_id == TASK
    assert result.status.value == "CANCELLED"
    assert result.revision == 4
    assert budget.calls == [
        (
            TASK,
            {
                "wait_reasons": [],
                "output": None,
                "error": None,
            },
        )
    ]
    assert supervisor.cancelled == [TASK]


@pytest.mark.asyncio
async def test_r8_g_durable_cancel_rejects_foreign_principal_before_mutation():
    store = _DurableTaskStore()
    budget = _BudgetService(store.task)
    supervisor = _Supervisor()
    coordinator = MultiAgentCoordinator(
        AgentRegistry(),
        durable_store=store,
        task_budget_service=budget,
        execution_supervisor=supervisor,
    )

    with pytest.raises(PermissionError):
        await coordinator.cancel_task_and_wait(
            TASK,
            _identity("foreign-user"),
        )

    assert budget.calls == []
    assert supervisor.cancelled == []
