from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
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
from se.src.infrastructure.storage.models.sql.agent.tool_call import AgentToolCallRecord
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
from se.src.infrastructure.storage.services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
)

OWNER = "user-ctx-f5-3i-b4-integration"
SESSION_ID = "session-b4"
EXECUTION_ID = "execution-b4"
ITERATION_ID = "iteration-b4"
INVOCATION_ID = "invocation-b4"
TOOL_CALL_ID = "tool-call-b4"
RESULT_ID = "result-b4"
CAPABILITY_ID = "tool.b4"
OUTPUT = {"message": "durable-result", "nested": [1, {"ok": True}]}


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
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'ctx-f5-3i-b4.sqlite3').as_posix()}"
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
                agent_id="agent-b4",
                correlation_id="corr-b4",
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
                id="tool-call-row-b4",
                execution_id=EXECUTION_ID,
                iteration_id=ITERATION_ID,
                invocation_id=INVOCATION_ID,
                tool_call_id=TOOL_CALL_ID,
                capability_id=CAPABILITY_ID,
                arguments={},
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


class _CountingSource:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    async def read_trusted_promotion_material(self, **kwargs):
        self.calls += 1
        return await self.delegate.read_trusted_promotion_material(**kwargs)


async def _row_count(sessions) -> int:
    async with sessions() as session:
        result = await session.execute(
            select(func.count()).select_from(PromotionReservationRow)
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_real_sql_recovers_issued_and_consumed_identity_without_new_row(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_lineage(sessions)
        source = _CountingSource(DurableToolResponsePayloadSourceAuthority(sessions))
        candidate_ids = iter(("authority-b4",))
        issuer = DurablePromotionReservationIssuer(
            sessions,
            authority_id_factory=lambda: next(candidate_ids),
        )
        recovery = DurablePromotionReservationRecovery(sessions)
        service = DurableToolResponsePayloadPromotionOrchestration(
            source,  # type: ignore[arg-type]
            issuer,
            recovery,
        )
        source_ref = _source_ref()

        first = await service.reserve(source_ref=source_ref, owner_user_id=OWNER)
        issued_replay = await service.reserve(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )

        assert first.reservation.promotion_authority_id == "authority-b4"
        assert issued_replay.reservation == first.reservation
        assert await _row_count(sessions) == 1

        async with sessions() as session:
            repository = DurablePromotionReservationRepository(session)
            consumed = await repository.mark_consumed("authority-b4")
            await session.commit()
            assert consumed.state is DurablePromotionReservationState.CONSUMED

        consumed_replay = await service.reserve(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )

        assert consumed_replay.reservation == first.reservation
        assert consumed_replay.content_snapshot == OUTPUT
        assert await _row_count(sessions) == 1
        assert source.calls == 3
    finally:
        await engine.dispose()
