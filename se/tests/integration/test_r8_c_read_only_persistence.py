from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentToolResultRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.models.sql.capability import (
    CapabilityInvocationRecord,
)
from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from se.src.infrastructure.storage.transcript_representation import (
    canonical_json_bytes,
    canonical_transcript_messages,
    logical_transcript_fingerprint,
    transcript_chunk_id,
    transcript_payload_root_ref,
    transcript_representation_ref,
)
from se.src.runtimes.agent.checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
)
from se.src.runtimes.agent.fork_planning import (
    ForkPlanRejected,
    _load_fork_safe_transcript_in_uow,
)
from se.src.runtimes.agent.retry_planning import (
    RetryPlanRejected,
    _load_retry_safe_checkpoint_transcript,
    _load_retry_safe_checkpoint_transcript_in_uow,
)
from se.src.runtimes.agent.persistence import (
    DurableAgentStore,
    ExecutionConflictError,
)


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None
        self.session = None
        self.agents = None
        self.capability_invocations = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.agents = AgentRepository(self.session)
        self.capability_invocations = CapabilityInvocationRepository(
            self.session
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'r8_c_read_only.sqlite').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


def _execution():
    return AgentExecutionRecord(
        id="exec-r8-c",
        session_id="session-r8-c",
        agent_id="agent-r8-c",
        task_id=None,
        branch_id=None,
        correlation_id="corr-r8-c",
        state="WAITING",
        revision=7,
        current_checkpoint_id="cp-r8-c",
        request={},
    )


def _iteration():
    return AgentIterationRecord(
        id="iter-r8-c",
        execution_id="exec-r8-c",
        iteration=4,
        state="WAITING",
        tool_call_ids=["call-b", "call-a"],
    )


def _checkpoint():
    return AgentExecutionCheckpointRecord(
        checkpoint_id="cp-r8-c",
        execution_id="exec-r8-c",
        execution_revision=7,
        session_id="session-r8-c",
        task_id=None,
        branch_id=None,
        iteration=4,
        wait_reason="RESOURCE",
        transcript_snapshot=[
            {
                "role": "user",
                "content": "base",
                "tool_calls": [],
                "name": None,
                "tool_call_id": None,
                "metadata": {},
            }
        ],
        metadata_json={},
    )


def _result(tool_call_id: str, output: str, *, committed=True):
    suffix = tool_call_id.removeprefix("call-")
    return AgentToolResultRecord(
        id=f"result-{tool_call_id}",
        execution_id="exec-r8-c",
        iteration_id="iter-r8-c",
        tool_call_id=tool_call_id,
        invocation_id=suffix,
        capability_id=f"tool.{suffix}",
        success=True,
        output={"value": output},
        retryable=False,
        commit_state="COMMITTED" if committed else "PROVISIONAL",
        attempt=1,
    )


def _invocation(invocation_id: str, *, execution_id="exec-r8-c"):
    return CapabilityInvocationRecord(
        invocation_id=invocation_id,
        capability_id=f"tool.{invocation_id}",
        capability_version="1",
        kind="TOOL",
        execution_mode="SYNC",
        idempotency="IDEMPOTENT",
        request_fingerprint=f"fp-{invocation_id}",
        remote_outcome_state="TERMINAL_COMMITTED",
        implementation_id=f"impl-{invocation_id}",
        driver_kind="REMOTE_CLIENT",
        state="COMPLETED",
        execution_id=execution_id,
        tool_call_id=f"call-{invocation_id}",
        attempt=1,
        max_attempts=1,
        arguments={},
        output={"value": invocation_id.upper()},
        revision=3,
    )


