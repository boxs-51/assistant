from __future__ import annotations

import pytest

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.sqlite.driver import SQLiteDriver
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.capability_invocations import (
    SqlCapabilityInvocationStore,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from se.src.runtimes.capability.contracts.error import (
    REMOTE_INVOCATION_CONFLICT,
    CapabilityError,
)
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    ExistingInvocationContinuationMode,
    RemoteOutcomeState,
)
from se.src.runtimes.capability.contracts.target import (
    CapabilityInvocationTarget,
    ResourceScope,
)
from se.src.runtimes.capability.fingerprint import capability_request_fingerprint
from se.src.runtimes.capability.invocation import CapabilityInvocationLifecycle
from se.src.runtimes.capability.runtime import CapabilityRuntime


def _invocation(target):
    arguments = {"path": "report.txt"}
    fingerprint = capability_request_fingerprint(
        capability_id="crt.remote",
        capability_version="1.0",
        arguments=arguments,
        target=target,
    )
    return CapabilityInvocation(
        invocation_id="inv-crt-cont",
        capability_id="crt.remote",
        capability_version="1.0",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        request_fingerprint=fingerprint,
        target=target,
        owner_user_id="user-crt",
        origin_client_id=(target.stable_client_id if target else "client-a"),
        remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        implementation_id="client-a:crt.remote",
        driver_kind="REMOTE_CLIENT",
        state=CapabilityInvocationState.WAITING,
        wait_reason=CapabilityWaitReason.CONNECTION,
        execution_id="exec-crt",
        tool_call_id="call-crt",
        connection_id="conn-old",
        attempt=1,
        arguments=arguments,
    )


@pytest.mark.asyncio
async def test_restart_preserves_target_and_recomputed_continuation_identity(tmp_path):
    driver = SQLiteDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"path": str(tmp_path / "crt1-restart.sqlite3")},
        )
    )
    async with driver._engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = lambda: SqlAlchemyUnitOfWork(driver)
    target = CapabilityInvocationTarget(
        resource_scope=ResourceScope.CLIENT_LOCAL,
        resource_ref="client://client-a/report.txt",
        stable_client_id="client-a",
    )
    original = _invocation(target)
    try:
        first_store = SqlCapabilityInvocationStore(factory)
        await first_store.create(original)

        restarted_store = SqlCapabilityInvocationStore(factory)
        loaded = await restarted_store.get(original.invocation_id)
        assert loaded is not None
        assert loaded.target == target

        runtime = CapabilityRuntime(
            invocation_lifecycle=CapabilityInvocationLifecycle(restarted_store)
        )
        runtime._validate_existing_continuation(
            loaded,
            mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
            expected_revision=loaded.revision,
            expected_request_fingerprint=loaded.request_fingerprint,
        )

        new_generation = loaded.model_copy(
            update={"connection_id": "conn-new"}
        )
        runtime._validate_existing_continuation(
            new_generation,
            mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
            expected_revision=new_generation.revision,
            expected_request_fingerprint=new_generation.request_fingerprint,
        )

        changed_target = target.model_copy(
            update={"stable_client_id": "foreign-client"}
        )
        tampered = loaded.model_copy(update={"target": changed_target})
        with pytest.raises(CapabilityError) as exc_info:
            runtime._validate_existing_continuation(
                tampered,
                mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
                expected_revision=tampered.revision,
                expected_request_fingerprint=tampered.request_fingerprint,
            )
        assert exc_info.value.code == REMOTE_INVOCATION_CONFLICT
    finally:
        await driver.disconnect()


def test_legacy_null_target_continuation_keeps_legacy_fingerprint():
    invocation = _invocation(None)
    runtime = CapabilityRuntime()
    runtime._validate_existing_continuation(
        invocation,
        mode=ExistingInvocationContinuationMode.DISPATCH_NOT_DISPATCHED,
        expected_revision=invocation.revision,
        expected_request_fingerprint=invocation.request_fingerprint,
    )
