from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from se.src.context.memory_promotion import (
    MemoryPromotionIntent,
    SourcePromotionAuthorityPort,
)
from se.src.context.source_identity import (
    ContextSourceKind,
    create_context_source_ref,
)
from se.src.context.tool_response_payload import (
    COMMITTED_RESULT_STATE,
    TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
    canonical_payload_bytes,
    tool_response_payload_id,
)
from se.src.infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import Session
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
    ToolResponsePayloadSourceRejectedError,
    ToolResponsePayloadSourceUnavailableError,
)


NOW = datetime(2026, 10, 2, 7, 45, tzinfo=timezone.utc)
OWNER = "user-ctx-f5-3i-b1"


def _evidence(*, result_overrides=None):
    output = {"kind": "tool-result", "nested": [1, {"ok": True}]}
    result_values = {
        "id": "result-1",
        "execution_id": "execution-1",
        "iteration_id": "iteration-1",
        "invocation_id": "invocation-1",
        "tool_call_id": "tool-call-1",
        "capability_id": "tool.echo",
        "success": True,
        "output": output,
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "commit_state": COMMITTED_RESULT_STATE,
    }
    result_values.update(result_overrides or {})
    result = SimpleNamespace(**result_values)
    execution = SimpleNamespace(
        id="execution-1",
        session_id="session-1",
    )
    session = SimpleNamespace(
        id="session-1",
        user_id=OWNER,
    )
    invocation = SimpleNamespace(
        invocation_id="invocation-1",
        execution_id="execution-1",
        session_id="session-1",
        owner_user_id=OWNER,
        tool_call_id="tool-call-1",
        capability_id="tool.echo",
    )
    tool_call = SimpleNamespace(
        execution_id="execution-1",
        iteration_id="iteration-1",
        tool_call_id="tool-call-1",
        invocation_id="invocation-1",
        capability_id="tool.echo",
    )
    return result, execution, session, invocation, tool_call


def _source_ref(*, created_at=NOW, extra_metadata=None):
    result, _, _, _, _ = _evidence()
    digest = __import__("hashlib").sha256(
        canonical_payload_bytes(result.output)
    ).hexdigest()
    payload_id = tool_response_payload_id(
        source_result_id=result.id,
        invocation_id=result.invocation_id,
        execution_id=result.execution_id,
        tool_call_id=result.tool_call_id,
        logical_capability_id=result.capability_id,
        content_digest=digest,
        payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
    )
    metadata = {"source_result_id": result.id}
    metadata.update(extra_metadata or {})
    return create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=payload_id,
        owner_user_id=OWNER,
        session_id="session-1",
        source_created_at=created_at,
        source_state=COMMITTED_RESULT_STATE,
        metadata=metadata,
    )


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(
        self,
        *,
        result,
        execution,
        canonical_session,
        invocation,
        tool_call,
        failure: BaseException | None = None,
    ) -> None:
        self.rows = {
            AgentToolResultRecord: result,
            AgentExecutionRecord: execution,
            Session: canonical_session,
            CapabilityInvocationRecord: invocation,
        }
        self.tool_call = tool_call
        self.failure = failure
        self.get_calls = []
        self.execute_calls = 0

    async def get(self, model, key):
        self.get_calls.append((model, key))
        if self.failure is not None:
            raise self.failure
        return self.rows.get(model)

    async def execute(self, statement):
        self.execute_calls += 1
        if self.failure is not None:
            raise self.failure
        return _ScalarResult(self.tool_call)


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


class _SessionFactory:
    def __init__(self, session) -> None:
        self.session = session
        self.calls = 0
        self.contexts = []

    def __call__(self):
        self.calls += 1
        context = _SessionContext(self.session)
        self.contexts.append(context)
        return context


