from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from se.src.context.source_identity import (
    ContextSourceKind,
    ContextSourceRef,
    create_context_source_ref,
    validate_context_source_ref_integrity,
)
from se.src.context.tool_response_payload import (
    COMMITTED_RESULT_STATE,
    ToolResponsePayload,
    validate_tool_response_payload_integrity,
)


def _require_non_empty(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_equal(name: str, left: object, right: object) -> None:
    if left != right:
        raise ValueError(f"{name} mismatch")


@runtime_checkable
class SessionEvidence(Protocol):
    id: str
    user_id: str | None
    status: str
    created_at: datetime


@runtime_checkable
class TaskEvidence(Protocol):
    id: str
    session_id: str
    revision: int
    status: str
    created_at: datetime
    created_by: str


@runtime_checkable
class BranchEvidence(Protocol):
    branch_id: str
    task_id: str
    revision: int
    resolution_state: str
    created_at: datetime
    created_by: str


@runtime_checkable
class ExecutionEvidence(Protocol):
    id: str
    session_id: str
    task_id: str | None
    branch_id: str | None


@runtime_checkable
class CheckpointEvidence(Protocol):
    execution_id: str
    session_id: str
    task_id: str | None
    branch_id: str | None
    transcript_ref: str | None
    transcript_version: int | None
    created_at: datetime


@runtime_checkable
class InvocationEvidence(Protocol):
    invocation_id: str
    execution_id: str | None
    session_id: str | None
    tool_call_id: str | None
    capability_id: str
    owner_user_id: str | None


@runtime_checkable
class ToolResultEvidence(Protocol):
    id: str
    execution_id: str
    invocation_id: str
    tool_call_id: str
    capability_id: str
    commit_state: str
    created_at: datetime


def _session_owner(session: SessionEvidence) -> str:
    _require_non_empty("session.id", session.id)
    return _require_non_empty("session.user_id", session.user_id)


def _finalize(ref: ContextSourceRef) -> ContextSourceRef:
    return validate_context_source_ref_integrity(ref)


def project_session_source(session: SessionEvidence) -> ContextSourceRef:
    owner_user_id = _session_owner(session)
    return _finalize(
        create_context_source_ref(
            source_kind=ContextSourceKind.SESSION,
            authority_id=session.id,
            owner_user_id=owner_user_id,
            session_id=session.id,
            source_created_at=session.created_at,
            source_state=session.status,
        )
    )


def project_task_source(
    session: SessionEvidence,
    task: TaskEvidence,
) -> ContextSourceRef:
    owner_user_id = _session_owner(session)
    _require_equal("task.session_id", task.session_id, session.id)
    return _finalize(
        create_context_source_ref(
            source_kind=ContextSourceKind.TASK,
            authority_id=task.id,
            authority_version=task.revision,
            owner_user_id=owner_user_id,
            session_id=session.id,
            task_id=task.id,
            source_created_at=task.created_at,
            source_state=task.status,
        )
    )


def project_branch_source(
    session: SessionEvidence,
    task: TaskEvidence,
    branch: BranchEvidence,
) -> ContextSourceRef:
    owner_user_id = _session_owner(session)
    _require_equal("branch.task_id", branch.task_id, task.id)
    _require_equal("task.session_id", task.session_id, session.id)
    return _finalize(
        create_context_source_ref(
            source_kind=ContextSourceKind.BRANCH,
            authority_id=branch.branch_id,
            authority_version=branch.revision,
            owner_user_id=owner_user_id,
            session_id=session.id,
            task_id=task.id,
            branch_id=branch.branch_id,
            source_created_at=branch.created_at,
            source_state=branch.resolution_state,
        )
    )


def project_agent_transcript_source(
    session: SessionEvidence,
    execution: ExecutionEvidence,
    checkpoint: CheckpointEvidence,
) -> ContextSourceRef:
    owner_user_id = _session_owner(session)
    _require_equal("checkpoint.execution_id", checkpoint.execution_id, execution.id)
    _require_equal("checkpoint.session_id", checkpoint.session_id, execution.session_id)
    _require_equal("execution.session_id", execution.session_id, session.id)
    _require_equal("checkpoint.task_id", checkpoint.task_id, execution.task_id)
    _require_equal("checkpoint.branch_id", checkpoint.branch_id, execution.branch_id)
    transcript_ref = _require_non_empty("checkpoint.transcript_ref", checkpoint.transcript_ref)
    version = checkpoint.transcript_version
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError("checkpoint.transcript_version must be a nonnegative integer")
    return _finalize(
        create_context_source_ref(
            source_kind=ContextSourceKind.AGENT_TRANSCRIPT,
            authority_id=transcript_ref,
            authority_version=version,
            owner_user_id=owner_user_id,
            session_id=session.id,
            task_id=checkpoint.task_id,
            branch_id=checkpoint.branch_id,
            source_created_at=checkpoint.created_at,
        )
    )


def project_tool_response_payload_source(
    session: SessionEvidence,
    execution: ExecutionEvidence,
    invocation: InvocationEvidence,
    result: ToolResultEvidence,
    payload: ToolResponsePayload,
) -> ContextSourceRef:
    owner_user_id = _session_owner(session)
    validate_tool_response_payload_integrity(payload)

    _require_equal("execution.session_id", execution.session_id, session.id)
    invocation_owner = _require_non_empty(
        "invocation.owner_user_id", invocation.owner_user_id
    )
    _require_equal("invocation.owner_user_id", invocation_owner, owner_user_id)
    _require_equal("invocation.execution_id", invocation.execution_id, execution.id)
    _require_equal("invocation.session_id", invocation.session_id, session.id)

    if result.commit_state != COMMITTED_RESULT_STATE:
        raise ValueError("durable tool result must be COMMITTED")
    _require_equal("result.execution_id", result.execution_id, execution.id)

    _require_equal("payload.source_result_id", payload.source_result_id, result.id)
    _require_equal("payload.invocation_id", payload.invocation_id, result.invocation_id)
    _require_equal("payload.execution_id", payload.execution_id, result.execution_id)
    _require_equal("payload.tool_call_id", payload.tool_call_id, result.tool_call_id)
    _require_equal(
        "payload.logical_capability_id",
        payload.logical_capability_id,
        result.capability_id,
    )

    _require_equal("invocation.invocation_id", invocation.invocation_id, result.invocation_id)
    _require_equal("invocation.tool_call_id", invocation.tool_call_id, result.tool_call_id)
    _require_equal("invocation.capability_id", invocation.capability_id, result.capability_id)

    if payload.owner_user_id is not None:
        _require_equal("payload.owner_user_id", payload.owner_user_id, owner_user_id)
    if payload.session_id is not None:
        _require_equal("payload.session_id", payload.session_id, session.id)

    return _finalize(
        create_context_source_ref(
            source_kind=ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
            authority_id=payload.payload_id,
            owner_user_id=owner_user_id,
            session_id=session.id,
            source_created_at=payload.created_at,
            source_state=COMMITTED_RESULT_STATE,
            metadata={"source_result_id": result.id},
        )
    )


__all__ = [
    "BranchEvidence",
    "CheckpointEvidence",
    "ExecutionEvidence",
    "InvocationEvidence",
    "SessionEvidence",
    "TaskEvidence",
    "ToolResultEvidence",
    "project_agent_transcript_source",
    "project_branch_source",
    "project_session_source",
    "project_task_source",
    "project_tool_response_payload_source",
]
