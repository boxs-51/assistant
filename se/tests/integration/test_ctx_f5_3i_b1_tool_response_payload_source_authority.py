from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory_promotion import MemoryPromotionIntent
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


NOW = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
OWNER = "user-ctx-f5-3i-b1-integration"
FOREIGN_OWNER = "user-foreign"

SESSION_ID = "session-integration"
EXECUTION_ID = "execution-integration"
ITERATION_ID = "iteration-integration"
INVOCATION_ID = "invocation-integration"
TOOL_CALL_ID = "tool-call-integration"
TOOL_CALL_ROW_ID = "tool-call-row-integration"
RESULT_ID = "result-integration"
CAPABILITY_ID = "tool.integration"

OUTPUT = {
    "message": "durable-result",
    "asset_id": "opaque-only",
    "nested": {"value": 7},
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


def _caller_ref(
    *,
    source_created_at: datetime = NOW,
    extra_metadata: dict | None = None,
):
    metadata = {"source_result_id": RESULT_ID}
    metadata.update(extra_metadata or {})
    return create_context_source_ref(
        source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
        authority_id=_payload_id(),
        owner_user_id=OWNER,
        session_id=SESSION_ID,
        source_created_at=source_created_at,
        source_state=COMMITTED_RESULT_STATE,
        metadata=metadata,
    )


async def _database(tmp_path, name: str):
    database_path = tmp_path / f"{name}.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_lineage(
    sessions,
    *,
    session_owner: str = OWNER,
    retryable: bool = False,
) -> None:
    async with sessions() as session:
        session.add(
            Session(
                id=SESSION_ID,
                user_id=session_owner,
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
                agent_id="agent-integration",
                correlation_id="corr-integration",
                state="COMPLETED",
                revision=3,
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
                owner_user_id=session_owner,
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
                retryable=retryable,
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


async def _delete_one(sessions, target: str) -> None:
    clauses = {
        "result": (
            AgentToolResultRecord,
            AgentToolResultRecord.id == RESULT_ID,
        ),
        "execution": (
            AgentExecutionRecord,
            AgentExecutionRecord.id == EXECUTION_ID,
        ),
        "session": (
            Session,
            Session.id == SESSION_ID,
        ),
        "invocation": (
            CapabilityInvocationRecord,
            CapabilityInvocationRecord.invocation_id == INVOCATION_ID,
        ),
        "tool_call": (
            AgentToolCallRecord,
            AgentToolCallRecord.id == TOOL_CALL_ROW_ID,
        ),
    }
    model, predicate = clauses[target]
    async with sessions() as session:
        await session.execute(delete(model).where(predicate))
        await session.commit()


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
async def test_b1_real_sql_reconstructs_stable_proof_without_writes_or_pins(
    tmp_path,
):
    engine, sessions = await _database(tmp_path, "success")
    try:
        await _seed_lineage(sessions)
        before = await _snapshot(sessions)
        before_counts = await _counts(sessions)

        authority = DurableToolResponsePayloadSourceAuthority(sessions)
        first_caller = _caller_ref(
            source_created_at=NOW,
            extra_metadata={"caller_only": "first"},
        )
        second_caller = _caller_ref(
            source_created_at=NOW + timedelta(days=1),
            extra_metadata={
                "caller_only": "second",
                "nested": {"ignored": True},
            },
        )

        first = await authority.reprove_for_memory_promotion(
            source_ref=first_caller,
            owner_user_id=OWNER,
        )
        second = await authority.reprove_for_memory_promotion(
            source_ref=second_caller,
            owner_user_id=OWNER,
        )

        assert first.source_ref_snapshot is not first_caller
        assert second.source_ref_snapshot is not second_caller
        assert first.source_ref_snapshot == second.source_ref_snapshot
        assert first.source_ref_snapshot.source_created_at is None
        assert first.source_ref_snapshot.session_id == SESSION_ID
        assert first.source_ref_snapshot.task_id is None
        assert first.source_ref_snapshot.branch_id is None
        assert first.source_ref_snapshot.source_state == COMMITTED_RESULT_STATE
        assert dict(first.source_ref_snapshot.metadata) == {
            "source_result_id": RESULT_ID
        }
        assert first.proof_receipt_id == second.proof_receipt_id
        assert first.authority_state_token == second.authority_state_token

        intent_one = MemoryPromotionIntent(
            owner_user_id=OWNER,
            source_ref_snapshot=first.source_ref_snapshot,
            source_proof=first,
            content_digest="memory-content",
            metadata={"kind": "integration"},
            memory_schema_version=1,
        )
        intent_two = MemoryPromotionIntent(
            owner_user_id=OWNER,
            source_ref_snapshot=second.source_ref_snapshot,
            source_proof=second,
            content_digest="memory-content",
            metadata={"kind": "integration"},
            memory_schema_version=1,
        )
        assert (
            intent_one.model_dump(mode="json")
            == intent_two.model_dump(mode="json")
        )

        assert await _counts(sessions) == before_counts
        assert await _snapshot(sessions) == before

        await _delete_all_source_rows(sessions)
        assert await _counts(sessions) == {
            "session": 0,
            "execution": 0,
            "iteration": 0,
            "invocation": 0,
            "tool_call": 0,
            "result": 0,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_b1_real_sql_foreign_canonical_session_owner_fails_closed(tmp_path):
    engine, sessions = await _database(tmp_path, "foreign-owner")
    try:
        await _seed_lineage(sessions, session_owner=FOREIGN_OWNER)
        authority = DurableToolResponsePayloadSourceAuthority(sessions)

        with pytest.raises(ToolResponsePayloadSourceRejectedError):
            await authority.reprove_for_memory_promotion(
                source_ref=_caller_ref(),
                owner_user_id=OWNER,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_target",
    ["result", "execution", "session", "invocation", "tool_call"],
)
async def test_b1_real_sql_each_missing_or_gc_source_row_fails_closed(
    tmp_path,
    missing_target,
):
    engine, sessions = await _database(tmp_path, f"missing-{missing_target}")
    try:
        await _seed_lineage(sessions)
        await _delete_one(sessions, missing_target)
        authority = DurableToolResponsePayloadSourceAuthority(sessions)

        with pytest.raises(ToolResponsePayloadSourceRejectedError):
            await authority.reprove_for_memory_promotion(
                source_ref=_caller_ref(),
                owner_user_id=OWNER,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_b1_real_sql_success_true_retryable_true_is_ineligible(tmp_path):
    engine, sessions = await _database(tmp_path, "retryable-malformed")
    try:
        await _seed_lineage(sessions, retryable=True)
        async with sessions() as session:
            durable = await session.get(AgentToolResultRecord, RESULT_ID)
            assert durable is not None
            assert durable.success is True
            assert durable.retryable is True
            assert durable.commit_state == COMMITTED_RESULT_STATE

        authority = DurableToolResponsePayloadSourceAuthority(sessions)
        with pytest.raises(ToolResponsePayloadSourceRejectedError):
            await authority.reprove_for_memory_promotion(
                source_ref=_caller_ref(),
                owner_user_id=OWNER,
            )
    finally:
        await engine.dispose()
