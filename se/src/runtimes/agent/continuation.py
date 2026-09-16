from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, replace
from typing import Any, Protocol, Sequence

from .contracts.continuation import (
    CheckpointReason,
    ContinuationBranch,
    ContinuationState,
    ExecutionCheckpoint,
)


class ContinuationPersistence(Protocol):
    async def save_continuation_state(
        self,
        execution_id: str,
        state: dict[str, Any],
    ) -> Any: ...


class ContinuationConflictError(RuntimeError):
    """The branch was based on an execution checkpoint that is no longer current."""


class ContinuationAuthorizationError(PermissionError):
    """The confirming user does not own the continuation branch."""


class AgentContinuationService:
    """Own immutable disconnect checkpoints and explicit continuation merges."""

    def __init__(self, persistence: ContinuationPersistence | None = None) -> None:
        self._persistence = persistence
        self._checkpoints: dict[str, ExecutionCheckpoint] = {}
        self._current: dict[str, str] = {}
        self._branches: dict[str, ContinuationBranch] = {}
        self._merged: dict[str, ExecutionCheckpoint] = {}
        self._lock = asyncio.Lock()

    def current_checkpoint(self, execution_id: str) -> ExecutionCheckpoint | None:
        checkpoint_id = self._current.get(execution_id)
        return self._checkpoints.get(checkpoint_id) if checkpoint_id else None

    def get_branch(self, branch_id: str) -> ContinuationBranch | None:
        return self._branches.get(branch_id)

    async def checkpoint_disconnect(
        self,
        *,
        execution_id: str,
        session_id: str,
        owner_user_id: str,
        connection_id: str,
        invocation_id: str,
        tool_call_id: str,
        capability_id: str,
        iteration: int,
        transcript: Sequence[dict[str, Any]],
        server_continuation_available: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionCheckpoint:
        async with self._lock:
            parent = self.current_checkpoint(execution_id)
            waiting = not server_continuation_available
            checkpoint = ExecutionCheckpoint(
                checkpoint_id=f"checkpoint-{uuid.uuid4().hex}",
                execution_id=execution_id,
                session_id=session_id,
                reason=(
                    CheckpointReason.WAITING_FOR_CONNECTION
                    if waiting
                    else CheckpointReason.CONNECTION_DISCONNECTED
                ),
                state=(
                    ContinuationState.WAITING_FOR_CONNECTION
                    if waiting
                    else ContinuationState.RUNNING
                ),
                parent_checkpoint_id=(
                    parent.checkpoint_id if parent is not None else None
                ),
                origin_connection_id=connection_id,
                current_connection_id=None,
                pending_invocation_id=invocation_id,
                pending_tool_call_id=tool_call_id,
                pending_capability_id=capability_id,
                iteration=iteration,
                transcript=tuple(dict(item) for item in transcript),
                metadata={
                    **dict(metadata or {}),
                    "owner_user_id": owner_user_id,
                },
            )
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._current[execution_id] = checkpoint.checkpoint_id
            await self._persist(execution_id)
            return checkpoint

    async def reconnect(
        self,
        *,
        execution_id: str,
        connection_id: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ContinuationBranch:
        if not connection_id:
            raise ValueError("Reconnect requires a new connection_id.")
        async with self._lock:
            base = self.current_checkpoint(execution_id)
            if base is None or base.state is not ContinuationState.WAITING_FOR_CONNECTION:
                raise ContinuationConflictError(
                    "Execution is not waiting for a connection."
                )
            if connection_id == base.origin_connection_id:
                raise ValueError("Reconnect must use a new connection identity.")
            if not user_id or user_id != base.metadata.get("owner_user_id"):
                raise ContinuationAuthorizationError(
                    "Continuation branch owner does not match execution owner."
                )
            branch = ContinuationBranch(
                branch_id=f"branch-{uuid.uuid4().hex}",
                execution_id=execution_id,
                base_checkpoint_id=base.checkpoint_id,
                connection_id=connection_id,
                owner_user_id=user_id,
                metadata=dict(metadata or {}),
            )
            self._branches[branch.branch_id] = branch
            await self._persist(execution_id)
            return branch

    async def confirm_merge(
        self,
        *,
        execution_id: str,
        branch_id: str,
        user_id: str,
    ) -> ExecutionCheckpoint:
        if not user_id:
            raise ContinuationAuthorizationError("Merge requires user identity.")
        async with self._lock:
            already_merged = self._merged.get(branch_id)
            if already_merged is not None:
                if already_merged.metadata.get("merged_by") != user_id:
                    raise ContinuationAuthorizationError(
                        "Merge confirmation user does not match branch owner."
                    )
                return already_merged

            branch = self._branches.get(branch_id)
            if branch is None or branch.execution_id != execution_id:
                raise KeyError(f"Unknown continuation branch: {branch_id}")
            if branch.owner_user_id != user_id:
                raise ContinuationAuthorizationError(
                    "Merge confirmation user does not match branch owner."
                )
            current = self.current_checkpoint(execution_id)
            if current is None or current.checkpoint_id != branch.base_checkpoint_id:
                raise ContinuationConflictError("Stale continuation branch.")

            checkpoint = ExecutionCheckpoint(
                checkpoint_id=f"checkpoint-{uuid.uuid4().hex}",
                execution_id=execution_id,
                session_id=current.session_id,
                reason=CheckpointReason.READY_TO_MERGE,
                state=ContinuationState.RUNNING,
                parent_checkpoint_id=current.checkpoint_id,
                origin_connection_id=current.origin_connection_id,
                current_connection_id=branch.connection_id,
                iteration=current.iteration,
                transcript=current.transcript,
                metadata={**dict(current.metadata), "merged_by": user_id},
            )
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._current[execution_id] = checkpoint.checkpoint_id
            self._branches[branch_id] = replace(
                branch,
                state=ContinuationState.RUNNING,
            )
            self._merged[branch_id] = checkpoint
            await self._persist(execution_id)
            return checkpoint

    async def _persist(self, execution_id: str) -> None:
        if self._persistence is None:
            return
        checkpoints = {
            key: self._serialize(value)
            for key, value in self._checkpoints.items()
            if value.execution_id == execution_id
        }
        branches = {
            key: self._serialize(value)
            for key, value in self._branches.items()
            if value.execution_id == execution_id
        }
        await self._persistence.save_continuation_state(
            execution_id,
            {
                "current_checkpoint_id": self._current.get(execution_id),
                "checkpoints": checkpoints,
                "branches": branches,
            },
        )

    @staticmethod
    def _serialize(value: Any) -> dict[str, Any]:
        result = asdict(value)
        for key, item in tuple(result.items()):
            if hasattr(item, "value"):
                result[key] = item.value
            elif hasattr(item, "isoformat"):
                result[key] = item.isoformat()
        return result


__all__ = [
    "AgentContinuationService",
    "ContinuationAuthorizationError",
    "ContinuationConflictError",
]