async def _seed_source(sessions, *, provisional=False, pending=False):
    async with sessions() as session:
        session.add(_execution())
        session.add(_iteration())
        session.add(_checkpoint())
        session.add(_result("call-a", "A", committed=not provisional))
        session.add(_result("call-b", "B"))
        session.add(_invocation("a"))
        session.add(_invocation("b"))
        if pending:
            session.add(
                AgentCheckpointPendingInvocationRecord(
                    checkpoint_id="cp-r8-c",
                    ordinal=0,
                    invocation_id="inv-pending",
                    invocation_revision=1,
                    tool_call_id="call-pending",
                    capability_id="tool.pending",
                    capability_version="1",
                    request_fingerprint="fp-pending",
                    idempotency="IDEMPOTENT",
                    observed_remote_outcome_state="TERMINAL_COMMITTED",
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_is_read_only_and_preserves_parallel_order(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions)
        store = DurableAgentStore(lambda: _Uow(sessions))

        transcript = await store.load_fork_safe_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
        )

        assert [item.role for item in transcript] == ["user", "tool", "tool"]
        assert [item.tool_call_id for item in transcript[1:]] == [
            "call-b",
            "call-a",
        ]
        assert [item.content for item in transcript[1:]] == [
            {"value": "B"},
            {"value": "A"},
        ]

        async with sessions() as session:
            a = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-a",
            )
            b = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-b",
            )
            execution = await AgentRepository(session).get_execution(
                "exec-r8-c"
            )
            assert a.commit_state == "COMMITTED"
            assert b.commit_state == "COMMITTED"
            assert execution.revision == 7
            assert execution.current_checkpoint_id == "cp-r8-c"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_never_promotes_provisional_result(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions, provisional=True)
        store = DurableAgentStore(lambda: _Uow(sessions))

        with pytest.raises(
            ExecutionConflictError,
            match="not durably COMMITTED",
        ):
            await store.load_fork_safe_checkpoint_transcript(
                "exec-r8-c",
                "cp-r8-c",
            )

        async with sessions() as session:
            result = await AgentRepository(session).get_tool_result(
                "exec-r8-c",
                "call-a",
            )
            assert result.commit_state == "PROVISIONAL"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_strict_transcript_rejects_any_pending_checkpoint_row(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions, pending=True)
        store = DurableAgentStore(lambda: _Uow(sessions))

        with pytest.raises(
            ExecutionConflictError,
            match="FORK_PENDING_INVOCATIONS",
        ):
            await store.load_fork_safe_checkpoint_transcript(
                "exec-r8-c",
                "cp-r8-c",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r8_c_invocation_scan_is_deterministic_and_read_only(tmp_path):
    engine, sessions = await _database(tmp_path)
    try:
        async with sessions() as session:
            session.add(_invocation("b"))
            session.add(_invocation("a"))
            session.add(_invocation("other", execution_id="exec-other"))
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        rows = await store.load_fork_capability_invocations("exec-r8-c")
        assert [item.invocation_id for item in rows] == ["a", "b"]

        async with sessions() as session:
            repo = CapabilityInvocationRepository(session)
            direct = await repo.list_records_for_execution("exec-r8-c")
            assert [item.invocation_id for item in direct] == ["a", "b"]
            assert [item.revision for item in direct] == [3, 3]
            assert [item.state for item in direct] == [
                "COMPLETED",
                "COMPLETED",
            ]
    finally:
        await engine.dispose()


async def _bind_r11_full_representation(
    sessions,
    *,
    mode: str,
    messages,
):
    async with sessions() as session:
        repo = AgentRepository(session)
        canonical = canonical_transcript_messages(messages)
        chunk_id = transcript_chunk_id(canonical)
        chunk = await repo.save_transcript_chunk(
            {
                "chunk_id": chunk_id,
                "payload": canonical,
                "message_count": len(canonical),
                "canonical_bytes": len(canonical_json_bytes(canonical)),
            }
        )
        root_ref = transcript_payload_root_ref(
            parent_payload_root_ref=None,
            chunk_id=chunk.chunk_id,
            logical_message_count=len(canonical),
        )
        root = await repo.save_transcript_payload_node(
            {
                "payload_root_ref": root_ref,
                "parent_payload_root_ref": None,
                "chunk_id": chunk.chunk_id,
                "logical_message_count": len(canonical),
            }
        )
        fingerprint = logical_transcript_fingerprint(canonical)
        rep_ref = transcript_representation_ref(
            transcript_version=0,
            kind="FULL",
            parent_transcript_ref=None,
            parent_transcript_version=None,
            delta_depth=0,
            logical_message_count=len(canonical),
            logical_transcript_fingerprint=fingerprint,
            payload_root_ref=root.payload_root_ref,
        )
        await repo.save_transcript_representation(
            {
                "transcript_ref": rep_ref,
                "transcript_version": 0,
                "kind": "FULL",
                "parent_transcript_ref": None,
                "parent_transcript_version": None,
                "delta_depth": 0,
                "logical_message_count": len(canonical),
                "logical_transcript_fingerprint": fingerprint,
                "payload_root_ref": root.payload_root_ref,
            }
        )
        checkpoint = await repo.get_execution_checkpoint("cp-r8-c")
        checkpoint.transcript_ref = rep_ref
        checkpoint.transcript_version = 0
        if mode == "REF_BACKED":
            checkpoint.transcript_snapshot = None
        elif mode != "DUAL":
            raise AssertionError(mode)
        await session.commit()
        return rep_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["REF_BACKED", "DUAL"])
async def test_r11_c_real_b1_representation_converges_resume_fork_retry_readers(
    tmp_path,
    mode,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions)
        store = DurableAgentStore(lambda: _Uow(sessions))

        inline_resume = await store.load_committed_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
            active_tool_call_ids=("call-b", "call-a"),
        )
        inline_fork = await store.load_fork_safe_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
        )
        inline_retry = await _load_retry_safe_checkpoint_transcript(
            store,
            "exec-r8-c",
            "cp-r8-c",
        )

        await _bind_r11_full_representation(
            sessions,
            mode=mode,
            messages=[{"role": "user", "content": "base"}],
        )

        converged_resume = await store.load_committed_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
            active_tool_call_ids=("call-b", "call-a"),
        )
        converged_fork = await store.load_fork_safe_checkpoint_transcript(
            "exec-r8-c",
            "cp-r8-c",
        )
        converged_retry = await _load_retry_safe_checkpoint_transcript(
            store,
            "exec-r8-c",
            "cp-r8-c",
        )

        assert inline_resume == ({"role": "user", "content": "base"},)
        if mode == "DUAL":
            # DUAL retains the pre-C inline outward shape after canonical
            # equality has been proven against the ref-backed authority.
            assert converged_resume == inline_resume
        else:
            # REF_BACKED has no pre-R11 raw JSON shape to preserve; continuation
            # equivalence is the established canonical InferenceMessage meaning.
            assert canonical_transcript_messages(converged_resume) == (
                canonical_transcript_messages(inline_resume)
            )
        assert [
            item.model_dump(mode="json") for item in converged_fork
        ] == [item.model_dump(mode="json") for item in inline_fork]
        assert [
            item.model_dump(mode="json") for item in converged_retry
        ] == [item.model_dump(mode="json") for item in inline_retry]

        async with _Uow(sessions) as uow:
            checkpoint = await uow.agents.get_execution_checkpoint("cp-r8-c")
            fork_in_uow = await _load_fork_safe_transcript_in_uow(
                uow,
                "exec-r8-c",
                checkpoint,
            )
            retry_in_uow = await _load_retry_safe_checkpoint_transcript_in_uow(
                uow,
                "exec-r8-c",
                checkpoint,
            )
            assert [
                item.model_dump(mode="json") for item in fork_in_uow
            ] == [item.model_dump(mode="json") for item in converged_fork]
            assert [
                item.model_dump(mode="json") for item in retry_in_uow
            ] == [item.model_dump(mode="json") for item in converged_retry]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_c_real_dual_mismatch_taxonomy_matches_store_and_in_uow(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions)
        store = DurableAgentStore(lambda: _Uow(sessions))
        await _bind_r11_full_representation(
            sessions,
            mode="DUAL",
            messages=[{"role": "user", "content": "base"}],
        )

        async with sessions() as session:
            repo = AgentRepository(session)
            checkpoint = await repo.get_execution_checkpoint("cp-r8-c")
            checkpoint.transcript_snapshot = [
                {"role": "user", "content": "different"}
            ]
            await session.commit()

        with pytest.raises(
            ExecutionConflictError,
            match="DUAL_TRANSCRIPT_MISMATCH",
        ):
            await store.load_fork_safe_checkpoint_transcript(
                "exec-r8-c",
                "cp-r8-c",
            )

        with pytest.raises(
            RetryPlanRejected,
            match="DUAL_TRANSCRIPT_MISMATCH",
        ) as retry_store:
            await _load_retry_safe_checkpoint_transcript(
                store,
                "exec-r8-c",
                "cp-r8-c",
            )
        assert retry_store.value.code == "DUAL_TRANSCRIPT_MISMATCH"

        async with _Uow(sessions) as uow:
            checkpoint = await uow.agents.get_execution_checkpoint("cp-r8-c")
            with pytest.raises(ForkPlanRejected) as fork_uow:
                await _load_fork_safe_transcript_in_uow(
                    uow,
                    "exec-r8-c",
                    checkpoint,
                )
            assert fork_uow.value.code == "DUAL_TRANSCRIPT_MISMATCH"

            with pytest.raises(RetryPlanRejected) as retry_uow:
                await _load_retry_safe_checkpoint_transcript_in_uow(
                    uow,
                    "exec-r8-c",
                    checkpoint,
                )
            assert retry_uow.value.code == "DUAL_TRANSCRIPT_MISMATCH"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_c_real_persisted_depth_corruption_is_depth_exceeded(
    tmp_path,
):
    engine, sessions = await _database(tmp_path)
    try:
        await _seed_source(sessions)
        async with sessions() as session:
            repo = AgentRepository(session)
            base_messages = canonical_transcript_messages(
                [{"role": "user", "content": "base"}]
            )
            base_chunk_id = transcript_chunk_id(base_messages)
            base_chunk = await repo.save_transcript_chunk(
                {
                    "chunk_id": base_chunk_id,
                    "payload": base_messages,
                    "message_count": 1,
                    "canonical_bytes": len(
                        canonical_json_bytes(base_messages)
                    ),
                }
            )
            base_root_ref = transcript_payload_root_ref(
                parent_payload_root_ref=None,
                chunk_id=base_chunk.chunk_id,
                logical_message_count=1,
            )
            base_root = await repo.save_transcript_payload_node(
                {
                    "payload_root_ref": base_root_ref,
                    "parent_payload_root_ref": None,
                    "chunk_id": base_chunk.chunk_id,
                    "logical_message_count": 1,
                }
            )
            base_fp = logical_transcript_fingerprint(base_messages)
            base_ref = transcript_representation_ref(
                transcript_version=0,
                kind="FULL",
                parent_transcript_ref=None,
                parent_transcript_version=None,
                delta_depth=0,
                logical_message_count=1,
                logical_transcript_fingerprint=base_fp,
                payload_root_ref=base_root.payload_root_ref,
            )
            await repo.save_transcript_representation(
                {
                    "transcript_ref": base_ref,
                    "transcript_version": 0,
                    "kind": "FULL",
                    "parent_transcript_ref": None,
                    "parent_transcript_version": None,
                    "delta_depth": 0,
                    "logical_message_count": 1,
                    "logical_transcript_fingerprint": base_fp,
                    "payload_root_ref": base_root.payload_root_ref,
                }
            )

            suffix = canonical_transcript_messages(
                [{"role": "user", "content": "delta"}]
            )
            suffix_id = transcript_chunk_id(suffix)
            suffix_chunk = await repo.save_transcript_chunk(
                {
                    "chunk_id": suffix_id,
                    "payload": suffix,
                    "message_count": 1,
                    "canonical_bytes": len(canonical_json_bytes(suffix)),
                }
            )
            suffix_root_ref = transcript_payload_root_ref(
                parent_payload_root_ref=None,
                chunk_id=suffix_chunk.chunk_id,
                logical_message_count=1,
            )
            suffix_root = await repo.save_transcript_payload_node(
                {
                    "payload_root_ref": suffix_root_ref,
                    "parent_payload_root_ref": None,
                    "chunk_id": suffix_chunk.chunk_id,
                    "logical_message_count": 1,
                }
            )
            logical = base_messages + suffix
            logical_fp = logical_transcript_fingerprint(logical)
            delta_ref = transcript_representation_ref(
                transcript_version=1,
                kind="DELTA",
                parent_transcript_ref=base_ref,
                parent_transcript_version=0,
                delta_depth=1,
                logical_message_count=2,
                logical_transcript_fingerprint=logical_fp,
                payload_root_ref=suffix_root.payload_root_ref,
            )
            await repo.save_transcript_representation(
                {
                    "transcript_ref": delta_ref,
                    "transcript_version": 1,
                    "kind": "DELTA",
                    "parent_transcript_ref": base_ref,
                    "parent_transcript_version": 0,
                    "delta_depth": 1,
                    "logical_message_count": 2,
                    "logical_transcript_fingerprint": logical_fp,
                    "payload_root_ref": suffix_root.payload_root_ref,
                }
            )
            checkpoint = await repo.get_execution_checkpoint("cp-r8-c")
            checkpoint.transcript_snapshot = None
            checkpoint.transcript_ref = delta_ref
            checkpoint.transcript_version = 1
            await session.commit()

        async with sessions() as session:
            await session.execute(text("PRAGMA ignore_check_constraints = ON"))
            await session.execute(
                text(
                    "UPDATE agent_transcript_representations "
                    "SET delta_depth = 10 "
                    "WHERE transcript_ref = :ref AND transcript_version = 1"
                ),
                {"ref": delta_ref},
            )
            await session.commit()

        store = DurableAgentStore(lambda: _Uow(sessions))
        checkpoint = await store.load_current_checkpoint("exec-r8-c")
        with pytest.raises(
            CheckpointTranscriptMaterializationError,
            match="TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED",
        ):
            await store.materialize_checkpoint_transcript(checkpoint)
    finally:
        await engine.dispose()
