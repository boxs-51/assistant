from __future__ import annotations

import hashlib
import pytest
from sqlalchemy import delete, func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
    ToolResponsePayloadSourceRejectedError,
)
import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401 - load mapped tables


OWNER = "user-ctx-f5-3i-b2-integration"
SESSION_ID = "session-b2"
EXECUTION_ID = "execution-b2"
ITERATION_ID = "iteration-b2"
INVOCATION_ID = "invocation-b2"
TOOL_CALL_ID = "tool-call-b2"
TOOL_CALL_ROW_ID = "tool-call-row-b2"
RESULT_ID = "result-b2"
CAPABILITY_ID = "tool.b2"
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
        metadata={
            "source_result_id": RESULT_ID,
            "caller_only": {"ignored": True},
        },
    )


async def _database(tmp_path):
    database_path = tmp_path / "ctx-f5-3i-b2.sqlite3"
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
                agent_id="agent-b2",
                correlation_id="corr-b2",
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


def _row_state(row) -> dict:
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in sa_inspect(type(row)).column_attrs
    }


async def _snapshot(sessions) -> dict:
    async with sessions() as session:
        rows = {
            "session": await session.get(Session, SESSION_ID),
            "execution": await session.get(AgentExecutionRecord, EXECUTION_ID),
            "iteration": await session.get(AgentIterationRecord, ITERATION_ID),
            "invocation": await session.get(
                CapabilityInvocationRecord,
                INVOCATION_ID,
            ),
            "tool_call": await session.get(
                AgentToolCallRecord,
                TOOL_CALL_ROW_ID,
            ),
            "result": await session.get(AgentToolResultRecord, RESULT_ID),
        }
        return {
            name: None if row is None else _row_state(row)
            for name, row in rows.items()
        }


async def _counts(sessions) -> dict[str, int]:
    models = {
        "session": Session,
        "execution": AgentExecutionRecord,
        "iteration": AgentIterationRecord,
        "invocation": CapabilityInvocationRecord,
        "tool_call": AgentToolCallRecord,
        "result": AgentToolResultRecord,
    }
    async with sessions() as session:
        return {
            name: int(
                (
                    await session.execute(
                        select(func.count()).select_from(model)
                    )
                ).scalar_one()
            )
            for name, model in models.items()
        }


async def _delete_all_source_rows(sessions) -> None:
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
async def test_b2_real_sql_material_is_read_only_detached_and_b1_compatible(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_lineage(sessions)
        before = await _snapshot(sessions)
        before_counts = await _counts(sessions)

        authority = DurableToolResponsePayloadSourceAuthority(sessions)
        source_ref = _source_ref()

        material = await authority.read_trusted_promotion_material(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )
        legacy_proof = await authority.reprove_for_memory_promotion(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )

        expected_digest = hashlib.sha256(
            canonical_payload_bytes(OUTPUT)
        ).hexdigest()
        assert material.source_proof == legacy_proof
        assert material.content_digest == expected_digest
        assert material.source_proof.source_ref_snapshot.authority_id == _payload_id()
        assert dict(material.source_proof.source_ref_snapshot.metadata) == {
            "source_result_id": RESULT_ID
        }
        snapshot = material.content_snapshot
        assert isinstance(snapshot, dict)
        assert isinstance(snapshot["nested"], dict)
        assert snapshot["nested"]["values"] == [1, 2, 3]
        assert hashlib.sha256(canonical_payload_bytes(snapshot)).hexdigest() == material.content_digest

        snapshot["message"] = "caller-mutated"
        snapshot["nested"]["values"].append(99)
        fresh_snapshot = material.content_snapshot
        assert fresh_snapshot["message"] == "durable-result"
        assert fresh_snapshot["nested"]["values"] == [1, 2, 3]
        assert hashlib.sha256(canonical_payload_bytes(fresh_snapshot)).hexdigest() == material.content_digest

        assert await _counts(sessions) == before_counts
        assert await _snapshot(sessions) == before

        await _delete_all_source_rows(sessions)

        post_delete_snapshot = material.content_snapshot
        assert post_delete_snapshot["message"] == "durable-result"
        assert post_delete_snapshot["nested"]["values"] == [1, 2, 3]
        assert hashlib.sha256(canonical_payload_bytes(post_delete_snapshot)).hexdigest() == material.content_digest

        with pytest.raises(ToolResponsePayloadSourceRejectedError):
            await authority.read_trusted_promotion_material(
                source_ref=source_ref,
                owner_user_id=OWNER,
            )
    finally:
        await engine.dispose()
