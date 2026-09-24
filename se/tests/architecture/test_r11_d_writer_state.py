from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.runtimes.agent.waiting_checkpoint import (
    WaitingCheckpointConflictError,
    stage_waiting_checkpoint,
)


class _Agents:
    def __init__(self):
        self.saved = []

    async def save_execution_checkpoint(self, values):
        self.saved.append(dict(values))

    async def save_checkpoint_pending_invocation(self, values):
        raise AssertionError("no pending invocation expected")


class _Uow:
    def __init__(self):
        self.agents = _Agents()


def _execution():
    return SimpleNamespace(
        id="exec-r11-d",
        revision=3,
        state="RUNNING",
        session_id="session-r11-d",
        task_id=None,
        branch_id=None,
        current_checkpoint_id="cp-parent",
    )


def _checkpoint(**overrides):
    values = {
        "checkpoint_id": "cp-r11-d",
        "execution_id": "exec-r11-d",
        "execution_revision": 4,
        "session_id": "session-r11-d",
        "task_id": None,
        "branch_id": None,
        "iteration": 1,
        "wait_reason": "RESOURCE",
        "metadata_json": {},
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_r11_d_ref_backed_writer_preserves_null_snapshot():
    uow = _Uow()
    await stage_waiting_checkpoint(
        uow,
        execution=_execution(),
        source_revision=3,
        transition_values={"wait_reason": "RESOURCE"},
        checkpoint_values=_checkpoint(
            transcript_snapshot=None,
            transcript_ref="a" * 64,
            transcript_version=0,
        ),
        pending_invocations=(),
    )

    assert len(uow.agents.saved) == 1
    saved = uow.agents.saved[0]
    assert saved["transcript_snapshot"] is None
    assert saved["transcript_ref"] == "a" * 64
    assert saved["transcript_version"] == 0


@pytest.mark.asyncio
async def test_r11_d_empty_inline_transcript_is_not_ref_backed():
    uow = _Uow()
    await stage_waiting_checkpoint(
        uow,
        execution=_execution(),
        source_revision=3,
        transition_values={"wait_reason": "RESOURCE"},
        checkpoint_values=_checkpoint(transcript_snapshot=[]),
        pending_invocations=(),
    )

    saved = uow.agents.saved[0]
    assert saved["transcript_snapshot"] == []
    assert saved.get("transcript_ref") is None
    assert saved.get("transcript_version") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript_ref", "transcript_version"),
    [
        ("b" * 64, None),
        (None, 0),
    ],
)
async def test_r11_d_writer_rejects_partial_representation_pair(
    transcript_ref,
    transcript_version,
):
    with pytest.raises(
        WaitingCheckpointConflictError,
        match="transcript_ref/transcript_version must be an exact pair",
    ):
        await stage_waiting_checkpoint(
            _Uow(),
            execution=_execution(),
            source_revision=3,
            transition_values={"wait_reason": "RESOURCE"},
            checkpoint_values=_checkpoint(
                transcript_snapshot=None,
                transcript_ref=transcript_ref,
                transcript_version=transcript_version,
            ),
            pending_invocations=(),
        )


@pytest.mark.asyncio
async def test_r11_d_writer_rejects_unreconstructable_state():
    with pytest.raises(
        WaitingCheckpointConflictError,
        match="representation is not reconstructable",
    ):
        await stage_waiting_checkpoint(
            _Uow(),
            execution=_execution(),
            source_revision=3,
            transition_values={"wait_reason": "RESOURCE"},
            checkpoint_values=_checkpoint(transcript_snapshot=None),
            pending_invocations=(),
        )
