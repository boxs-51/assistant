from __future__ import annotations

import asyncio
import hashlib
from types import MappingProxyType, SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

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
    TrustedToolResponsePromotionMaterial,
)


OWNER = "user-ctx-f5-3i-b2"
OUTPUT = {"kind": "tool-result", "nested": [1, {"ok": True}]}


def _payload_id() -> str:
    digest = hashlib.sha256(canonical_payload_bytes(OUTPUT)).hexdigest()
    return tool_response_payload_id(
        source_result_id="result-1",
        invocation_id="invocation-1",
        execution_id="execution-1",
        tool_call_id="tool-call-1",
        logical_capability_id="tool.echo",
        content_digest=digest,
        payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
    )


def _evidence(*, result_overrides=None):
    result_values = {
        "id": "result-1",
        "execution_id": "execution-1",
        "iteration_id": "iteration-1",
        "invocation_id": "invocation-1",
        "tool_call_id": "tool-call-1",
        "capability_id": "tool.echo",
        "success": True,
        "output": {
            "kind": "tool-result",
            "nested": [1, {"ok": True}],
        },
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "commit_state": COMMITTED_RESULT_STATE,
    }
    result_values.update(result_overrides or {})
    return (
        SimpleNamespace(**result_values),
        SimpleNamespace(id="execution-1", session_id="session-1"),
        SimpleNamespace(id="session-1", user_id=OWNER),
        SimpleNamespace(
            invocation_id="invocation-1",
            execution_id="execution-1",
            session_id="session-1",
            owner_user_id=OWNER,
            tool_call_id="tool-call-1",
            capability_id="tool.echo",
        ),
        SimpleNamespace(
            execution_id="execution-1",
            iteration_id="iteration-1",
            tool_call_id="tool-call-1",
            invocation_id="invocation-1",
            capability_id="tool.echo",
        ),
    )


def _source_ref(*, extra_metadata=None):
    metadata = {"source_result_id": "result-1"}
    metadata.update(extra_metadata or {})
    return create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=_payload_id(),
        owner_user_id=OWNER,
        session_id="session-1",
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

    async def get(self, model, key):
        if self.failure is not None:
            raise self.failure
        return self.rows.get(model)

    async def execute(self, statement):
        if self.failure is not None:
            raise self.failure
        return _ScalarResult(self.tool_call)


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, session) -> None:
        self.session = session
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return _SessionContext(self.session)


def _service(*, result_overrides=None, failure=None):
    result, execution, session, invocation, tool_call = _evidence(
        result_overrides=result_overrides,
    )
    factory = _SessionFactory(
        _FakeSession(
            result=result,
            execution=execution,
            canonical_session=session,
            invocation=invocation,
            tool_call=tool_call,
            failure=failure,
        )
    )
    return DurableToolResponsePayloadSourceAuthority(factory), factory, result


@pytest.mark.asyncio
async def test_b2_material_matches_legacy_proof_and_is_deep_frozen_detached():
    authority, factory, result = _service()
    source_ref = _source_ref(extra_metadata={"caller_only": {"ignored": True}})

    material = await authority.read_trusted_promotion_material(
        source_ref=source_ref,
        owner_user_id=OWNER,
    )
    legacy_proof = await authority.reprove_for_memory_promotion(
        source_ref=source_ref,
        owner_user_id=OWNER,
    )

    assert isinstance(material, TrustedToolResponsePromotionMaterial)
    assert material.source_proof == legacy_proof
    assert factory.calls == 2

    expected_digest = hashlib.sha256(canonical_payload_bytes(OUTPUT)).hexdigest()
    assert material.content_digest == expected_digest
    assert material.source_proof.source_ref_snapshot.authority_id == _payload_id()
    assert dict(material.source_proof.source_ref_snapshot.metadata) == {
        "source_result_id": "result-1"
    }

    assert isinstance(material.content_snapshot, MappingProxyType)
    assert isinstance(material.content_snapshot["nested"], tuple)
    assert isinstance(material.content_snapshot["nested"][1], MappingProxyType)
    with pytest.raises(TypeError):
        material.content_snapshot["kind"] = "mutated"

    result.output["kind"] = "changed-after-read"
    result.output["nested"][1]["ok"] = False
    assert material.content_snapshot["kind"] == "tool-result"
    assert material.content_snapshot["nested"][1]["ok"] is True


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
async def test_b2_requires_exact_successful_committed_source(overrides):
    authority, _, _ = _service(result_overrides=overrides)

    with pytest.raises(ToolResponsePayloadSourceRejectedError):
        await authority.read_trusted_promotion_material(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )


@pytest.mark.asyncio
async def test_b2_maps_sqlalchemy_failure_to_bounded_unavailable_error():
    authority, _, _ = _service(failure=SQLAlchemyError("database unavailable"))

    with pytest.raises(ToolResponsePayloadSourceUnavailableError):
        await authority.read_trusted_promotion_material(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )


@pytest.mark.asyncio
async def test_b2_cancellation_propagates_unchanged():
    authority, _, _ = _service(failure=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await authority.read_trusted_promotion_material(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )
