from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from ..capability.contracts.definition import CapabilityIdempotency
from ..capability.contracts.error import (
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_RESULT_RECONCILIATION_REQUIRED,
)
from ..capability.contracts.implementation import CapabilityImplementationState
from ..capability.contracts.invocation import (
    TERMINAL_INVOCATION_STATES,
    CapabilityInvocation,
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from ..connection.contracts import ConnectionNotFoundError
from ..capability.contracts.reconciliation import RemoteReconciliationStatus
from ..connection.multiplexer import RemoteConnectionLost
from .contracts.inference import InferenceMessage
from .contracts.resume import (
    CheckpointPendingInvocation,
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
)


class ResumePlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ResumePlanRejected(ResumePlanError):
    """The trigger/checkpoint/invocation identity is not eligible to resume."""


class ResumePlanDeferred(ResumePlanError):
    """No authority was acquired; retry may become safe after state changes."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AgentResumePlanningService:
    """R7-D read-only AgentExecution planning with R6 reconciliation.

    Planning may commit newly learned R6 terminal facts and promote the exact
    R7-C AgentToolResult projection. It MUST NOT create/consume ResumeClaim,
    mutate AgentExecution/TaskBudget, bind K2, or execute/replay capabilities.
    """

    _SAFE_REPLAY = frozenset(
        {
            CapabilityIdempotency.IDEMPOTENT,
            CapabilityIdempotency.DEDUPLICATED,
        }
    )
    _ROUTABLE_STATES = frozenset(
        {
            CapabilityImplementationState.ENABLED,
            CapabilityImplementationState.DEGRADED,
        }
    )
    _TASK_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
    _OUTCOME_PROGRESS = {
        None: frozenset(
            {
                RemoteOutcomeState.NOT_DISPATCHED,
                RemoteOutcomeState.IN_FLIGHT,
                RemoteOutcomeState.OUTCOME_UNKNOWN,
                RemoteOutcomeState.TERMINAL_COMMITTED,
                None,
            }
        ),
        RemoteOutcomeState.NOT_DISPATCHED: frozenset(
            {
                RemoteOutcomeState.NOT_DISPATCHED,
                RemoteOutcomeState.IN_FLIGHT,
                RemoteOutcomeState.OUTCOME_UNKNOWN,
                RemoteOutcomeState.TERMINAL_COMMITTED,
            }
        ),
        RemoteOutcomeState.IN_FLIGHT: frozenset(
            {
                RemoteOutcomeState.IN_FLIGHT,
                RemoteOutcomeState.NOT_DISPATCHED,
                RemoteOutcomeState.OUTCOME_UNKNOWN,
                RemoteOutcomeState.TERMINAL_COMMITTED,
            }
        ),
        RemoteOutcomeState.OUTCOME_UNKNOWN: frozenset(
            {
                RemoteOutcomeState.OUTCOME_UNKNOWN,
                RemoteOutcomeState.TERMINAL_COMMITTED,
            }
        ),
        RemoteOutcomeState.TERMINAL_COMMITTED: frozenset(
            {RemoteOutcomeState.TERMINAL_COMMITTED}
        ),
    }

    def __init__(
        self,
        durable_store,
        capability_runtime,
        *,
        now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = durable_store
        self._capabilities = capability_runtime
        self._now_utc = now_utc or (lambda: datetime.now(timezone.utc))

    async def build_resume_plan(
        self,
        execution_id: str,
        checkpoint_id: str,
        *,
        target_user_id: str,
        target_client_id: str | None,
        target_connection_id: str,
    ) -> ResumePlan:
        self._validate_target_connection(
            target_user_id=target_user_id,
            target_client_id=target_client_id,
            target_connection_id=target_connection_id,
        )

        execution = await self._store.load_execution(execution_id)
        if execution is None:
            raise ResumePlanRejected(
                "RESUME_EXECUTION_NOT_FOUND",
                f"Unknown AgentExecution: {execution_id}",
            )
        if str(execution.state) != "WAITING":
            raise ResumePlanRejected(
                "RESUME_NOT_WAITING",
                f"AgentExecution {execution_id} is not WAITING.",
            )
        if getattr(execution, "current_checkpoint_id", None) != checkpoint_id:
            raise ResumePlanRejected(
                "STALE_CHECKPOINT",
                "Requested checkpoint is not AgentExecution.current_checkpoint_id.",
            )

        checkpoint = await self._store.load_current_checkpoint(execution_id)
        if checkpoint is None or checkpoint.checkpoint_id != checkpoint_id:
            raise ResumePlanRejected(
                "STALE_CHECKPOINT",
                "Normalized current checkpoint is missing or stale.",
            )
        if checkpoint.execution_revision != execution.revision:
            raise ResumePlanRejected(
                "STALE_CHECKPOINT",
                "Checkpoint revision does not match AgentExecution revision.",
            )
        if checkpoint.wait_reason != "CONNECTION":
            raise ResumePlanRejected(
                "UNSUPPORTED_RESUME_TRIGGER",
                f"R7-D connection preflight cannot resume {checkpoint.wait_reason!r}.",
            )
        if (
            checkpoint.origin_connection_id
            and checkpoint.origin_connection_id == target_connection_id
        ):
            raise ResumePlanRejected(
                "RESUME_CONNECTION_NOT_NEW",
                "Reconnect must use a new connection generation.",
            )
        if (
            checkpoint.origin_client_id
            and checkpoint.origin_client_id != target_client_id
        ):
            raise ResumePlanRejected(
                "FOREIGN_CLIENT",
                "Resume client does not match checkpoint origin client.",
            )

        if execution.task_id:
            task = await self._store.load_task(execution.task_id)
            if task is None:
                raise ResumePlanRejected(
                    "RESUME_TASK_NOT_FOUND",
                    f"Unknown AgentTask: {execution.task_id}",
                )
            if task.session_id != execution.session_id:
                raise ResumePlanRejected(
                    "TASK_SEMANTIC_CONFLICT",
                    "AgentTask session does not match AgentExecution.",
                )
            if task.created_by != target_user_id:
                raise ResumePlanRejected(
                    "FOREIGN_PRINCIPAL",
                    "AgentTask owner does not match resume principal.",
                )
            task_status = getattr(task.status, "value", task.status)
            if str(task_status) in self._TASK_TERMINAL_STATES:
                raise ResumePlanRejected(
                    "TASK_TERMINAL",
                    f"AgentTask {execution.task_id} is already {task_status}.",
                )

        remaining = checkpoint.remaining_active_budget_seconds
        if remaining is None or not math.isfinite(float(remaining)):
            raise ResumePlanRejected(
                "UNKNOWN_ACTIVE_BUDGET",
                "Checkpoint has no trusted finite active budget.",
            )
        remaining = float(remaining)
        if remaining <= 0.0:
            raise ResumePlanDeferred(
                "AGENT_EXECUTION_TIMEOUT",
                "No active execution budget remains.",
            )
        wait_expires_at = _utc(checkpoint.wait_expires_at)
        if wait_expires_at is not None and _utc(self._now_utc()) >= wait_expires_at:
            raise ResumePlanDeferred(
                "WAIT_TTL_EXPIRED",
                "WAITING checkpoint TTL has expired.",
            )

        iteration = await self._store.load_iteration(
            execution_id,
            iteration_number=checkpoint.iteration,
        )
        if iteration is None:
            raise ResumePlanRejected(
                "CHECKPOINT_ITERATION_MISSING",
                "Checkpoint iteration is absent from durable history.",
            )
        ordered_tool_call_ids = tuple(
            getattr(iteration, "tool_call_ids", None) or ()
        )
        if not ordered_tool_call_ids:
            raise ResumePlanRejected(
                "CHECKPOINT_TOOL_ORDER_MISSING",
                "Checkpoint iteration has no canonical tool_call_ids.",
            )

        pending = await self._store.load_checkpoint_pending_invocations(
            checkpoint_id
        )
        if not pending:
            raise ResumePlanRejected(
                "CHECKPOINT_PENDING_INVOCATIONS_MISSING",
                "Connection WAITING checkpoint has no pending invocation snapshots.",
            )
        self._validate_pending_order(pending, ordered_tool_call_ids)

        transcript_raw = await self._store.load_committed_checkpoint_transcript(
            execution_id,
            checkpoint_id,
            active_tool_call_ids=ordered_tool_call_ids,
        )
        transcript = tuple(
            InferenceMessage.model_validate(item)
            for item in transcript_raw
        )

        actions: list[ResumeInvocationAction] = []
        for snapshot in pending:
            invocation = await self._load_invocation(snapshot.invocation_id)
            self._validate_semantic_identity(
                execution_id,
                snapshot,
                invocation,
                target_user_id=target_user_id,
                target_client_id=target_client_id,
            )
            action = await self._classify(
                execution_id,
                snapshot,
                invocation,
                target_user_id=target_user_id,
                target_client_id=target_client_id,
                target_connection_id=target_connection_id,
            )
            if action.action is not ResumeInvocationActionKind.REUSE_COMMITTED:
                self._require_target_capability(action, target_connection_id)
            actions.append(action)

        state = dict(getattr(execution, "context_state", None) or {})
        metadata = dict(checkpoint.metadata or {})
        plan_values: dict[str, Any] = {
            "execution_id": execution.id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "expected_execution_revision": execution.revision,
            "agent_id": execution.agent_id,
            "session_id": execution.session_id,
            "task_id": execution.task_id,
            "branch_id": execution.branch_id,
            "parent_execution_id": execution.parent_execution_id,
            "retry_of_execution_id": execution.retry_of_execution_id,
            "base_execution_id": execution.base_execution_id,
            "base_checkpoint_id": execution.base_checkpoint_id,
            "correlation_id": execution.correlation_id,
            "trace_id": metadata.get("trace_id") or state.get("trace_id"),
            "request_id": metadata.get("request_id") or state.get("request_id"),
            "iteration": checkpoint.iteration,
            "ordered_tool_call_ids": ordered_tool_call_ids,
            "transcript_snapshot": transcript,
            "remaining_active_budget_seconds": remaining,
            "wait_expires_at": wait_expires_at,
            "target_user_id": target_user_id,
            "target_client_id": target_client_id,
            "target_connection_id": target_connection_id,
            "invocation_actions": tuple(actions),
        }
        fingerprint = self._fingerprint(plan_values)
        return ResumePlan(
            **plan_values,
            plan_fingerprint=fingerprint,
        )

    @staticmethod
    def _validate_pending_order(
        pending: tuple[CheckpointPendingInvocation, ...],
        ordered_tool_call_ids: tuple[str, ...],
    ) -> None:
        previous = -1
        for item in pending:
            if item.ordinal <= previous:
                raise ResumePlanRejected(
                    "CHECKPOINT_PENDING_ORDER_INVALID",
                    "Pending invocation ordinals are not strictly increasing.",
                )
            previous = item.ordinal
            if item.ordinal >= len(ordered_tool_call_ids):
                raise ResumePlanRejected(
                    "CHECKPOINT_PENDING_ORDER_INVALID",
                    "Pending invocation ordinal is outside the tool batch.",
                )
            if ordered_tool_call_ids[item.ordinal] != item.tool_call_id:
                raise ResumePlanRejected(
                    "CHECKPOINT_PENDING_ORDER_INVALID",
                    "Pending invocation ordinal disagrees with tool_call_ids.",
                )

    def _validate_target_connection(
        self,
        *,
        target_user_id: str,
        target_client_id: str | None,
        target_connection_id: str,
    ) -> None:
        registry = getattr(self._capabilities, "connection_registry", None)
        if registry is None:
            raise ResumePlanRejected(
                "CONNECTION_REGISTRY_UNAVAILABLE",
                "Connection authority is unavailable during resume planning.",
            )
        try:
            snapshot = registry.get(target_connection_id)
        except ConnectionNotFoundError as exc:
            raise ResumePlanDeferred(
                "RESUME_CONNECTION_UNAVAILABLE",
                "Target resume connection is no longer registered.",
            ) from exc
        if not snapshot.is_usable:
            raise ResumePlanDeferred(
                "RESUME_CONNECTION_UNAVAILABLE",
                "Target resume connection is not ACTIVE/usable.",
            )
        if snapshot.user_id != target_user_id:
            raise ResumePlanRejected(
                "FOREIGN_PRINCIPAL",
                "Target resume connection belongs to another principal.",
            )
        connection_client_id = str(snapshot.metadata.get("client_id") or "")
        if target_client_id and connection_client_id != target_client_id:
            raise ResumePlanRejected(
                "FOREIGN_CLIENT",
                "Target connection stable client identity changed.",
            )

    async def _load_invocation(self, invocation_id: str) -> CapabilityInvocation:
        lifecycle = getattr(self._capabilities, "invocation_lifecycle", None)
        store = getattr(lifecycle, "store", None)
        if store is None:
            raise ResumePlanRejected(
                "R6_INVOCATION_STORE_UNAVAILABLE",
                "R6 invocation authority is unavailable.",
            )
        invocation = await store.get(invocation_id)
        if invocation is None:
            raise ResumePlanRejected(
                "R6_INVOCATION_MISSING",
                f"Unknown CapabilityInvocation: {invocation_id}",
            )
        return invocation

    @staticmethod
    def _validate_semantic_identity(
        execution_id: str,
        snapshot: CheckpointPendingInvocation,
        invocation: CapabilityInvocation,
        *,
        target_user_id: str,
        target_client_id: str | None,
    ) -> None:
        if invocation.revision < snapshot.invocation_revision:
            raise ResumePlanRejected(
                "INVOCATION_REVISION_REGRESSION",
                "CapabilityInvocation revision is older than checkpoint watermark.",
            )

        observed = (
            RemoteOutcomeState(snapshot.observed_remote_outcome_state)
            if snapshot.observed_remote_outcome_state is not None
            else None
        )
        allowed = AgentResumePlanningService._OUTCOME_PROGRESS[observed]
        if invocation.remote_outcome_state not in allowed:
            raise ResumePlanRejected(
                "REMOTE_OUTCOME_REGRESSION",
                "CapabilityInvocation remote outcome regressed behind checkpoint watermark.",
            )

        expected = {
            "execution_id": execution_id,
            "tool_call_id": snapshot.tool_call_id,
            "capability_id": snapshot.capability_id,
            "capability_version": snapshot.capability_version,
            "request_fingerprint": snapshot.request_fingerprint,
        }
        for field, value in expected.items():
            if getattr(invocation, field) != value:
                raise ResumePlanRejected(
                    "INVOCATION_SEMANTIC_CONFLICT",
                    f"CapabilityInvocation {field} changed since checkpoint.",
                )
        if invocation.idempotency.value != snapshot.idempotency:
            raise ResumePlanRejected(
                "INVOCATION_SEMANTIC_CONFLICT",
                "CapabilityInvocation idempotency changed since checkpoint.",
            )
        if invocation.owner_user_id != target_user_id:
            raise ResumePlanRejected(
                "FOREIGN_PRINCIPAL",
                "CapabilityInvocation owner does not match resume principal.",
            )
        if (
            invocation.origin_client_id
            and invocation.origin_client_id != target_client_id
        ):
            raise ResumePlanRejected(
                "FOREIGN_CLIENT",
                "CapabilityInvocation origin client differs from resume client.",
            )
        if (
            snapshot.origin_client_id
            and snapshot.origin_client_id != target_client_id
        ):
            raise ResumePlanRejected(
                "FOREIGN_CLIENT",
                "Checkpoint invocation client differs from resume client.",
            )

    async def _classify(
        self,
        execution_id: str,
        snapshot: CheckpointPendingInvocation,
        invocation: CapabilityInvocation,
        *,
        target_user_id: str,
        target_client_id: str | None,
        target_connection_id: str,
    ) -> ResumeInvocationAction:
        outcome = invocation.remote_outcome_state
        if outcome is RemoteOutcomeState.TERMINAL_COMMITTED:
            await self._require_committed_projection(
                execution_id,
                snapshot,
                invocation,
            )
            return self._action(
                snapshot,
                invocation,
                ResumeInvocationActionKind.REUSE_COMMITTED,
            )

        if outcome is RemoteOutcomeState.NOT_DISPATCHED:
            return self._action(
                snapshot,
                invocation,
                ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
            )

        if outcome not in {
            RemoteOutcomeState.IN_FLIGHT,
            RemoteOutcomeState.OUTCOME_UNKNOWN,
        }:
            raise ResumePlanRejected(
                "REMOTE_OUTCOME_INVALID",
                "Checkpointed remote invocation has no safe R6 outcome state.",
            )

        try:
            result = await self._capabilities.reconcile_remote_invocation(
                invocation.invocation_id,
                target_connection_id,
            )
        except CapabilityError as exc:
            if exc.code in {
                REMOTE_INVOCATION_CONFLICT,
                REMOTE_RESULT_RECONCILIATION_REQUIRED,
                "CAPABILITY_UNAUTHORIZED",
            }:
                raise ResumePlanRejected(exc.code, str(exc)) from exc
            raise
        except (RemoteConnectionLost, asyncio.TimeoutError) as exc:
            raise ResumePlanDeferred(
                "RECONCILIATION_UNAVAILABLE",
                "Remote reconciliation could not complete on the target connection.",
            ) from exc

        current = await self._load_invocation(invocation.invocation_id)
        self._validate_semantic_identity(
            execution_id,
            snapshot,
            current,
            target_user_id=target_user_id,
            target_client_id=target_client_id,
        )

        if result.status is RemoteReconciliationStatus.TERMINAL:
            if current.remote_outcome_state is not RemoteOutcomeState.TERMINAL_COMMITTED:
                raise ResumePlanRejected(
                    "RECONCILIATION_TERMINAL_NOT_COMMITTED",
                    "R6 terminal reconciliation did not commit durable authority.",
                )
            await self._require_committed_projection(
                execution_id,
                snapshot,
                current,
            )
            return self._action(
                snapshot,
                current,
                ResumeInvocationActionKind.REUSE_COMMITTED,
            )
        if result.status is RemoteReconciliationStatus.RUNNING:
            raise ResumePlanDeferred(
                "REMOTE_INVOCATION_RUNNING",
                "Remote invocation is still running.",
            )
        if result.status is RemoteReconciliationStatus.CONFLICT:
            raise ResumePlanRejected(
                "REMOTE_INVOCATION_CONFLICT",
                "Remote reconciliation reported semantic conflict.",
            )
        if result.status in {
            RemoteReconciliationStatus.UNKNOWN,
            RemoteReconciliationStatus.NOT_FOUND,
        }:
            if current.idempotency in self._SAFE_REPLAY:
                return self._action(
                    snapshot,
                    current,
                    ResumeInvocationActionKind.REPLAY_SAFE,
                )
            raise ResumePlanDeferred(
                "REMOTE_OUTCOME_UNSAFE_TO_REPLAY",
                "Remote outcome remains ambiguous and invocation is not replay-safe.",
            )

        raise ResumePlanRejected(
            "RECONCILIATION_STATUS_UNSUPPORTED",
            f"Unsupported reconciliation status: {result.status}",
        )

    async def _require_committed_projection(
        self,
        execution_id: str,
        snapshot: CheckpointPendingInvocation,
        invocation: CapabilityInvocation,
    ) -> None:
        if (
            invocation.remote_outcome_state
            is not RemoteOutcomeState.TERMINAL_COMMITTED
            or invocation.state not in TERMINAL_INVOCATION_STATES
        ):
            raise ResumePlanRejected(
                "TERMINAL_INVOCATION_AUTHORITY_INVALID",
                "REUSE_COMMITTED requires terminal R6 lifecycle and outcome authority.",
            )

        result = await self._store.load_committed_tool_result(
            execution_id,
            snapshot.tool_call_id,
        )
        if result is None:
            raise ResumePlanRejected(
                "COMMITTED_TOOL_RESULT_MISSING",
                "Terminal R6 authority has no committed AgentToolResult projection.",
            )

        identity = {
            "execution_id": execution_id,
            "tool_call_id": snapshot.tool_call_id,
            "invocation_id": invocation.invocation_id,
            "capability_id": invocation.capability_id,
        }
        for field, expected in identity.items():
            if getattr(result, field, None) != expected:
                raise ResumePlanRejected(
                    "COMMITTED_TOOL_RESULT_CONFLICT",
                    f"Committed AgentToolResult {field} does not match R6 authority.",
                )

        error = dict(invocation.error or {})
        succeeded = invocation.state is CapabilityInvocationState.COMPLETED
        projection = {
            "success": succeeded,
            "output": invocation.output,
            "error_code": None if succeeded else (
                error.get("error_code") or error.get("code")
            ),
            "error_message": None if succeeded else (
                error.get("error_message") or error.get("message")
            ),
            "retryable": False if succeeded else bool(
                error.get("retryable", False)
            ),
        }
        for field, expected in projection.items():
            if getattr(result, field, None) != expected:
                raise ResumePlanRejected(
                    "COMMITTED_TOOL_RESULT_CONFLICT",
                    f"Committed AgentToolResult {field} differs from terminal R6 projection.",
                )

    def _require_target_capability(
        self,
        action: ResumeInvocationAction,
        target_connection_id: str,
    ) -> None:
        catalog = getattr(self._capabilities, "catalog", None)
        if catalog is None:
            raise ResumePlanDeferred(
                "PENDING_CAPABILITY_NOT_READY",
                "Capability catalog is unavailable.",
            )
        implementations = catalog.list_implementations_for_connection(
            target_connection_id
        )
        ready = any(
            item.capability_id == action.capability_id
            and item.version == action.capability_version
            and item.state in self._ROUTABLE_STATES
            for item in implementations
        )
        if not ready:
            raise ResumePlanDeferred(
                "PENDING_CAPABILITY_NOT_READY",
                f"Capability {action.capability_id!r} is not ready on target connection.",
            )

    @staticmethod
    def _action(
        snapshot: CheckpointPendingInvocation,
        invocation: CapabilityInvocation,
        kind: ResumeInvocationActionKind,
    ) -> ResumeInvocationAction:
        if not invocation.capability_version or not invocation.request_fingerprint:
            raise ResumePlanRejected(
                "INVOCATION_RECONCILIATION_SNAPSHOT_MISSING",
                "Invocation lacks version/fingerprint required by R7-D.",
            )
        return ResumeInvocationAction(
            invocation_id=invocation.invocation_id,
            tool_call_id=snapshot.tool_call_id,
            ordinal=snapshot.ordinal,
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            request_fingerprint=invocation.request_fingerprint,
            idempotency=invocation.idempotency,
            expected_invocation_revision=invocation.revision,
            expected_invocation_state=invocation.state,
            expected_remote_outcome_state=invocation.remote_outcome_state,
            action=kind,
        )

    @staticmethod
    def _fingerprint(values: dict[str, Any]) -> str:
        actions = [
            {
                "invocation_id": item.invocation_id,
                "tool_call_id": item.tool_call_id,
                "ordinal": item.ordinal,
                "capability_id": item.capability_id,
                "capability_version": item.capability_version,
                "request_fingerprint": item.request_fingerprint,
                "idempotency": item.idempotency.value,
                "expected_invocation_revision": item.expected_invocation_revision,
                "expected_invocation_state": item.expected_invocation_state.value,
                "expected_remote_outcome_state": (
                    item.expected_remote_outcome_state.value
                    if item.expected_remote_outcome_state is not None
                    else None
                ),
                "action": item.action.value,
            }
            for item in values["invocation_actions"]
        ]
        payload = {
            "execution_id": values["execution_id"],
            "checkpoint_id": values["checkpoint_id"],
            "expected_execution_revision": values["expected_execution_revision"],
            "agent_id": values["agent_id"],
            "session_id": values["session_id"],
            "task_id": values["task_id"],
            "branch_id": values["branch_id"],
            "parent_execution_id": values["parent_execution_id"],
            "retry_of_execution_id": values["retry_of_execution_id"],
            "base_execution_id": values["base_execution_id"],
            "base_checkpoint_id": values["base_checkpoint_id"],
            "correlation_id": values["correlation_id"],
            "trace_id": values["trace_id"],
            "request_id": values["request_id"],
            "iteration": values["iteration"],
            "ordered_tool_call_ids": list(values["ordered_tool_call_ids"]),
            "transcript_snapshot": [
                item.model_dump(mode="json")
                for item in values["transcript_snapshot"]
            ],
            "remaining_active_budget_seconds": values[
                "remaining_active_budget_seconds"
            ],
            "wait_expires_at": (
                values["wait_expires_at"].isoformat()
                if values["wait_expires_at"] is not None
                else None
            ),
            "target_user_id": values["target_user_id"],
            "target_client_id": values["target_client_id"],
            "target_connection_id": values["target_connection_id"],
            "invocation_actions": actions,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AgentResumePlanningService",
    "ResumePlanDeferred",
    "ResumePlanError",
    "ResumePlanRejected",
]
