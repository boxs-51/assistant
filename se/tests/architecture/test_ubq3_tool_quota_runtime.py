from __future__ import annotations

import asyncio

import pytest

from se.src.application.user_tool_quota import ToolQuotaAdmission
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
)
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime


class _EchoDriver(BaseCapabilityDriver):
    def __init__(
        self,
        *,
        capability_id: str = "tool.ubq3",
        kind: CapabilityKind = CapabilityKind.TOOL,
    ) -> None:
        super().__init__(
            CapabilityDefinition(
                id=capability_id,
                name=capability_id,
                description="UBQ-3 runtime test",
                kind=kind,
                input_schema={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        )
        self.calls = 0

    async def execute(self, context, arguments):
        self.calls += 1
        return {
            "value": arguments["value"],
            "invocation_id": context.invocation_id,
        }


class _Quota:
    enabled = True

    def __init__(self) -> None:
        self.reserve_calls = []
        self.settle_calls = []
        self.release_calls = []

    async def reserve_tool_call(self, **kwargs):
        self.reserve_calls.append(dict(kwargs))
        invocation_id = str(kwargs["invocation_id"])
        return ToolQuotaAdmission(
            owner_user_id=str(kwargs["identity"].user_id),
            invocation_id=invocation_id,
            capability_id=str(kwargs["capability_id"]),
            request_fingerprint=str(kwargs["request_fingerprint"]),
            idempotency_key=f"quota:{invocation_id}",
            reservation_id=f"reservation:{invocation_id}",
            window_epoch=1,
            reservation_state="RESERVED",
        )

    async def settle_tool_call(self, admission):
        self.settle_calls.append(admission)
        return ToolQuotaAdmission(
            owner_user_id=admission.owner_user_id,
            invocation_id=admission.invocation_id,
            capability_id=admission.capability_id,
            request_fingerprint=admission.request_fingerprint,
            idempotency_key=admission.idempotency_key,
            reservation_id=admission.reservation_id,
            window_epoch=admission.window_epoch,
            reservation_state="SETTLED",
        )

    async def release_tool_call(self, admission):
        self.release_calls.append(admission)
        return ToolQuotaAdmission(
            owner_user_id=admission.owner_user_id,
            invocation_id=admission.invocation_id,
            capability_id=admission.capability_id,
            request_fingerprint=admission.request_fingerprint,
            idempotency_key=admission.idempotency_key,
            reservation_id=admission.reservation_id,
            window_epoch=admission.window_epoch,
            reservation_state="RELEASED",
        )


def _identity() -> Identity:
    return Identity(user_id="user-ubq3", auth_type="jwt", scopes={"*"})


@pytest.mark.asyncio
async def test_ubq3_runtime_validates_then_admits_then_settles_tool() -> None:
    store = InMemoryCapabilityInvocationStore()
    quota = _Quota()
    driver = _EchoDriver()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
        tool_quota_service=quota,
    )
    runtime.register_capability(driver)

    result = await runtime.execute_capability(
        driver.name,
        {"value": "ok"},
        _identity(),
        invocation_id="inv-runtime-ubq3",
    )

    assert result.output["value"] == "ok"
    assert driver.calls == 1
    assert len(quota.reserve_calls) == 1
    assert len(quota.settle_calls) == 1
    assert quota.release_calls == []

    persisted = store.items["inv-runtime-ubq3"]
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.execution_id is None
    attempts = await store.list_attempts("inv-runtime-ubq3")
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].state is CapabilityInvocationState.COMPLETED


@pytest.mark.asyncio
async def test_ubq3_runtime_invalid_tool_schema_has_zero_quota_mutation() -> None:
    store = InMemoryCapabilityInvocationStore()
    quota = _Quota()
    driver = _EchoDriver()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
        tool_quota_service=quota,
    )
    runtime.register_capability(driver)

    with pytest.raises(CapabilityError) as caught:
        await runtime.execute_capability(
            driver.name,
            {"value": 123},
            _identity(),
            invocation_id="inv-invalid-ubq3",
        )

    assert caught.value.code == "CAPABILITY_INVALID_ARGUMENT"
    assert quota.reserve_calls == []
    assert driver.calls == 0
    assert await store.get("inv-invalid-ubq3") is None


@pytest.mark.asyncio
async def test_ubq3_runtime_terminal_replay_has_no_second_attempt() -> None:
    store = InMemoryCapabilityInvocationStore()
    quota = _Quota()
    driver = _EchoDriver()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
        tool_quota_service=quota,
    )
    runtime.register_capability(driver)

    first = await runtime.execute_capability(
        driver.name,
        {"value": "same"},
        _identity(),
        invocation_id="inv-replay-ubq3",
    )
    second = await runtime.execute_capability(
        driver.name,
        {"value": "same"},
        _identity(),
        invocation_id="inv-replay-ubq3",
    )

    assert first.output == second.output
    assert driver.calls == 1
    assert len(quota.reserve_calls) == 2
    assert len(quota.settle_calls) == 2
    attempts = await store.list_attempts("inv-replay-ubq3")
    assert len(attempts) == 1
    assert second.metadata["replayed_terminal"] is True