def _service(*, result_overrides=None, failure=None):
    result, execution, session, invocation, tool_call = _evidence(
        result_overrides=result_overrides,
    )
    fake_session = _FakeSession(
        result=result,
        execution=execution,
        canonical_session=session,
        invocation=invocation,
        tool_call=tool_call,
        failure=failure,
    )
    factory = _SessionFactory(fake_session)
    return DurableToolResponsePayloadSourceAuthority(factory), factory


@pytest.mark.asyncio
async def test_b1_reconstructs_canonical_snapshot_and_discards_caller_provenance():
    authority, factory = _service()
    caller_one = _source_ref(
        created_at=NOW,
        extra_metadata={"caller_only": "one"},
    )
    caller_two = _source_ref(
        created_at=NOW + timedelta(hours=3),
        extra_metadata={"caller_only": "two", "nested": {"ignored": True}},
    )

    proof_one = await authority.reprove_for_memory_promotion(
        source_ref=caller_one,
        owner_user_id=OWNER,
    )
    proof_two = await authority.reprove_for_memory_promotion(
        source_ref=caller_two,
        owner_user_id=OWNER,
    )

    assert SourcePromotionAuthorityPort in DurableToolResponsePayloadSourceAuthority.__mro__
    assert proof_one.source_ref_snapshot is not caller_one
    assert proof_two.source_ref_snapshot is not caller_two
    assert proof_one.source_ref_snapshot == proof_two.source_ref_snapshot
    assert proof_one.source_ref_snapshot.source_created_at is None
    assert proof_one.source_ref_snapshot.task_id is None
    assert proof_one.source_ref_snapshot.branch_id is None
    assert dict(proof_one.source_ref_snapshot.metadata) == {
        "source_result_id": "result-1"
    }
    assert proof_one.proof_receipt_id == proof_two.proof_receipt_id
    assert proof_one.authority_state_token == proof_two.authority_state_token
    assert factory.calls == 2

    intent_one = MemoryPromotionIntent(
        owner_user_id=OWNER,
        source_ref_snapshot=proof_one.source_ref_snapshot,
        source_proof=proof_one,
        content_digest="content-digest",
        metadata={"kind": "same"},
        memory_schema_version=1,
    )
    intent_two = MemoryPromotionIntent(
        owner_user_id=OWNER,
        source_ref_snapshot=proof_two.source_ref_snapshot,
        source_proof=proof_two,
        content_digest="content-digest",
        metadata={"kind": "same"},
        memory_schema_version=1,
    )
    assert intent_one.model_dump(mode="json") == intent_two.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"commit_state": "PROVISIONAL"},
        {"success": False},
        {"error_code": "TOOL_FAILED"},
        {"error_message": "failed"},
        {"retryable": True},
    ],
)
async def test_b1_requires_exact_successful_committed_result(overrides):
    authority, _ = _service(result_overrides=overrides)

    with pytest.raises(ToolResponsePayloadSourceRejectedError):
        await authority.reprove_for_memory_promotion(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )


@pytest.mark.asyncio
async def test_b1_fails_closed_on_owner_or_lineage_mismatch():
    result, execution, session, invocation, tool_call = _evidence()
    invocation.owner_user_id = "foreign-user"
    fake_session = _FakeSession(
        result=result,
        execution=execution,
        canonical_session=session,
        invocation=invocation,
        tool_call=tool_call,
    )
    authority = DurableToolResponsePayloadSourceAuthority(
        _SessionFactory(fake_session)
    )

    with pytest.raises(ToolResponsePayloadSourceRejectedError):
        await authority.reprove_for_memory_promotion(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )


@pytest.mark.asyncio
async def test_b1_maps_sqlalchemy_read_failure_to_service_local_unavailable_error():
    authority, _ = _service(failure=SQLAlchemyError("database unavailable"))

    with pytest.raises(ToolResponsePayloadSourceUnavailableError):
        await authority.reprove_for_memory_promotion(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )


@pytest.mark.asyncio
async def test_b1_asyncio_cancellation_propagates_unchanged():
    authority, _ = _service(failure=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await authority.reprove_for_memory_promotion(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )
