from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

# Import the agent runtime path before SQL transcript models. Direct-file pytest
# starts from a fresh interpreter, and this ordering prevents storage -> agent
# package initialization from re-entering transcript storage mid-import.
from se.src.runtimes.agent.persistence import DurableAgentStore

from se.src.infrastructure.storage.models.sql.agent import (
    AgentTranscriptChunkRecord,
    AgentTranscriptRepresentationRecord,
)
from se.src.infrastructure.storage.transcript_representation import (
    canonical_json_bytes,
    canonical_transcript_messages,
)
from se.src.runtimes.agent.checkpoint_transcript_writer import (
    write_transcript_representation_in_uow,
)
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.agent.retry_planning import AgentRetryPlanningService
from se.tests.architecture.test_r11_a_branch_budget_memory_baseline import (
    _Uow as _BranchUow,
    _limits as _branch_limits,
    _measure_direct_task_budget_cas_contention_distribution,
    _seed_fork_source,
    _setup as _branch_setup,
)
from se.tests.architecture.test_r11_a_persistence_baseline import (
    _MeasuredUow,
    _SqlMetrics,
    _measure_waiting_commit,
    _message as _persistence_message,
    _new_database,
    _seed_execution_and_invocations,
)
from se.tests.architecture.test_r11_a_resume_performance_baseline import (
    CLIENT as _RESUME_CLIENT,
    NEW_CONNECTION as _RESUME_CONNECTION,
    USER as _RESUME_USER,
    _CapabilityRuntime,
    _seed_resume_samples,
)
from se.tests.integration.test_r11_d_transcript_writer import (
    _Uow as _WriterUow,
    _database as _writer_database,
    _execution_and_checkpoint,
    _message as _writer_message,
)
from se.tests.integration.test_r9_b_atomic_retry_admission import (
    _seed_failed_source,
)


def _assert_percentile_order(sample: dict[str, int]) -> None:
    assert sample["p50_ns"] <= sample["p95_ns"] <= sample["p99_ns"]


def _state_value(value) -> str:
    return str(getattr(value, "value", value))


@pytest.mark.asyncio
async def test_r11_g1_task_budget_contention_stress_preserves_cas_semantics(
    tmp_path,
):
    report = await _measure_direct_task_budget_cas_contention_distribution(
        tmp_path,
        samples=24,
    )

    assert report["samples"] == 24
    assert report["service_calls"] == 48
    assert report["service_successes"] == 48
    assert report["barrier_arrivals"] == 48
    assert report["cas_successes"] == 48
    assert report["retry_signals"] >= 24
    assert report["cas_other_errors"] == 0
    assert report["final_active_branches_total"] == 48
    assert report["reservation_rows_total"] == 48
    _assert_percentile_order(report["cas_latency"])
    _assert_percentile_order(report["race_latency"])