@pytest.mark.asyncio
async def test_ubq3_non_tool_kind_does_not_use_tool_quota() -> None:
    store = InMemoryCapabilityInvocationStore()
    quota = _Quota()
    driver = _EchoDriver(
        capability_id="skill.ubq3",
        kind=CapabilityKind.SKILL,
    )
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
        tool_quota_service=quota,
    )
    runtime.register_capability(driver)

    result = await runtime.execute_capability(
        driver.name,
        {"value": "skill"},
        _identity(),
        invocation_id="inv-skill-ubq3",
    )

    assert result.output["value"] == "skill"
    assert quota.reserve_calls == []
    assert quota.settle_calls == []


@pytest.mark.asyncio
async def test_ubq3_disabled_tool_quota_preserves_legacy_runtime_path() -> None:
    class DisabledQuota(_Quota):
        enabled = False

    store = InMemoryCapabilityInvocationStore()
    quota = DisabledQuota()
    driver = _EchoDriver()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
        tool_quota_service=quota,
    )
    runtime.register_capability(driver)

    result = await runtime.execute_capability(
        driver.name,
        {"value": "legacy"},
        _identity(),
        invocation_id="inv-disabled-runtime",
    )

    assert result.output["value"] == "legacy"
    assert quota.reserve_calls == []
    assert quota.settle_calls == []
    persisted = store.items["inv-disabled-runtime"]
    assert persisted.state is CapabilityInvocationState.COMPLETED
    assert persisted.execution_id is not None
    assert persisted.revision == 3
    attempts = await store.list_attempts("inv-disabled-runtime")
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1


@pytest.mark.asyncio
async def test_ubq3_sql_initial_attempt_claim_is_atomic() -> None:
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": ":memory:"},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    lifecycle = CapabilityInvocationLifecycle(
        SqlCapabilityInvocationStore(
            lambda: SqlAlchemyUnitOfWork(driver)
        )
    )
    original = CapabilityInvocation(
        invocation_id="inv-sql-initial-race",
        capability_id="tool.ubq3",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
    )
    await lifecycle.create(original)
    left = await lifecycle.store.get(original.invocation_id)
    right = await lifecycle.store.get(original.invocation_id)
    assert left is not None and right is not None

    async def claim(candidate):
        try:
            return await lifecycle.begin_initial_attempt(
                candidate,
                implementation_id="legacy:tool.ubq3",
                driver_kind="_EchoDriver",
                connection_id=None,
            )
        except RuntimeError:
            return None

    outcomes = await asyncio.gather(claim(left), claim(right))
    winners = [item for item in outcomes if item is not None]
    assert len(winners) == 1
    stored = await lifecycle.store.get(original.invocation_id)
    attempts = await lifecycle.store.list_attempts(original.invocation_id)
    assert stored is not None
    assert stored.state is CapabilityInvocationState.DISPATCHING
    assert stored.attempt == 1
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1


@pytest.mark.asyncio
async def test_ubq3_initial_attempt_claim_allows_only_one_owner() -> None:
    store = InMemoryCapabilityInvocationStore()
    lifecycle = CapabilityInvocationLifecycle(store)
    original = CapabilityInvocation(
        invocation_id="inv-initial-race",
        capability_id="tool.ubq3",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
    )
    await lifecycle.create(original)

    left = await store.get(original.invocation_id)
    right = await store.get(original.invocation_id)
    assert left is not None and right is not None

    async def claim(candidate):
        try:
            return await lifecycle.begin_initial_attempt(
                candidate,
                implementation_id="legacy:tool.ubq3",
                driver_kind="_EchoDriver",
                connection_id=None,
            )
        except RuntimeError:
            return None

    outcomes = await asyncio.gather(claim(left), claim(right))
    winners = [item for item in outcomes if item is not None]
    assert len(winners) == 1

    stored = await store.get(original.invocation_id)
    attempts = await store.list_attempts(original.invocation_id)
    assert stored is not None
    assert stored.state is CapabilityInvocationState.DISPATCHING
    assert stored.attempt == 1
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1

    running, running_attempt = await lifecycle.start_initial_attempt(
        winners[0][0],
        winners[0][1],
    )
    assert running.state is CapabilityInvocationState.RUNNING
    assert running_attempt.state is CapabilityInvocationState.RUNNING
