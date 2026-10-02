from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import delete, inspect as sa_inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import MEMORY_SCHEMA_VERSION
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
from se.src.infrastructure.storage.models.sql.agent.iteration import (
    AgentIterationRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_call import (
    AgentToolCallRecord,
)
from se.src.infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.models.sql.chat_data.session import Session
from se.src.infrastructure.storage.models.sql.promotion_reservation import (
    PromotionReservationRow,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
)
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
    ToolResponsePayloadSourceRejectedError,
)
import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401 - load mapped tables


OWNER = "user-ctx-f5-3i-b3-integration"
SESSION_ID = "session-b3"
EXECUTION_ID = "execution-b3"
ITERATION_ID = "iteration-b3"
INVOCATION_ID = "invocation-b3"
TOOL_CALL_ID = "tool-call-b3"
TOOL_CALL_ROW_ID = "tool-call-row-b3"
RESULT_ID = "result-b3"
CAPABILITY_ID = "tool.b3"
OUTPUT = {
    "message": "durable-result",
    "nested": {"values": [1, 2, 3]},
    "asset_id": "opaque-only",
}


def _payload_id() -> str:
    digest = hashlib.sha256(canonical_payload_bytes(OUTPUT)).hexdigest()
    return tool_response_payload_id(
        source_result_id=RESULT_ID,
        invocation_id=INVOCATION_ID,
        execution_id=EXECUTION_ID,
        tool_call_id=TOOL_CALL_ID,
        logical_capability_id=CAPABILITY_ID,
        content_digest=digest,
        payload_schema_version=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
    )


def _source_ref():
    return create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=_payload_id(),
        owner_user_id=OWNER,
        session_id=SESSION_ID,
        source_state=COMMITTED_RESULT_STATE,
        metadata={"source_result_id": RESULT_ID},
    )


async def _database(tmp_path):
    database_path = tmp_path / "ctx-f5-3i-b3.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_lineage(sessions) -> None:
    async with sessions() as session:
        session.add(
            Session(
                id=SESSION_ID,
                user_id=OWNER,
                project_id=None,
                organization_id=None,
                status="active",
                metadata_json={},
            )
        )
        session.add(
            AgentExecutionRecord(
                id=EXECUTION_ID,
                session_id=SESSION_ID,
                agent_id="agent-b3",
                correlation_id="corr-b3",
                state="COMPLETED",
                revision=1,
                request={},
                result={"ok": True},
            )
        )
        session.add(
            AgentIterationRecord(
                id=ITERATION_ID,
                execution_id=EXECUTION_ID,
                iteration=1,
                state="COMPLETED",
                tool_call_ids=[TOOL_CALL_ID],
            )
        )
        session.add(
            CapabilityInvocationRecord(
                invocation_id=INVOCATION_ID,
                capability_id=CAPABILITY_ID,
                kind="TOOL",
                execution_mode="LOCAL",
                idempotency="IDEMPOTENT",
                owner_user_id=OWNER,
                state="COMPLETED",
                session_id=SESSION_ID,
                execution_id=EXECUTION_ID,
                tool_call_id=TOOL_CALL_ID,
                attempt=1,
                max_attempts=1,
                arguments={},
                output=OUTPUT,
            )
        )
        session.add(
            AgentToolCallRecord(
                id=TOOL_CALL_ROW_ID,
                execution_id=EXECUTION_ID,
                iteration_id=ITERATION_ID,
                invocation_id=INVOCATION_ID,
                tool_call_id=TOOL_CALL_ID,
                capability_id=CAPABILITY_ID,
                arguments={"value": 7},
                status="COMPLETED",
                extra_metadata={},
            )
        )
        session.add(
            AgentToolResultRecord(
                id=RESULT_ID,
                execution_id=EXECUTION_ID,
                iteration_id=ITERATION_ID,
                tool_call_id=TOOL_CALL_ID,
                invocation_id=INVOCATION_ID,
                capability_id=CAPABILITY_ID,
                success=True,
                output=OUTPUT,
                error_code=None,
                error_message=None,
                retryable=False,
                extra_metadata={},
                commit_state=COMMITTED_RESULT_STATE,
                attempt=1,
            )
        )
        await session.commit()


async def _delete_source_rows(sessions) -> None:
    models = (
        AgentToolResultRecord,
        AgentToolCallRecord,
        CapabilityInvocationRecord,
        AgentIterationRecord,
        AgentExecutionRecord,
        Session,
    )
    async with sessions() as session:
        for model in models:
            await session.execute(delete(model))
        await session.commit()


@pytest.mark.asyncio
async def test_b3_real_sql_reserves_exact_intent_without_persisting_content(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_lineage(sessions)

        source_authority = DurableToolResponsePayloadSourceAuthority(sessions)
        issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: "authority-b3",
        )
        service = DurableToolResponsePayloadPromotionOrchestration(
            source_authority,
            issuer,
        )
        source_ref = _source_ref()

        result = await service.reserve(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )

        reservation = result.reservation
        intent = reservation.intent
        expected_digest = hashlib.sha256(
            canonical_payload_bytes(OUTPUT)
        ).hexdigest()

        assert reservation.promotion_authority_id == "authority-b3"
        assert intent.owner_user_id == OWNER
        assert (
            intent.source_ref_snapshot.context_source_id
            == source_ref.context_source_id
        )
        assert (
            intent.source_proof.source_ref_snapshot
            == intent.source_ref_snapshot
        )
        assert intent.content_digest == expected_digest == result.content_digest
        assert dict(intent.metadata) == {}
        assert intent.memory_schema_version == MEMORY_SCHEMA_VERSION
        assert result.content_snapshot == OUTPUT

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            durable = await repository.get("authority-b3")
            row = await session.get(PromotionReservationRow, "authority-b3")

            assert durable is not None
            assert durable.state is DurablePromotionReservationState.ISSUED
            assert durable.intent == intent
            assert durable.intent_digest
            assert (
                durable.source_context_source_id
                == source_ref.context_source_id
            )
            assert (
                durable.proof_receipt_id
                == intent.source_proof.proof_receipt_id
            )
            assert (
                durable.authority_state_token
                == intent.source_proof.authority_state_token
            )

            assert row is not None
            assert row.intent_json["content_digest"] == expected_digest
            assert "content" not in row.intent_json
            assert "content_snapshot" not in row.intent_json
            columns = set(sa_inspect(PromotionReservationRow).columns.keys())
            assert "content" not in columns
            assert "content_snapshot" not in columns

        held_snapshot = result.content_snapshot
        await _delete_source_rows(sessions)

        assert held_snapshot == OUTPUT
        held_snapshot["message"] = "mutated"
        assert result.content_snapshot["message"] == "durable-result"

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            durable_after_source_delete = await repository.get("authority-b3")
            assert durable_after_source_delete is not None
            assert (
                durable_after_source_delete.state
                is DurablePromotionReservationState.ISSUED
            )
            assert durable_after_source_delete.intent == intent

        with pytest.raises(ToolResponsePayloadSourceRejectedError):
            await service.reserve(
                source_ref=source_ref,
                owner_user_id=OWNER,
            )
    finally:
        await engine.dispose()