@pytest.mark.asyncio
async def test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded(tmp_path):
    checkpoints = 48
    engine, sessions = await _writer_database(tmp_path)
    try:
        async with sessions() as session:
            uow = _WriterUow(session)
            messages: list[dict[str, object]] = []
            parent_checkpoint_id = None
            full_count = 0
            delta_count = 0
            max_delta_depth = 0
            final_representation = None

            for index in range(checkpoints):
                messages.append(_writer_message(f"g1-{index:03d}"))
                representation = await write_transcript_representation_in_uow(
                    uow,
                    messages=messages,
                    candidate_parent_checkpoint_id=parent_checkpoint_id,
                )
                record = await uow.agents.get_transcript_representation(
                    representation.transcript_ref,
                    representation.transcript_version,
                )
                assert record is not None
                depth = int(record.delta_depth)
                assert 0 <= depth <= 9
                max_delta_depth = max(max_delta_depth, depth)
                if record.kind == "FULL":
                    full_count += 1
                    assert depth == 0
                else:
                    delta_count += 1
                    assert record.kind == "DELTA"
                    assert depth > 0

                checkpoint_id = f"cp-r11-g1-{index:03d}"
                await _execution_and_checkpoint(
                    uow,
                    execution_id=f"exec-r11-g1-{index:03d}",
                    checkpoint_id=checkpoint_id,
                    representation=representation,
                    snapshot=None,
                    parent_checkpoint_id=parent_checkpoint_id,
                )
                checkpoint = await uow.agents.get_execution_checkpoint(
                    checkpoint_id
                )
                assert checkpoint is not None
                assert checkpoint.transcript_snapshot is None
                assert checkpoint.transcript_ref == representation.transcript_ref
                assert (
                    int(checkpoint.transcript_version)
                    == int(representation.transcript_version)
                )
                parent_checkpoint_id = checkpoint_id
                final_representation = representation

            assert final_representation is not None
            materialized = await uow.agents.materialize_transcript_representation(
                final_representation.transcript_ref,
                final_representation.transcript_version,
            )
            assert [item["content"] for item in materialized] == [
                f"g1-{index:03d}" for index in range(checkpoints)
            ]

            representation_count = int(
                await session.scalar(
                    select(func.count()).select_from(
                        AgentTranscriptRepresentationRecord
                    )
                )
                or 0
            )
            chunk_count = int(
                await session.scalar(
                    select(func.count()).select_from(AgentTranscriptChunkRecord)
                )
                or 0
            )
            stored_chunk_bytes = int(
                await session.scalar(
                    select(func.sum(AgentTranscriptChunkRecord.canonical_bytes))
                )
                or 0
            )
            final_logical_bytes = len(
                canonical_json_bytes(
                    canonical_transcript_messages(messages)
                )
            )

            assert representation_count == checkpoints
            assert full_count + delta_count == checkpoints
            assert full_count >= 5
            assert max_delta_depth == 9
            assert chunk_count <= checkpoints + 1
            assert stored_chunk_bytes <= final_logical_bytes * 2
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_g1_multi_branch_uses_atomic_consume_and_stable_listing(
    tmp_path,
):
    engine, sessions, service, planner, metrics = await _branch_setup(
        tmp_path,
        name="r11-g1-multi-branch.sqlite",
        limits=_branch_limits(
            max_active_branches=16,
            max_active_executions=16,
            max_total_executions=32,
        ),
    )
    store = DurableAgentStore(lambda: _BranchUow(sessions, metrics))
    try:
        source = await _seed_fork_source(
            sessions,
            service,
            planner,
            metrics,
            task_id="task-r11-g1-multi-branch",
            fork_request_id="fork-r11-g1-0",
        )
        admissions = []
        for index in range(8):
            if index == 0:
                plan = source["plan"]
            else:
                plan = await planner.build_fork_plan(
                    fork_request_id=f"fork-r11-g1-{index}",
                    task_id=source["task_id"],
                    source_branch_id=source["source_branch_id"],
                    source_execution_id=source["source_execution_id"],
                    source_checkpoint_id=source["checkpoint_id"],
                    target_user_id="user-r11-a",
                    overlay_messages=(
                        {
                            "role": "user",
                            "content": f"fork-r11-g1-{index}",
                        },
                    ),
                )
            admissions.append(await service.consume_fork_plan(plan))

        first = await store.list_task_branches(source["task_id"])
        second = await store.list_task_branches(source["task_id"])
        budget = await service.get_budget(source["task_id"])

        first_ids = [branch.branch_id for branch in first]
        second_ids = [branch.branch_id for branch in second]
        admission_ids = {item.branch_id for item in admissions}

        assert len(first_ids) == 9
        assert first_ids == second_ids
        assert len(set(first_ids)) == 9
        assert source["source_branch_id"] in first_ids
        assert admission_ids.issubset(set(first_ids))
        assert all(branch.task_id == source["task_id"] for branch in first)
        assert int(budget.active_branches) == 9
        assert int(budget.active_executions) == 8
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_g1_large_pending_batch_is_atomic_ordered_and_batched(
    tmp_path,
):
    pending_count = 128
    message_count = 32
    baseline = await _measure_waiting_commit(
        tmp_path,
        0,
        message_count=message_count,
    )

    metrics = _SqlMetrics()
    engine, sessions = await _new_database(
        tmp_path / "r11-g1-large-pending.sqlite",
        metrics,
    )
    execution_id = "exec-r11-g1-large-pending"
    checkpoint_id = f"{execution_id}:checkpoint:2"
    try:
        pending = await _seed_execution_and_invocations(
            sessions,
            execution_id=execution_id,
            pending_count=pending_count,
        )
        async with _MeasuredUow(sessions, metrics) as authority_uow:
            seeded_invocation = await authority_uow.capability_invocations.get_record(
                pending[0]["invocation_id"]
            )
            assert seeded_invocation is not None
            checkpoint_origin_client_id = str(
                seeded_invocation.origin_client_id
            )
            checkpoint_origin_connection_id = str(
                seeded_invocation.connection_id
            )

        metrics.reset()
        store = DurableAgentStore(lambda: _MeasuredUow(sessions, metrics))

        committed = await store.commit_waiting_checkpoint(
            execution_id,
            1,
            {
                "state": "WAITING",
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
            },
            checkpoint_values={
                "checkpoint_id": checkpoint_id,
                "execution_id": execution_id,
                "execution_revision": 2,
                "session_id": f"session-{execution_id}",
                "task_id": None,
                "branch_id": None,
                "iteration": 1,
                "wait_reason": "RESOURCE",
                "remaining_active_budget_seconds": 30.0,
                "wait_expires_at": None,
                "origin_client_id": checkpoint_origin_client_id,
                "origin_connection_id": checkpoint_origin_connection_id,
                "transcript_snapshot": [
                    _persistence_message(index)
                    for index in range(message_count)
                ],
                "metadata_json": {"phase": "r11-g1"},
            },
            pending_invocations=pending,
        )
        write_metrics = metrics.snapshot()
        loaded = await store.load_checkpoint_pending_invocations(
            checkpoint_id
        )
        checkpoint = await store.load_current_checkpoint(execution_id)

        assert int(committed.revision) == 2
        assert checkpoint is not None
        assert checkpoint.checkpoint_id == checkpoint_id
        assert checkpoint.transcript_snapshot is None
        assert checkpoint.transcript_ref is not None
        assert checkpoint.transcript_version is not None

        assert len(loaded) == pending_count
        assert [int(item.ordinal) for item in loaded] == list(
            range(pending_count)
        )
        assert [item.invocation_id for item in loaded] == [
            item["invocation_id"] for item in pending
        ]
        assert [item.tool_call_id for item in loaded] == [
            item["tool_call_id"] for item in pending
        ]

        assert write_metrics["commits"] == 1
        assert write_metrics["rollbacks"] == 0
        assert write_metrics["select"] - baseline["select"] == pending_count
        assert write_metrics["insert"] - baseline["insert"] == 1
        assert write_metrics["flushes"] == baseline["flushes"]
        assert (
            write_metrics["sql"] - baseline["sql"]
            == pending_count + 1
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_r11_g1_concurrent_resume_retry_fork_read_pressure_is_stable(
    tmp_path,
):
    sample_count = 4
    engine, sessions, service, fork_planner, metrics = await _branch_setup(
        tmp_path,
        name="r11-g1-read-pressure.sqlite",
        limits=_branch_limits(
            max_active_branches=32,
            max_active_executions=32,
            max_total_executions=64,
        ),
    )
    store = DurableAgentStore(lambda: _BranchUow(sessions, metrics))
    retry_planner = AgentRetryPlanningService(store)
    try:
        fork_sources = []
        for index in range(sample_count):
            fork_sources.append(
                await _seed_fork_source(
                    sessions,
                    service,
                    fork_planner,
                    metrics,
                    task_id=f"task-r11-g1-read-fork-{index}",
                    fork_request_id=f"seed-fork-r11-g1-{index}",
                )
            )

        retry_sources = []
        for index in range(sample_count):
            _root, plan = await _seed_failed_source(
                sessions,
                service,
                retry_planner,
                task_id=f"task-r11-g1-read-retry-{index}",
                retry_request_id=f"seed-retry-r11-g1-{index}",
            )
            retry_sources.append(plan)

        invocations = await _seed_resume_samples(
            sessions,
            samples=sample_count,
            message_count=16,
        )
        resume_planner = AgentResumePlanningService(
            store,
            _CapabilityRuntime(invocations),
            now_utc=lambda: datetime.now(timezone.utc),
        )

        execution_ids = [
            source["source_execution_id"] for source in fork_sources
        ] + [
            plan.source_execution_id for plan in retry_sources
        ] + [
            f"exec-r11-a-resume-{index}"
            for index in range(sample_count)
        ]

        before = {}
        for execution_id in execution_ids:
            execution = await store.load_execution(execution_id)
            assert execution is not None
            before[execution_id] = (
                int(execution.revision),
                _state_value(execution.state),
                execution.current_checkpoint_id,
            )

        def build_wave():
            calls = []
            for index, source in enumerate(fork_sources):
                calls.append(
                    fork_planner.build_fork_plan(
                        fork_request_id=f"read-fork-r11-g1-{index}",
                        task_id=source["task_id"],
                        source_branch_id=source["source_branch_id"],
                        source_execution_id=source["source_execution_id"],
                        source_checkpoint_id=source["checkpoint_id"],
                        target_user_id="user-r11-a",
                        overlay_messages=(
                            {
                                "role": "user",
                                "content": f"read-pressure-{index}",
                            },
                        ),
                    )
                )
            for index, seeded in enumerate(retry_sources):
                calls.append(
                    retry_planner.build_retry_plan(
                        retry_request_id=f"read-retry-r11-g1-{index}",
                        task_id=seeded.task_id,
                        branch_id=seeded.branch_id,
                        source_execution_id=seeded.source_execution_id,
                        target_user_id="user-r9",
                        source_checkpoint_id=None,
                    )
                )
            for index in range(sample_count):
                calls.append(
                    resume_planner.build_resume_plan(
                        f"exec-r11-a-resume-{index}",
                        f"cp-r11-a-resume-{index}",
                        target_user_id=_RESUME_USER,
                        target_client_id=_RESUME_CLIENT,
                        target_connection_id=_RESUME_CONNECTION,
                    )
                )
            return calls

        started_ns = time.perf_counter_ns()
        first = await asyncio.gather(*build_wave())
        second = await asyncio.gather(*build_wave())
        elapsed_ns = time.perf_counter_ns() - started_ns

        first_fingerprints = [
            item.plan_fingerprint for item in first
        ]
        second_fingerprints = [
            item.plan_fingerprint for item in second
        ]
        assert len(first) == len(second) == sample_count * 3
        assert first_fingerprints == second_fingerprints
        assert elapsed_ns > 0

        after = {}
        for execution_id in execution_ids:
            execution = await store.load_execution(execution_id)
            assert execution is not None
            after[execution_id] = (
                int(execution.revision),
                _state_value(execution.state),
                execution.current_checkpoint_id,
            )
        assert after == before
    finally:
        await engine.dispose()
