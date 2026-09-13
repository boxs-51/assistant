from __future__ import annotations

import asyncio

from src.application.policy.authorization import AuthorizationService
from src.domain.schemas.identity import Identity
from src.domain.schemas.workflow import WorkflowDefinition, WorkflowStep
from src.runtimes.capability.composition import DeclarativeWorkflowDriver
from src.runtimes.capability.contracts.context import CapabilityExecutionContext
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from src.runtimes.capability.registry import CapabilityRegistry
from src.runtimes.capability.runtime import CapabilityRuntime


def test_declarative_workflow_composes_capability_steps() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        registry.register_capability(
            PythonCapabilityDriver(
                CapabilityDefinition(
                    id="math.double",
                    name="math.double",
                    description="Double a value",
                    input_schema={"type": "object"},
                ),
                lambda value: value * 2,
            )
        )
        registry.register_capability(
            PythonCapabilityDriver(
                CapabilityDefinition(
                    id="math.add",
                    name="math.add",
                    description="Add two values",
                    input_schema={"type": "object"},
                ),
                lambda left, right: left + right,
            )
        )
        runtime = CapabilityRuntime(
            registry=registry,
            authorization=AuthorizationService(),
        )
        workflow = WorkflowDefinition(
            steps=[
                WorkflowStep(
                    step_id="double",
                    tool_name="math.double",
                    arguments={"value": "{{initial_input.value}}"},
                ),
                WorkflowStep(
                    step_id="add",
                    tool_name="math.add",
                    arguments={
                        "left": "{{steps.double}}",
                        "right": "{{initial_input.offset}}",
                    },
                ),
            ]
        )
        driver = DeclarativeWorkflowDriver(
            CapabilityDefinition(
                id="workflow.total",
                name="workflow.total",
                description="Double and add",
                input_schema={"type": "object"},
                execution_kind="DECLARATIVE",
            ),
            workflow,
            runtime,
        )
        result = await driver.execute(
            CapabilityExecutionContext.create(
                identity=Identity(auth_type="jwt"),
                execution_id="exec-workflow",
                invocation_id="inv-workflow",
            ),
            {"value": 4, "offset": 3},
        )
        assert result == 11

    asyncio.run(scenario())