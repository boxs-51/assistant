from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from se.src.infrastructure.storage.transcript_representation import (
    canonical_transcript_messages,
)
from se.src.runtimes.agent.checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
    materialize_checkpoint_transcript_in_uow,
)


@dataclass
class _Checkpoint:
    transcript_snapshot: list[dict] | None
    transcript_ref: str | None
    transcript_version: int | None


class _Agents:
    def __init__(
        self,
        *,
        rows=None,
        versions=None,
        materialized=None,
        materialize_error: Exception | None = None,
    ):
        self.rows = rows or {}
        self.versions = versions or {}
        self.materialized = materialized or {}
        self.materialize_error = materialize_error

    async def get_transcript_representation(self, ref: str, version: int):
        return self.rows.get((ref, version))

    async def list_transcript_representation_versions(self, ref: str):
        return tuple(self.versions.get(ref, ()))

    async def materialize_transcript_representation(self, ref: str, version: int):
        if self.materialize_error is not None:
            raise self.materialize_error
        return list(self.materialized[(ref, version)])


def _uow(agents: _Agents):
    return SimpleNamespace(agents=agents)


def _raw(text: str):
    return {"role": "user", "content": text}


@pytest.mark.asyncio
async def test_r11_c_legacy_inline_is_canonicalized():
    checkpoint = _Checkpoint([_raw("same")], None, None)

    result = await materialize_checkpoint_transcript_in_uow(
        _uow(_Agents()),
        checkpoint,
    )

    assert [item.model_dump(mode="json") for item in result] == (
        canonical_transcript_messages([_raw("same")])
    )


@pytest.mark.asyncio
async def test_r11_c_ref_backed_materializes_exact_immutable_pair():
    ref = "a" * 64
    checkpoint = _Checkpoint(None, ref, 0)
    agents = _Agents(
        rows={(ref, 0): object()},
        materialized={(ref, 0): [_raw("ref")]},
    )

    result = await materialize_checkpoint_transcript_in_uow(
        _uow(agents),
        checkpoint,
    )

    assert result[0].content == "ref"


@pytest.mark.asyncio
async def test_r11_c_dual_compares_canonical_semantics_not_raw_shape():
    ref = "b" * 64
    canonical = canonical_transcript_messages([_raw("same")])
    checkpoint = _Checkpoint([_raw("same")], ref, 0)
    agents = _Agents(
        rows={(ref, 0): object()},
        materialized={(ref, 0): canonical},
    )

    result = await materialize_checkpoint_transcript_in_uow(
        _uow(agents),
        checkpoint,
    )

    assert [item.model_dump(mode="json") for item in result] == canonical


@pytest.mark.asyncio
async def test_r11_c_dual_mismatch_fails_closed():
    ref = "c" * 64
    checkpoint = _Checkpoint([_raw("inline")], ref, 0)
    agents = _Agents(
        rows={(ref, 0): object()},
        materialized={(ref, 0): [_raw("ref")]},
    )

    with pytest.raises(
        CheckpointTranscriptMaterializationError,
        match="DUAL_TRANSCRIPT_MISMATCH",
    ):
        await materialize_checkpoint_transcript_in_uow(
            _uow(agents),
            checkpoint,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint",
    [
        _Checkpoint(None, None, None),
        _Checkpoint([_raw("x")], "d" * 64, None),
        _Checkpoint([_raw("x")], None, 0),
    ],
)
async def test_r11_c_illegal_checkpoint_representation_states_fail_closed(
    checkpoint,
):
    with pytest.raises(
        CheckpointTranscriptMaterializationError,
        match="INVALID_CHECKPOINT_REPRESENTATION_STATE",
    ):
        await materialize_checkpoint_transcript_in_uow(
            _uow(_Agents()),
            checkpoint,
        )


@pytest.mark.asyncio
async def test_r11_c_missing_and_version_mismatch_remain_distinct():
    ref = "e" * 64

    with pytest.raises(
        CheckpointTranscriptMaterializationError,
        match="MISSING_TRANSCRIPT_REPRESENTATION",
    ):
        await materialize_checkpoint_transcript_in_uow(
            _uow(_Agents()),
            _Checkpoint(None, ref, 0),
        )

    with pytest.raises(
        CheckpointTranscriptMaterializationError,
        match="TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH",
    ):
        await materialize_checkpoint_transcript_in_uow(
            _uow(_Agents(versions={ref: (0, 1)})),
            _Checkpoint(None, ref, 2),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        (
            "Stored DELTA representation version ancestry is invalid.",
            "TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID",
        ),
        (
            "DELTA depth exceeds R11-B safety envelope.",
            "TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED",
        ),
        (
            "Transcript representation logical fingerprint is corrupt.",
            "TRANSCRIPT_REPRESENTATION_CORRUPT",
        ),
    ],
)
async def test_r11_c_representation_integrity_failures_keep_taxonomy(
    message,
    expected_code,
):
    ref = "f" * 64
    agents = _Agents(
        rows={(ref, 0): object()},
        materialize_error=ValueError(message),
    )

    with pytest.raises(
        CheckpointTranscriptMaterializationError,
        match=expected_code,
    ):
        await materialize_checkpoint_transcript_in_uow(
            _uow(agents),
            _Checkpoint(None, ref, 0),
        )


def test_r11_c_runtime_readers_do_not_require_inline_snapshot():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "runtimes" / "agent"
    fork_source = (root / "fork_planning.py").read_text(encoding="utf-8")
    retry_source = (root / "retry_planning.py").read_text(encoding="utf-8")

    assert "checkpoint.transcript_snapshot is None" not in fork_source
    assert "checkpoint.transcript_snapshot is None" not in retry_source
    assert "materialize_checkpoint_transcript_in_uow" in fork_source
