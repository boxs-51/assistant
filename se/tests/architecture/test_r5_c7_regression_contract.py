from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import AgentExecutionContext
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.agent.task_budget import TaskBudgetService


def test_r5_c7_runtime_keeps_task_budget_optional_for_taskless_roots():
    runtime = AgentRuntime(
        context_builder=None,
        inference=None,
        tool_execution=None,
        execution_policy=None,
        durable_store=None,
        task_budget_service=TaskBudgetService(lambda: None),
    )

    context = AgentExecutionContext.create(
        execution_id="exec-taskless",
        agent_id="agent-taskless",
        session_id="session-taskless",
        correlation_id="corr-taskless",
        identity=Identity(
            user_id="user-taskless",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(),
        input={"prompt": "hello"},
    )

    assert runtime._task_budget_service is not None
    assert runtime._durable_store is None
    assert context.task_id is None
    assert runtime._uses_task_budget(context) is False
