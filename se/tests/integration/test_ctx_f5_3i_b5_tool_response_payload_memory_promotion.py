from __future__ import annotations

import asyncio
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
from se.src.infrastructure.storage.models.sql.memory import MemoryRecordRow
from se.src.infrastructure.storage.models.sql.promotion_reservation import (
    PromotionReservationRow,
)
from se.src.infrastructure.storage.repositories.promotion_reservation import (
    DurablePromotionReservationRepository,
    DurablePromotionReservationState,
    PromotionReservationAlreadyConsumedError,
)
from se.src.infrastructure.storage.services.memory_promotion_admission import (
    DurableMemoryPromotionAdmission,
)
from se.src.infrastructure.storage.services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)
from se.src.infrastructure.storage.services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
)
from se.src.infrastructure.storage.services.tool_response_payload_memory_promotion import (
    DurableToolResponsePayloadMemoryPromotion,
)
from se.src.infrastructure.storage.services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)
from se.src.infrastructure.storage.services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
)

OWNER = "user-ctx-f5-3i-b5"
SESSION_ID = "session-b5"
EXECUTION_ID = "execution-b5"
ITERATION_ID = "iteration-b5"
INVOCATION_ID = "invocation-b5"
TOOL_CALL_ID = "tool-call-b5"
RESULT_ID = "result-b5"
CAPABILITY_ID = "tool.b5"
OUTPUT = {"message": "durable-memory", "nested": [1, {"ok": True}]}


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


async def _database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
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
                agent_id="agent-b5",
                correlation_id="corr-b5",
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
                id="tool-call-row-b5",
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


def _service(sessions, authority_id_factory, *, source=None):
    trusted_source = source or DurableToolResponsePayloadSourceAuthority(sessions)
    issuer = DurablePromotionReservationIssuer(
        sessions,
        authority_id_factory=authority_id_factory,
    )
    recovery = DurablePromotionReservationRecovery(sessions)
    orchestration = DurableToolResponsePayloadPromotionOrchestration(
        trusted_source,  # type: ignore[arg-type]
        issuer,
        recovery,
    )
    admission = DurableMemoryPromotionAdmission(sessions)
    return DurableToolResponsePayloadMemoryPromotion(
        orchestration,
        admission,
    )


async def _row_count(sessions, row_type) -> int:
    async with sessions() as session:
        result = await session.execute(select(func.count()).select_from(row_type))
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_b5_first_promotion_consumes_reservation_and_fresh_retry_replays_memory(
    tmp_path,
):
    engine, sessions = await _database(tmp_path / "ctx-f5-3i-b5-replay.sqlite3")
    try:
        await _seed_lineage(sessions)
        source_first = _CountingSource(
            DurableToolResponsePayloadSourceAuthority(sessions)
        )
        candidate_ids = iter(("authority-b5",))
        first_service = _service(
            sessions,
            lambda: next(candidate_ids),
            source=source_first,
        )

        first = await first_service.promote(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )

        source_retry = _CountingSource(
            DurableToolResponsePayloadSourceAuthority(sessions)
        )

        def unexpected_authority_id():
            raise AssertionError("CONSUMED retry must not mint a new authority")

        retry_service = _service(
            sessions,
            unexpected_authority_id,
            source=source_retry,
        )
        replay = await retry_service.promote(
            source_ref=_source_ref(),
            owner_user_id=OWNER,
        )

        assert first.memory_id == replay.memory_id
        assert first.created_at == replay.created_at
        assert first.content["message"] == "durable-memory"
        assert source_first.calls == 1
        assert source_retry.calls == 1
        assert await _row_count(sessions, PromotionReservationRow) == 1
        assert await _row_count(sessions, MemoryRecordRow) == 1

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                "authority-b5"
            )
            assert durable is not None
            assert durable.state is DurablePromotionReservationState.CONSUMED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_b5_concurrent_same_source_converges_to_one_memory_identity(tmp_path):
    engine, sessions = await _database(tmp_path / "ctx-f5-3i-b5-race.sqlite3")
    try:
        await _seed_lineage(sessions)
        candidate_ids = iter(("authority-b5-race-a", "authority-b5-race-b"))
        service = _service(sessions, lambda: next(candidate_ids))
        source_ref = _source_ref()

        outcomes = await asyncio.gather(
            service.promote(source_ref=source_ref, owner_user_id=OWNER),
            service.promote(source_ref=source_ref, owner_user_id=OWNER),
            return_exceptions=True,
        )

        successes = [
            outcome for outcome in outcomes if not isinstance(outcome, BaseException)
        ]
        failures = [
            outcome for outcome in outcomes if isinstance(outcome, BaseException)
        ]

        assert successes
        assert len(failures) <= 1
        assert all(
            type(failure) is PromotionReservationAlreadyConsumedError
            for failure in failures
        )

        canonical = successes[0]
        assert all(
            success.memory_id == canonical.memory_id
            and success.promotion_authority_id == canonical.promotion_authority_id
            for success in successes[1:]
        )
        assert await _row_count(sessions, PromotionReservationRow) == 1
        assert await _row_count(sessions, MemoryRecordRow) == 1

        async with sessions() as session:
            durable = await DurablePromotionReservationRepository(session).get(
                canonical.promotion_authority_id
            )
            assert durable is not None
            assert durable.state is DurablePromotionReservationState.CONSUMED

        source_retry = _CountingSource(
            DurableToolResponsePayloadSourceAuthority(sessions)
        )

        def unexpected_authority_id():
            raise AssertionError("CONSUMED retry must not mint a new authority")

        replay = await _service(
            sessions,
            unexpected_authority_id,
            source=source_retry,
        ).promote(
            source_ref=source_ref,
            owner_user_id=OWNER,
        )

        assert replay.memory_id == canonical.memory_id
        assert replay.promotion_authority_id == canonical.promotion_authority_id
        assert replay.created_at == canonical.created_at
        assert source_retry.calls == 1
        assert await _row_count(sessions, PromotionReservationRow) == 1
        assert await _row_count(sessions, MemoryRecordRow) == 1
    finally:
        await engine.dispose()
