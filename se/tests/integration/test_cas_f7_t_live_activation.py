from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.runtimes.agent.runtime import AgentRuntime


class _DurableStore:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            id="result-1",
            commit_state="COMMITTED",
            execution_id="exec-1",
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="desktop.screenshot",
            success=True,
            output={"ok": True},
            error_code=None,
            error_message=None,
            retryable=False,
            extra_metadata={},
        )

    async def load_committed_tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
    ):
        if (
            execution_id != self.record.execution_id
            or tool_call_id != self.record.tool_call_id
        ):
            return None
        return self.record


class _BlockingCanonicalizer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def canonicalize_committed_result(
        self,
        *,
        source_result_id: str,
    ):
        self.calls.append(source_result_id)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ()


class _FailingCanonicalizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def canonicalize_committed_result(
        self,
        *,
        source_result_id: str,
    ):
        self.calls.append(source_result_id)
        raise RuntimeError("simulated CAS publication failure")


def _runtime(canonicalizer):
    return AgentRuntime(
        context_builder=object(),
        inference=object(),
        tool_execution=object(),
        execution_policy=object(),
        durable_store=_DurableStore(),
        f7t_canonicalizer=canonicalizer,
    )


def _request():
    return SimpleNamespace(
        execution_id="exec-1",
        iteration=3,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="desktop.screenshot",
    )


def _context():
    return SimpleNamespace(
        execution_id="exec-1",
        iteration=3,
    )


@pytest.mark.asyncio
async def test_both_committed_read_seams_schedule_same_durable_source_without_gating():
    canonicalizer = _BlockingCanonicalizer()
    runtime = _runtime(canonicalizer)

    first = await runtime._load_committed_tool_result(_request())
    assert first is not None
    assert first.success is True
    assert canonicalizer.release.is_set() is False

    second = await runtime._load_committed_tool_result_by_id(
        _context(),
        "call-1",
    )
    assert second.success is True
    assert canonicalizer.release.is_set() is False

    await asyncio.sleep(0)
    assert canonicalizer.calls == ["result-1", "result-1"]
    assert len(runtime._f7t_publication_tasks) == 2

    canonicalizer.release.set()
    await asyncio.sleep(0)
    await runtime.quiesce_f7t_publication()
    assert runtime._f7t_publication_tasks == set()


@pytest.mark.asyncio
async def test_publication_failure_is_observed_without_changing_committed_result():
    canonicalizer = _FailingCanonicalizer()
    runtime = _runtime(canonicalizer)

    result = await runtime._load_committed_tool_result(_request())
    assert result is not None
    assert result.success is True
    assert result.output == {"ok": True}

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert canonicalizer.calls == ["result-1"]
    assert runtime._f7t_publication_tasks == set()


@pytest.mark.asyncio
async def test_quiesce_cancels_owned_publication_and_rejects_new_scheduling():
    canonicalizer = _BlockingCanonicalizer()
    runtime = _runtime(canonicalizer)

    result = await runtime._load_committed_tool_result(_request())
    assert result is not None
    await canonicalizer.started.wait()
    assert canonicalizer.calls == ["result-1"]

    await runtime.quiesce_f7t_publication()

    assert canonicalizer.cancelled.is_set()
    assert runtime._f7t_publication_tasks == set()

    repeated = await runtime._load_committed_tool_result(_request())
    assert repeated is not None
    await asyncio.sleep(0)
    assert canonicalizer.calls == ["result-1"]
