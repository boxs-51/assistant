from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from se.src.context.source_adapters import (
    project_agent_transcript_source,
    project_asset_source,
    project_branch_source,
    project_session_source,
    project_task_source,
    project_tool_response_payload_source,
)
from se.src.context.source_identity import ContextSourceKind
from se.src.context.tool_response_payload import create_tool_response_payload


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Session:
    id: str = "session-1"
    user_id: str | None = "user-1"
    status: str = "active"
    created_at: datetime = NOW


@dataclass(frozen=True)
class Task:
    id: str = "task-1"
    session_id: str = "session-1"
    revision: int = 3
    status: str = "WAITING"
    created_at: datetime = NOW
    created_by: str = "agent-creator"


@dataclass(frozen=True)
class Branch:
    branch_id: str = "branch-1"
    task_id: str = "task-1"
    revision: int = 2
    resolution_state: str = "OPEN"
    created_at: datetime = NOW
    created_by: str = "agent-creator"


@dataclass(frozen=True)
class Execution:
    id: str = "exec-1"
    session_id: str = "session-1"
    task_id: str | None = "task-1"
    branch_id: str | None = "branch-1"


@dataclass(frozen=True)
class Checkpoint:
    execution_id: str = "exec-1"
    session_id: str = "session-1"
    task_id: str | None = "task-1"
    branch_id: str | None = "branch-1"
    transcript_ref: str | None = "transcript-ref-1"
    transcript_version: int | None = 4
    created_at: datetime = NOW


@dataclass(frozen=True)
class Invocation:
    invocation_id: str = "inv-1"
    execution_id: str | None = "exec-1"
    session_id: str | None = "session-1"
    tool_call_id: str | None = "call-1"
    capability_id: str = "cap-1"
    owner_user_id: str | None = "user-1"


@dataclass(frozen=True)
class Result:
    id: str = "result-1"
    execution_id: str = "exec-1"
    invocation_id: str = "inv-1"
    tool_call_id: str = "call-1"
    capability_id: str = "cap-1"
    commit_state: str = "COMMITTED"
    created_at: datetime = NOW


@dataclass(frozen=True)
class Asset:
    asset_id: str = "asset-1"
    owner_user_id: str = "user-1"
    filename: str = "report.pdf"
    mime_type: str = "application/pdf"
    size_bytes: int | None = 128
    sha256: str | None = "abc123"
    state: str = "READY"
    uri: str = "asset://asset-1"
    origin_type: str = "USER_UPLOAD"
    revision: int = 1


def _payload(**overrides):
    values = {
        "source_result_id": "result-1",
        "invocation_id": "inv-1",
        "execution_id": "exec-1",
        "tool_call_id": "call-1",
        "logical_capability_id": "cap-1",
        "content": {"ok": True},
        "source_commit_state": "COMMITTED",
        "owner_user_id": "user-1",
        "session_id": "session-1",
    }
    values.update(overrides)
    return create_tool_response_payload(**values)


def test_ctx_f2b_session_projection_uses_canonical_session_owner():
    ref = project_session_source(Session())
    assert ref.source_kind is ContextSourceKind.SESSION
    assert ref.authority_id == "session-1"
    assert ref.owner_user_id == "user-1"
    assert ref.session_id == "session-1"


def test_ctx_f2b_session_projection_rejects_missing_owner():
    with pytest.raises(ValueError, match="session.user_id"):
        project_session_source(Session(user_id=None))


def test_ctx_f2b_task_projection_is_deterministic_and_revision_sensitive():
    first = project_task_source(Session(), Task())
    same = project_task_source(Session(), Task())
    changed = project_task_source(Session(), Task(revision=4))
    assert first.context_source_id == same.context_source_id
    assert first.context_source_id != changed.context_source_id
    assert first.owner_user_id == "user-1"
    assert first.source_state == "WAITING"


def test_ctx_f2b_task_projection_rejects_session_lineage_mismatch():
    with pytest.raises(ValueError, match="task.session_id mismatch"):
        project_task_source(Session(), Task(session_id="other-session"))


def test_ctx_f2b_task_created_by_never_becomes_owner():
    ref = project_task_source(Session(user_id="user-owner"), Task(created_by="other"))
    assert ref.owner_user_id == "user-owner"


def test_ctx_f2b_branch_projection_checks_task_and_session_lineage():
    ref = project_branch_source(Session(), Task(), Branch())
    assert ref.source_kind is ContextSourceKind.BRANCH
    assert ref.task_id == "task-1"
    assert ref.branch_id == "branch-1"

    with pytest.raises(ValueError, match="branch.task_id mismatch"):
        project_branch_source(Session(), Task(), Branch(task_id="task-other"))

    with pytest.raises(ValueError, match="task.session_id mismatch"):
        project_branch_source(Session(), Task(session_id="session-other"), Branch())


def test_ctx_f2b_branch_created_by_never_becomes_owner():
    ref = project_branch_source(
        Session(user_id="user-owner"),
        Task(),
        Branch(created_by="other"),
    )
    assert ref.owner_user_id == "user-owner"


@pytest.mark.parametrize(
    "checkpoint,execution,message",
    [
        (Checkpoint(execution_id="other"), Execution(), "checkpoint.execution_id mismatch"),
        (
            Checkpoint(session_id="other"),
            Execution(),
            "checkpoint.session_id mismatch",
        ),
        (
            Checkpoint(session_id="other"),
            Execution(session_id="other"),
            "execution.session_id mismatch",
        ),
        (
            Checkpoint(task_id="other"),
            Execution(),
            "checkpoint.task_id mismatch",
        ),
        (
            Checkpoint(branch_id="other"),
            Execution(),
            "checkpoint.branch_id mismatch",
        ),
    ],
)
def test_ctx_f2b_transcript_projection_rejects_lineage_mismatch(
    checkpoint,
    execution,
    message,
):
    with pytest.raises(ValueError, match=message):
        project_agent_transcript_source(Session(), execution, checkpoint)


@pytest.mark.parametrize(
    "checkpoint,message",
    [
        (Checkpoint(transcript_ref=None), "checkpoint.transcript_ref"),
        (Checkpoint(transcript_version=None), "checkpoint.transcript_version"),
        (Checkpoint(transcript_version=-1), "checkpoint.transcript_version"),
        (Checkpoint(transcript_version=True), "checkpoint.transcript_version"),
    ],
)
def test_ctx_f2b_transcript_projection_requires_exact_pair(checkpoint, message):
    with pytest.raises(ValueError, match=message):
        project_agent_transcript_source(Session(), Execution(), checkpoint)


def test_ctx_f2b_transcript_projection_uses_r11_pair_as_native_authority():
    ref = project_agent_transcript_source(Session(), Execution(), Checkpoint())
    assert ref.source_kind is ContextSourceKind.AGENT_TRANSCRIPT
    assert ref.authority_id == "transcript-ref-1"
    assert ref.authority_version == 4
    assert ref.session_id == "session-1"
    assert ref.task_id == "task-1"
    assert ref.branch_id == "branch-1"


def test_ctx_f2b_trp_projection_requires_durable_committed_result():
    with pytest.raises(ValueError, match="durable tool result must be COMMITTED"):
        project_tool_response_payload_source(
            Session(),
            Execution(),
            Invocation(),
            Result(commit_state="PROVISIONAL"),
            _payload(),
        )


@pytest.mark.parametrize(
    "invocation,message",
    [
        (Invocation(owner_user_id=None), "invocation.owner_user_id"),
        (Invocation(owner_user_id="other-user"), "invocation.owner_user_id mismatch"),
        (Invocation(execution_id="other"), "invocation.execution_id mismatch"),
        (Invocation(session_id="other"), "invocation.session_id mismatch"),
        (Invocation(invocation_id="other"), "invocation.invocation_id mismatch"),
        (Invocation(tool_call_id="other"), "invocation.tool_call_id mismatch"),
        (Invocation(capability_id="other"), "invocation.capability_id mismatch"),
    ],
)
def test_ctx_f2b_trp_projection_rejects_invocation_authority_mismatch(
    invocation,
    message,
):
    with pytest.raises(ValueError, match=message):
        project_tool_response_payload_source(
            Session(),
            Execution(),
            invocation,
            Result(),
            _payload(),
        )


@pytest.mark.parametrize(
    "result,payload_kwargs,message",
    [
        (Result(id="other-result"), {}, "payload.source_result_id mismatch"),
        (Result(invocation_id="other-inv"), {}, "payload.invocation_id mismatch"),
        (Result(execution_id="other-exec"), {}, "result.execution_id mismatch"),
        (Result(tool_call_id="other-call"), {}, "payload.tool_call_id mismatch"),
        (Result(capability_id="other-cap"), {}, "payload.logical_capability_id mismatch"),
        (Result(), {"owner_user_id": "other-user"}, "payload.owner_user_id mismatch"),
        (Result(), {"session_id": "other-session"}, "payload.session_id mismatch"),
    ],
)
def test_ctx_f2b_trp_projection_rejects_result_payload_authority_mismatch(
    result,
    payload_kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        project_tool_response_payload_source(
            Session(),
            Execution(),
            Invocation(),
            result,
            _payload(**payload_kwargs),
        )


def test_ctx_f2b_trp_projection_accepts_proven_committed_source():
    payload = _payload()
    first = project_tool_response_payload_source(
        Session(), Execution(), Invocation(), Result(), payload
    )
    second = project_tool_response_payload_source(
        Session(), Execution(), Invocation(), Result(), payload
    )
    assert first.source_kind is ContextSourceKind.TOOL_RESPONSE_PAYLOAD
    assert first.authority_id == payload.payload_id
    assert first.authority_version is None
    assert first.owner_user_id == "user-1"
    assert first.context_source_id == second.context_source_id


def test_ctx_f2c_asset_projection_uses_canonical_asset_authority():
    ref = project_asset_source(Asset())
    assert ref.source_kind is ContextSourceKind.ASSET
    assert ref.authority_id == "asset-1"
    assert ref.authority_version is None
    assert ref.owner_user_id == "user-1"
    assert ref.source_state == "READY"
    assert ref.metadata["uri"] == "asset://asset-1"
    assert ref.metadata["file_asset_revision"] == 1


def test_ctx_f2c_asset_identity_is_stable_across_lifecycle_revision_changes():
    first = project_asset_source(Asset(revision=1))
    changed = project_asset_source(Asset(revision=2))
    assert first.context_source_id == changed.context_source_id
    assert first.metadata["file_asset_revision"] == 1
    assert changed.metadata["file_asset_revision"] == 2


@pytest.mark.parametrize("state", ["STAGING", "DELETING", "DELETED", ""])
def test_ctx_f2c_asset_projection_rejects_non_ready_evidence(state):
    with pytest.raises(ValueError, match="asset.state must be READY"):
        project_asset_source(Asset(state=state))


@pytest.mark.parametrize(
    "asset,message",
    [
        (Asset(asset_id=" asset-1 "), "asset.asset_id"),
        (Asset(owner_user_id=" user-1 "), "asset.owner_user_id"),
    ],
)
def test_ctx_f2c_asset_projection_rejects_noncanonical_authority_ids(asset, message):
    with pytest.raises(ValueError, match=message):
        project_asset_source(asset)


@pytest.mark.parametrize(
    "asset",
    [
        Asset(uri="https://example.invalid/asset-1"),
        Asset(uri="asset://asset-other"),
        Asset(uri=""),
    ],
)
def test_ctx_f2c_asset_projection_requires_exact_canonical_uri(asset):
    with pytest.raises(ValueError, match="asset.uri must exactly match"):
        project_asset_source(asset)


@pytest.mark.parametrize("revision", [-1, True, 1.5, "1"])
def test_ctx_f2c_asset_projection_rejects_invalid_revision(revision):
    with pytest.raises(ValueError, match="asset.revision"):
        project_asset_source(Asset(revision=revision))


@pytest.mark.parametrize("size_bytes", [-1, True, 1.5, "128"])
def test_ctx_f2c_asset_projection_rejects_invalid_size(size_bytes):
    with pytest.raises(ValueError, match="asset.size_bytes"):
        project_asset_source(Asset(size_bytes=size_bytes))


def test_ctx_f2c_asset_projection_allows_unknown_size():
    ref = project_asset_source(Asset(size_bytes=None))
    assert ref.metadata["size_bytes"] is None


def test_ctx_f2c_adapter_has_no_storage_or_runtime_dependency():
    import inspect
    import se.src.context.source_adapters as adapters

    source = inspect.getsource(adapters)
    assert "sqlalchemy" not in source
    assert "infrastructure.storage" not in source
    assert "AssetService" not in source
    assert "object_store" not in source


def test_ctx_f2b_session_projection_rejects_noncanonical_agent_session_shape():
    class AgentSessionLike:
        id = "agent-session-1"
        owner_user_id = "user-1"
        status = "ACTIVE"
        created_at = NOW

    with pytest.raises(ValueError, match="session.user_id is required"):
        project_session_source(AgentSessionLike())


@pytest.mark.parametrize(
    "execution,checkpoint,message",
    [
        (Execution(id=""), Checkpoint(execution_id=""), "execution.id"),
        (
            Execution(session_id=""),
            Checkpoint(session_id=""),
            "execution.session_id",
        ),
    ],
)
def test_ctx_f2b_transcript_projection_rejects_empty_lineage_ids(
    execution,
    checkpoint,
    message,
):
    with pytest.raises(ValueError, match=message):
        project_agent_transcript_source(Session(), execution, checkpoint)


def test_ctx_f2b_trp_projection_rejects_empty_structural_evidence_ids():
    with pytest.raises(ValueError, match="execution.id"):
        project_tool_response_payload_source(
            Session(),
            Execution(id=""),
            Invocation(execution_id=""),
            Result(execution_id=""),
            _payload(execution_id=""),
        )


@pytest.mark.parametrize(
    "session",
    [
        Session(id=" session-1 "),
        Session(user_id=" user-1 "),
    ],
)
def test_ctx_f2b_session_projection_rejects_noncanonical_ids(session):
    with pytest.raises(ValueError, match="canonical normalized form"):
        project_session_source(session)


@pytest.mark.parametrize(
    "task",
    [
        Task(id=" task-1 "),
        Task(session_id=" session-1 "),
    ],
)
def test_ctx_f2b_task_projection_rejects_noncanonical_ids(task):
    with pytest.raises(ValueError, match="canonical normalized form"):
        project_task_source(Session(), task)


@pytest.mark.parametrize(
    "branch",
    [
        Branch(branch_id=" branch-1 "),
        Branch(task_id=" task-1 "),
    ],
)
def test_ctx_f2b_branch_projection_rejects_noncanonical_ids(branch):
    with pytest.raises(ValueError, match="canonical normalized form"):
        project_branch_source(Session(), Task(), branch)


@pytest.mark.parametrize(
    "execution,checkpoint",
    [
        (Execution(id=" exec-1 "), Checkpoint()),
        (Execution(session_id=" session-1 "), Checkpoint(session_id=" session-1 ")),
        (Execution(), Checkpoint(execution_id=" exec-1 ")),
        (Execution(), Checkpoint(session_id=" session-1 ")),
        (Execution(), Checkpoint(transcript_ref=" transcript-ref-1 ")),
    ],
)
def test_ctx_f2b_transcript_projection_rejects_noncanonical_ids(
    execution,
    checkpoint,
):
    with pytest.raises(ValueError, match="canonical normalized form"):
        project_agent_transcript_source(Session(), execution, checkpoint)


@pytest.mark.parametrize(
    "invocation,result,payload_kwargs",
    [
        (Invocation(invocation_id=" inv-1 "), Result(), {}),
        (Invocation(execution_id=" exec-1 "), Result(), {}),
        (Invocation(session_id=" session-1 "), Result(), {}),
        (Invocation(tool_call_id=" call-1 "), Result(), {}),
        (Invocation(capability_id=" cap-1 "), Result(), {}),
        (Invocation(owner_user_id=" user-1 "), Result(), {}),
        (Invocation(), Result(id=" result-1 "), {}),
        (Invocation(), Result(execution_id=" exec-1 "), {}),
        (Invocation(), Result(invocation_id=" inv-1 "), {}),
        (Invocation(), Result(tool_call_id=" call-1 "), {}),
        (Invocation(), Result(capability_id=" cap-1 "), {}),
    ],
)
def test_ctx_f2b_trp_projection_rejects_noncanonical_authority_ids(
    invocation,
    result,
    payload_kwargs,
):
    with pytest.raises(ValueError, match="canonical normalized form"):
        project_tool_response_payload_source(
            Session(),
            Execution(),
            invocation,
            result,
            _payload(**payload_kwargs),
        )


@pytest.mark.parametrize(
    "execution,checkpoint",
    [
        (
            Execution(task_id=" task-1 "),
            Checkpoint(task_id=" task-1 "),
        ),
        (
            Execution(branch_id=" branch-1 "),
            Checkpoint(branch_id=" branch-1 "),
        ),
        (
            Execution(task_id=""),
            Checkpoint(task_id=""),
        ),
        (
            Execution(branch_id=""),
            Checkpoint(branch_id=""),
        ),
    ],
)
def test_ctx_f2b_transcript_projection_rejects_noncanonical_optional_lineage_ids(
    execution,
    checkpoint,
):
    with pytest.raises(ValueError):
        project_agent_transcript_source(Session(), execution, checkpoint)


def test_ctx_f2b_transcript_projection_allows_absent_optional_lineage_ids():
    ref = project_agent_transcript_source(
        Session(),
        Execution(task_id=None, branch_id=None),
        Checkpoint(task_id=None, branch_id=None),
    )
    assert ref.task_id is None
    assert ref.branch_id is None
