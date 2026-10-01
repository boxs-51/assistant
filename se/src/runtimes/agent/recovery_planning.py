from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from ..capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from ..capability.contracts.error import CapabilityError
from ..capability.contracts.invocation import (
    TERMINAL_INVOCATION_STATES,
    CapabilityInvocation,
    CapabilityInvocationState,
    CapabilityWaitReason,
    RemoteOutcomeState,
)
from .contracts.inference import InferenceMessage
from .contracts.recovery import (
    RecoveryContinuationAuthority,
    RecoveryInferenceDisposition,
    RecoveryInvocationAction,
    RecoveryPlan,
    RecoveryToolQuotaAuthority,
    recovery_plan_fingerprint,
)
from .contracts.resume import ResumeInvocationActionKind, ResumeTriggerType
from .safe_point_reconstruction import (
    SafePointReconstructionError,
    reconstruct_r7c_safe_point_in_uow,
    recovery_safe_point_fingerprint,
)


class RecoveryPlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RecoveryPlanRejected(RecoveryPlanError):
    """Durable recovery authority is inconsistent or semantically invalid."""


class RecoveryPlanDeferred(RecoveryPlanError):
    """No authority was acquired; later state may make planning executable."""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AgentRecoveryPlanningService:
    """R12-F1 read-only SERVER_RECOVERY plan construction.

    This service owns no durable mutation. It does not create/consume ResumeClaim,
    activate RUNNING, mutate leases/TaskBudget/UBQ, reconcile remote invocations,
    create attempts, or dispatch capability/provider work.
    """

    _TASK_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

    def __init__(
        self,
        uow_factory,
        capability_runtime,
        *,
        tool_quota_service=None,
        now_utc: Callable[[], datetime] | None = None,
        safe_point_reconstructor=None,
    ) -> None:
        self._uow_factory = uow_factory
        self._capabilities = capability_runtime
        self._tool_quota = (
            tool_quota_service
            if tool_quota_service is not None
            else getattr(capability_runtime, "tool_quota_service", None)
        )
        self._now_utc = now_utc or (lambda: datetime.now(timezone.utc))
        self._safe_point_reconstructor = (
            safe_point_reconstructor or reconstruct_r7c_safe_point_in_uow
        )

    async def build_recovery_plan(
        self,
        execution_id: str,
        *,
        target_connection_id: str | None = None,
    ) -> RecoveryPlan:
        if not execution_id:
            raise RecoveryPlanRejected(
                "RECOVERY_EXECUTION_ID_REQUIRED",
                "Recovery planning requires an execution id.",
            )

        async with self._uow_factory() as uow:
            execution = await uow.agents.get_execution(execution_id)
            if execution is None:
                raise RecoveryPlanRejected(
                    "RECOVERY_EXECUTION_NOT_FOUND",
                    f"Unknown AgentExecution: {execution_id}",
                )
            self._validate_recovery_execution(execution)

            checkpoint_id = str(execution.current_checkpoint_id or "")
            checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
            if checkpoint is None:
                raise RecoveryPlanRejected(
                    "RECOVERY_CHECKPOINT_MISSING",
                    "Current RECOVERY checkpoint does not exist.",
                )
            metadata = self._validate_recovery_checkpoint(
                execution,
                checkpoint,
            )

            principal = await self._resolve_principal_in_uow(
                uow,
                execution,
            )
            task, branch, budget = await self._load_lineage_in_uow(
                uow,
                execution,
                principal,
            )

            try:
                safe_point = await self._safe_point_reconstructor(
                    uow,
                    execution,
                    require_pending_invocation_authority=True,
                )
            except SafePointReconstructionError as exc:
                raise RecoveryPlanRejected(
                    self._safe_point_code(exc),
                    str(exc),
                ) from exc

            if (
                safe_point.checkpoint_id != checkpoint.checkpoint_id
                or safe_point.checkpoint_revision
                != checkpoint.execution_revision
                or safe_point.iteration_number != checkpoint.iteration
            ):
                raise RecoveryPlanRejected(
                    "RECOVERY_SAFE_POINT_CONFLICT",
                    "Reconstructed safe point differs from current RECOVERY checkpoint.",
                )

            await self._reject_unproven_later_iteration_in_uow(
                uow,
                execution,
                checkpoint,
            )

            (
                recovery_iteration_id,
                inference_request_id,
                inference_disposition,
            ) = await self._freeze_inference_identity_in_uow(
                uow,
                execution,
                checkpoint,
                safe_point,
                metadata,
            )

            remaining = checkpoint.remaining_active_budget_seconds
            if remaining is None or not math.isfinite(float(remaining)):
                raise RecoveryPlanRejected(
                    "RECOVERY_ACTIVE_BUDGET_UNKNOWN",
                    "RECOVERY checkpoint lacks a trusted finite active budget.",
                )
            remaining = float(remaining)
            if remaining <= 0.0:
                raise RecoveryPlanDeferred(
                    "AGENT_EXECUTION_TIMEOUT",
                    "No active execution budget remains.",
                )
            wait_expires_at = _utc(checkpoint.wait_expires_at)
            if (
                wait_expires_at is not None
                and _utc(self._now_utc()) >= wait_expires_at
            ):
                raise RecoveryPlanDeferred(
                    "WAIT_TTL_EXPIRED",
                    "RECOVERY checkpoint TTL has expired.",
                )

            pending_rows = tuple(
                await uow.agents.list_checkpoint_pending_invocations(
                    checkpoint.checkpoint_id
                )
            )
            self._validate_pending_rows(
                pending_rows,
                tuple(
                    str(item.tool_call_id)
                    for item in safe_point.ordered_tool_calls
                ),
                safe_point.ordered_pending_invocations,
            )

            actions: list[RecoveryInvocationAction] = []
            target_client_id: str | None = None
            for snapshot in pending_rows:
                invocation_record = await uow.capability_invocations.get_record(
                    str(snapshot.invocation_id)
                )
                if invocation_record is None:
                    raise RecoveryPlanRejected(
                        "RECOVERY_INVOCATION_MISSING",
                        f"Missing CapabilityInvocation {snapshot.invocation_id!r}.",
                    )
                invocation = self._invocation_from_record(invocation_record)
                self._validate_invocation_identity(
                    execution,
                    checkpoint,
                    snapshot,
                    invocation,
                    principal=principal,
                )

                action = await self._classify_action_in_uow(
                    uow,
                    execution,
                    snapshot,
                    invocation,
                    principal=principal,
                    target_connection_id=target_connection_id,
                )
                if action.continuation_authority is not None:
                    action_client_id = (
                        action.continuation_authority.target_client_id
                    )
                    if (
                        target_client_id is not None
                        and target_client_id != action_client_id
                    ):
                        raise RecoveryPlanRejected(
                            "RECOVERY_MULTIPLE_STABLE_CLIENTS",
                            "One recovery plan cannot target multiple stable clients.",
                        )
                    target_client_id = action_client_id
                actions.append(action)

            ordered_tool_call_ids = tuple(
                str(item.tool_call_id)
                for item in safe_point.ordered_tool_calls
            )
            transcript = tuple(
                InferenceMessage.model_validate(item)
                for item in safe_point.transcript_snapshot
            )

            plan_values: dict[str, Any] = {
                "execution_id": str(execution.id),
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "expected_execution_revision": int(execution.revision),
                "recovery_fingerprint": str(
                    metadata["r12_recovery_fingerprint"]
                ),
                "expected_unowned_lease_generation": int(
                    execution.lease_generation
                ),
                "checkpoint_origin_client_id": checkpoint.origin_client_id,
                "checkpoint_origin_connection_id": checkpoint.origin_connection_id,
                "agent_id": str(execution.agent_id),
                "session_id": str(execution.session_id),
                "task_id": (
                    str(execution.task_id)
                    if execution.task_id is not None
                    else None
                ),
                "task_revision": (
                    int(task.revision) if task is not None else None
                ),
                "branch_id": (
                    str(execution.branch_id)
                    if execution.branch_id is not None
                    else None
                ),
                "branch_revision": (
                    int(branch.revision) if branch is not None else None
                ),
                "parent_execution_id": execution.parent_execution_id,
                "retry_of_execution_id": execution.retry_of_execution_id,
                "base_execution_id": execution.base_execution_id,
                "base_checkpoint_id": execution.base_checkpoint_id,
                "resolved_recovery_principal": principal,
                "iteration": int(checkpoint.iteration),
                "recovery_iteration_id": recovery_iteration_id,
                "inference_request_id": inference_request_id,
                "inference_disposition": inference_disposition,
                "ordered_tool_call_ids": ordered_tool_call_ids,
                "transcript_snapshot": transcript,
                "remaining_active_budget_seconds": remaining,
                "wait_expires_at": wait_expires_at,
                "task_budget_incarnation_generation": (
                    int(budget.incarnation_generation)
                    if budget is not None
                    else None
                ),
                "task_budget_revision": (
                    int(budget.revision) if budget is not None else None
                ),
                "target_trigger": ResumeTriggerType.SERVER_RECOVERY,
                "target_client_id": target_client_id,
                "target_connection_id": (
                    target_connection_id
                    if any(
                        item.continuation_authority is not None
                        for item in actions
                    )
                    else None
                ),
                "invocation_actions": tuple(actions),
            }
            fingerprint = recovery_plan_fingerprint(plan_values)
            return RecoveryPlan(
                **plan_values,
                plan_fingerprint=fingerprint,
            )

    @staticmethod
    def _safe_point_code(exc: SafePointReconstructionError) -> str:
        text = str(exc)
        if ":" in text:
            code = text.split(":", 1)[0].strip()
            if code:
                return code
        return "RECOVERY_SAFE_POINT_INVALID"

    @staticmethod
    def _validate_recovery_execution(execution) -> None:
        if str(execution.state) != "WAITING":
            raise RecoveryPlanRejected(
                "RECOVERY_NOT_WAITING",
                "SERVER_RECOVERY planning requires WAITING execution state.",
            )
        if str(execution.wait_reason or "") != "RECOVERY":
            raise RecoveryPlanRejected(
                "RECOVERY_WAIT_REASON_REQUIRED",
                "SERVER_RECOVERY planning requires wait_reason=RECOVERY.",
            )
        if not str(execution.current_checkpoint_id or ""):
            raise RecoveryPlanRejected(
                "RECOVERY_CHECKPOINT_REQUIRED",
                "WAITING(RECOVERY) execution lacks current checkpoint authority.",
            )
        if (
            execution.owner_instance_id is not None
            or execution.lease_expires_at is not None
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_EXECUTION_STILL_OWNED",
                "R12-E recovery safe point must be durably unowned.",
            )
        if int(execution.lease_generation) <= 0:
            raise RecoveryPlanRejected(
                "RECOVERY_LEASE_GENERATION_INVALID",
                "R12-E recovery safe point must carry a positive fencing generation.",
            )

    @staticmethod
    def _validate_recovery_checkpoint(execution, checkpoint) -> dict[str, Any]:
        if (
            str(checkpoint.execution_id) != str(execution.id)
            or str(checkpoint.checkpoint_id)
            != str(execution.current_checkpoint_id)
            or int(checkpoint.execution_revision) != int(execution.revision)
            or str(checkpoint.session_id) != str(execution.session_id)
            or checkpoint.task_id != execution.task_id
            or checkpoint.branch_id != execution.branch_id
            or str(checkpoint.wait_reason) != "RECOVERY"
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_CHECKPOINT_LINEAGE_CONFLICT",
                "Current RECOVERY checkpoint differs from execution lineage.",
            )

        metadata = dict(checkpoint.metadata_json or {})
        fingerprint = str(metadata.get("r12_recovery_fingerprint") or "")
        receipt = metadata.get("r12_recovery_receipt")
        if not fingerprint or not isinstance(receipt, dict):
            raise RecoveryPlanRejected(
                "RECOVERY_FINGERPRINT_MISSING",
                "RECOVERY checkpoint lacks R12-E fingerprint/receipt authority.",
            )
        if recovery_safe_point_fingerprint(dict(receipt)) != fingerprint:
            raise RecoveryPlanRejected(
                "RECOVERY_FINGERPRINT_CONFLICT",
                "RECOVERY checkpoint fingerprint does not match its receipt.",
            )
        if (
            str(receipt.get("execution_id") or "") != str(execution.id)
            or int(receipt.get("target_revision", -1))
            != int(execution.revision)
            or str(receipt.get("checkpoint_id") or "")
            != str(checkpoint.checkpoint_id)
            or str(receipt.get("target_state") or "") != "WAITING"
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_RECEIPT_LINEAGE_CONFLICT",
                "R12-E recovery receipt differs from current checkpoint authority.",
            )
        return metadata

    async def _resolve_principal_in_uow(self, uow, execution) -> str:
        session = await uow.agents.get_session(str(execution.session_id))
        principal = (
            str(getattr(session, "owner_user_id", "") or "")
            if session is not None
            else ""
        )
        if not principal:
            raise RecoveryPlanRejected(
                "RECOVERY_PRINCIPAL_UNRESOLVED",
                "Durable AgentSession owner is missing.",
            )
        return principal

    async def _load_lineage_in_uow(
        self,
        uow,
        execution,
        principal: str,
    ):
        task = None
        budget = None
        branch = None
        if execution.task_id is not None:
            task = await uow.agents.get_task(str(execution.task_id))
            if task is None:
                raise RecoveryPlanRejected(
                    "RECOVERY_TASK_MISSING",
                    "Task-scoped execution has no durable AgentTask.",
                )
            if (
                str(task.session_id) != str(execution.session_id)
                or str(task.created_by) != principal
            ):
                raise RecoveryPlanRejected(
                    "RECOVERY_TASK_PRINCIPAL_CONFLICT",
                    "AgentTask session/owner differs from recovery principal.",
                )
            if str(task.status) in self._TASK_TERMINAL_STATES:
                raise RecoveryPlanRejected(
                    "RECOVERY_TASK_TERMINAL",
                    "Terminal AgentTask cannot activate recovered execution.",
                )
            budget = await uow.agents.get_task_budget(str(execution.task_id))
            if budget is None:
                raise RecoveryPlanRejected(
                    "RECOVERY_TASK_BUDGET_MISSING",
                    "Task-scoped recovery requires durable TaskBudget.",
                )
            if str(budget.state) != "OPEN":
                raise RecoveryPlanDeferred(
                    "RECOVERY_TASK_BUDGET_CLOSED",
                    "TaskBudget is not OPEN.",
                )

        if execution.branch_id is not None:
            if execution.task_id is None:
                raise RecoveryPlanRejected(
                    "RECOVERY_BRANCH_WITHOUT_TASK",
                    "TaskBranch recovery requires task-scoped execution.",
                )
            branch = await uow.agents.get_task_branch(
                str(execution.branch_id)
            )
            if (
                branch is None
                or str(branch.task_id) != str(execution.task_id)
                or str(branch.resolution_state) != "OPEN"
                or str(branch.current_execution_id or "")
                != str(execution.id)
            ):
                raise RecoveryPlanRejected(
                    "RECOVERY_BRANCH_NOT_OPEN",
                    "Recovered execution is not the current OPEN TaskBranch head.",
                )
        return task, branch, budget

    async def _reject_unproven_later_iteration_in_uow(
        self,
        uow,
        execution,
        checkpoint,
    ) -> None:
        iterations = tuple(
            await uow.agents.list_iterations(str(execution.id))
        )
        if any(
            int(item.iteration) > int(checkpoint.iteration)
            for item in iterations
        ):
            raise RecoveryPlanRejected(
                "SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN",
                "Durable iteration progress beyond the frozen recovery cut "
                "has no resumed-owner provenance.",
            )

    async def _freeze_inference_identity_in_uow(
        self,
        uow,
        execution,
        checkpoint,
        safe_point,
        metadata: dict[str, Any],
    ) -> tuple[str | None, str | None, RecoveryInferenceDisposition]:
        frozen_iteration_id = metadata.get("r12_recovery_iteration_id")
        frozen_ids = metadata.get("r12_recovery_active_tool_call_ids")
        if not isinstance(frozen_ids, (list, tuple)):
            raise RecoveryPlanRejected(
                "RECOVERY_BATCH_SNAPSHOT_CORRUPT",
                "Frozen active tool-call identity is not a sequence.",
            )

        if frozen_iteration_id is None:
            if int(checkpoint.iteration) != 0 or list(frozen_ids):
                raise RecoveryPlanRejected(
                    "RECOVERY_ITERATION_ZERO_CONFLICT",
                    "Null frozen iteration is valid only for empty iteration-zero cut.",
                )
            if safe_point.iteration_id is not None:
                raise RecoveryPlanRejected(
                    "RECOVERY_ITERATION_ZERO_PROMOTED",
                    "Late iteration row cannot redefine empty recovery cut.",
                )
            return None, None, RecoveryInferenceDisposition.NO_INFERENCE

        iteration = await uow.agents.get_iteration(str(frozen_iteration_id))
        if (
            iteration is None
            or str(iteration.execution_id) != str(execution.id)
            or str(iteration.id) != str(frozen_iteration_id)
            or int(iteration.iteration) != int(checkpoint.iteration)
            or str(safe_point.iteration_id) != str(frozen_iteration_id)
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_ITERATION_PROVENANCE_CONFLICT",
                "Frozen recovery iteration differs from exact durable row.",
            )

        inference_request_id = str(
            getattr(iteration, "inference_request_id", "") or ""
        ) or None
        inference_response = getattr(iteration, "inference_response", None)
        if inference_request_id is not None and inference_response is None:
            raise RecoveryPlanDeferred(
                "RECOVERY_INFERENCE_OUTCOME_AMBIGUOUS",
                "Frozen logical inference has no canonical completion proof.",
            )

        if inference_request_id is None:
            if (
                str(iteration.state) == "PREPARING"
                and getattr(iteration, "inference_request", None) is None
                and inference_response is None
                and not tuple(safe_point.ordered_tool_calls)
            ):
                disposition = (
                    RecoveryInferenceDisposition.NEW_LOGICAL_INFERENCE_ALLOWED
                )
            else:
                disposition = RecoveryInferenceDisposition.NO_INFERENCE
        else:
            disposition = RecoveryInferenceDisposition.NO_INFERENCE

        return str(frozen_iteration_id), inference_request_id, disposition

    @staticmethod
    def _validate_pending_rows(
        pending_rows,
        ordered_tool_call_ids: tuple[str, ...],
        reconstructed_pending,
    ) -> None:
        by_invocation = {
            str(item["invocation_id"]): item
            for item in reconstructed_pending
        }
        expected_unresolved = set(by_invocation)
        frozen_watermark = {str(item.invocation_id) for item in pending_rows}
        if not expected_unresolved.issubset(frozen_watermark):
            raise RecoveryPlanRejected(
                "RECOVERY_PENDING_SNAPSHOT_CONFLICT",
                "Current unresolved invocations are missing from frozen checkpoint watermark.",
            )

        previous = -1
        for item in pending_rows:
            ordinal = int(item.ordinal)
            if (
                ordinal <= previous
                or ordinal >= len(ordered_tool_call_ids)
                or ordered_tool_call_ids[ordinal] != str(item.tool_call_id)
            ):
                raise RecoveryPlanRejected(
                    "RECOVERY_PENDING_ORDER_INVALID",
                    "Pending invocation ordering differs from frozen active batch.",
                )
            previous = ordinal
            reconstructed = by_invocation.get(str(item.invocation_id))
            if reconstructed is not None and (
                int(reconstructed["ordinal"]) != ordinal
                or str(reconstructed["tool_call_id"])
                != str(item.tool_call_id)
                or str(reconstructed["capability_id"])
                != str(item.capability_id)
            ):
                raise RecoveryPlanRejected(
                    "RECOVERY_PENDING_SNAPSHOT_CONFLICT",
                    "Pending invocation identity differs from reconstructed safe point.",
                )

    @staticmethod
    def _invocation_from_record(record) -> CapabilityInvocation:
        return CapabilityInvocation(
            invocation_id=record.invocation_id,
            capability_id=record.capability_id,
            capability_version=record.capability_version,
            kind=CapabilityKind(record.kind),
            execution_mode=CapabilityExecutionMode(record.execution_mode),
            idempotency=CapabilityIdempotency(record.idempotency),
            request_fingerprint=record.request_fingerprint,
            owner_user_id=record.owner_user_id,
            origin_client_id=record.origin_client_id,
            remote_outcome_state=(
                RemoteOutcomeState(record.remote_outcome_state)
                if record.remote_outcome_state is not None
                else None
            ),
            implementation_id=record.implementation_id,
            driver_kind=record.driver_kind,
            state=CapabilityInvocationState(record.state),
            wait_reason=(
                CapabilityWaitReason(record.wait_reason)
                if record.wait_reason is not None
                else None
            ),
            session_id=record.session_id,
            turn_id=record.turn_id,
            execution_id=record.execution_id,
            workflow_id=record.workflow_id,
            tool_call_id=record.tool_call_id,
            connection_id=record.connection_id,
            attempt=record.attempt,
            max_attempts=record.max_attempts,
            arguments=dict(record.arguments or {}),
            output=record.output,
            error=record.error,
            created_at=record.created_at,
            started_at=record.started_at,
            updated_at=record.updated_at,
            completed_at=record.completed_at,
            deadline_at=record.deadline_at,
            correlation_id=record.correlation_id,
            trace_id=record.trace_id,
            revision=record.revision,
        )

    @staticmethod
    def _validate_invocation_identity(
        execution,
        checkpoint,
        snapshot,
        invocation: CapabilityInvocation,
        *,
        principal: str,
    ) -> None:
        expected = {
            "execution_id": str(execution.id),
            "tool_call_id": str(snapshot.tool_call_id),
            "capability_id": str(snapshot.capability_id),
            "capability_version": snapshot.capability_version,
            "request_fingerprint": snapshot.request_fingerprint,
        }
        for field, expected_value in expected.items():
            if getattr(invocation, field) != expected_value:
                raise RecoveryPlanRejected(
                    "RECOVERY_INVOCATION_SEMANTIC_CONFLICT",
                    f"CapabilityInvocation {field} differs from checkpoint.",
                )
        if invocation.idempotency.value != str(snapshot.idempotency):
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_SEMANTIC_CONFLICT",
                "CapabilityInvocation idempotency differs from checkpoint.",
            )
        if invocation.owner_user_id != principal:
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_PRINCIPAL_CONFLICT",
                "CapabilityInvocation owner differs from durable recovery principal.",
            )
        if snapshot.origin_client_id != invocation.origin_client_id:
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_CLIENT_LINEAGE_CONFLICT",
                "Checkpoint invocation stable-client identity changed.",
            )
        if (
            snapshot.origin_connection_id is not None
            and invocation.connection_id != snapshot.origin_connection_id
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_CONNECTION_LINEAGE_CONFLICT",
                "Checkpoint invocation origin connection changed.",
            )
        if (
            invocation.remote_outcome_state
            is not RemoteOutcomeState.TERMINAL_COMMITTED
            and (
                not checkpoint.origin_client_id
                or checkpoint.origin_client_id != invocation.origin_client_id
            )
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_CHECKPOINT_CLIENT_LINEAGE_CONFLICT",
                "Non-REUSE recovery requires checkpoint stable-client lineage.",
            )
        if invocation.revision < int(snapshot.invocation_revision):
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_REVISION_REGRESSION",
                "CapabilityInvocation revision is older than recovery watermark.",
            )
        observed = (
            RemoteOutcomeState(snapshot.observed_remote_outcome_state)
            if snapshot.observed_remote_outcome_state is not None
            else None
        )
        if observed is RemoteOutcomeState.TERMINAL_COMMITTED and (
            invocation.remote_outcome_state
            is not RemoteOutcomeState.TERMINAL_COMMITTED
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_REMOTE_OUTCOME_REGRESSION",
                "Terminal checkpoint outcome regressed in durable invocation.",
            )

    async def _classify_action_in_uow(
        self,
        uow,
        execution,
        snapshot,
        invocation: CapabilityInvocation,
        *,
        principal: str,
        target_connection_id: str | None,
    ) -> RecoveryInvocationAction:
        outcome = invocation.remote_outcome_state

        if outcome is RemoteOutcomeState.TERMINAL_COMMITTED:
            await self._require_committed_projection_in_uow(
                uow,
                execution,
                snapshot,
                invocation,
            )
            return self._action(
                snapshot,
                invocation,
                ResumeInvocationActionKind.REUSE_COMMITTED,
            )

        if outcome in {
            RemoteOutcomeState.IN_FLIGHT,
            RemoteOutcomeState.OUTCOME_UNKNOWN,
        }:
            raise RecoveryPlanDeferred(
                "RECOVERY_RECONCILIATION_REQUIRED",
                "Read-only F1 does not mutate R6 reconciliation authority.",
            )

        if outcome is not RemoteOutcomeState.NOT_DISPATCHED:
            raise RecoveryPlanRejected(
                "RECOVERY_REMOTE_OUTCOME_INVALID",
                "Pending invocation has no safe durable remote outcome.",
            )
        if (
            invocation.state is not CapabilityInvocationState.WAITING
            or invocation.wait_reason is not CapabilityWaitReason.CONNECTION
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_CONTINUATION_STATE_INVALID",
                "NOT_DISPATCHED continuation requires WAITING(CONNECTION).",
            )

        continuation = self._resolve_continuation_authority(
            invocation,
            target_connection_id=target_connection_id,
        )
        quota = await self._find_tool_quota_authority(
            invocation,
            principal=principal,
        )
        return self._action(
            snapshot,
            invocation,
            ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
            continuation=continuation,
            quota=quota,
        )

    async def _require_committed_projection_in_uow(
        self,
        uow,
        execution,
        snapshot,
        invocation: CapabilityInvocation,
    ) -> None:
        if invocation.state not in TERMINAL_INVOCATION_STATES:
            raise RecoveryPlanRejected(
                "RECOVERY_TERMINAL_AUTHORITY_INVALID",
                "TERMINAL_COMMITTED requires terminal invocation lifecycle.",
            )
        result = await uow.agents.get_tool_result(
            str(execution.id),
            str(snapshot.tool_call_id),
        )
        if result is None or str(result.commit_state) != "COMMITTED":
            raise RecoveryPlanRejected(
                "RECOVERY_COMMITTED_RESULT_MISSING",
                "Terminal invocation lacks COMMITTED AgentToolResult.",
            )
        identity = {
            "execution_id": str(execution.id),
            "tool_call_id": str(snapshot.tool_call_id),
            "invocation_id": invocation.invocation_id,
            "capability_id": invocation.capability_id,
        }
        for field, expected in identity.items():
            if str(getattr(result, field, "") or "") != str(expected):
                raise RecoveryPlanRejected(
                    "RECOVERY_COMMITTED_RESULT_CONFLICT",
                    f"Committed AgentToolResult {field} differs from invocation.",
                )

        error = dict(invocation.error or {})
        succeeded = invocation.state is CapabilityInvocationState.COMPLETED
        expected_projection = {
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
        for field, expected in expected_projection.items():
            if getattr(result, field, None) != expected:
                raise RecoveryPlanRejected(
                    "RECOVERY_COMMITTED_RESULT_CONFLICT",
                    f"Committed AgentToolResult {field} differs from invocation.",
                )

    def _resolve_continuation_authority(
        self,
        invocation: CapabilityInvocation,
        *,
        target_connection_id: str | None,
    ) -> RecoveryContinuationAuthority:
        resolver = getattr(
            self._capabilities,
            "_resolve_continuation_target",
            None,
        )
        if not callable(resolver):
            raise RecoveryPlanDeferred(
                "RECOVERY_CONTINUATION_AUTHORITY_UNAVAILABLE",
                "Canonical continuation target resolver is unavailable.",
            )
        try:
            _driver, implementation = resolver(
                invocation,
                target_connection_id=target_connection_id,
            )
        except CapabilityError as exc:
            if bool(getattr(exc, "retryable", False)):
                raise RecoveryPlanDeferred(exc.code, str(exc)) from exc
            raise RecoveryPlanRejected(exc.code, str(exc)) from exc

        target_connection = str(target_connection_id or "")
        target_client_id = str(invocation.origin_client_id or "")
        implementation_id = str(
            getattr(implementation, "implementation_id", "") or ""
        )
        if not target_connection or not target_client_id or not implementation_id:
            raise RecoveryPlanRejected(
                "RECOVERY_CONTINUATION_AUTHORITY_INVALID",
                "Canonical continuation target proof is incomplete.",
            )
        return RecoveryContinuationAuthority(
            target_client_id=target_client_id,
            target_connection_id=target_connection,
            implementation_id=implementation_id,
        )

    async def _find_tool_quota_authority(
        self,
        invocation: CapabilityInvocation,
        *,
        principal: str,
    ) -> RecoveryToolQuotaAuthority | None:
        quota = self._tool_quota
        if (
            invocation.kind is not CapabilityKind.TOOL
            or quota is None
            or not bool(getattr(quota, "enabled", False))
        ):
            return None

        authority = await quota.find_tool_call_authority(
            owner_user_id=principal,
            invocation_id=invocation.invocation_id,
            capability_id=invocation.capability_id,
            request_fingerprint=str(invocation.request_fingerprint or ""),
            arguments=dict(invocation.arguments),
            execution_id=invocation.execution_id,
            tool_call_id=invocation.tool_call_id,
            workflow_id=invocation.workflow_id,
            session_id=invocation.session_id,
        )
        if authority is None:
            raise RecoveryPlanDeferred(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_MISSING",
                "Continuation lacks existing UBQ-3 TOOL authority.",
            )
        if (
            not bool(authority.historical_bridge)
            and str(authority.reservation_state) != "RESERVED"
        ):
            raise RecoveryPlanRejected(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_INVALID",
                "Direct UBQ-3 continuation authority must remain RESERVED.",
            )
        if authority.reservation_id is None or authority.window_epoch is None:
            raise RecoveryPlanRejected(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_INVALID",
                "UBQ-3 authority lacks durable reservation identity.",
            )
        return RecoveryToolQuotaAuthority(
            reservation_id=str(authority.reservation_id),
            idempotency_key=(
                str(authority.idempotency_key)
                if authority.idempotency_key is not None
                else None
            ),
            window_epoch=int(authority.window_epoch),
            reservation_state=str(authority.reservation_state),
            historical_bridge=bool(authority.historical_bridge),
        )

    @staticmethod
    def _action(
        snapshot,
        invocation: CapabilityInvocation,
        kind: ResumeInvocationActionKind,
        *,
        continuation: RecoveryContinuationAuthority | None = None,
        quota: RecoveryToolQuotaAuthority | None = None,
    ) -> RecoveryInvocationAction:
        if not invocation.capability_version or not invocation.request_fingerprint:
            raise RecoveryPlanRejected(
                "RECOVERY_INVOCATION_SNAPSHOT_MISSING",
                "Invocation lacks version/fingerprint required by recovery plan.",
            )
        return RecoveryInvocationAction(
            invocation_id=invocation.invocation_id,
            tool_call_id=str(snapshot.tool_call_id),
            ordinal=int(snapshot.ordinal),
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            request_fingerprint=invocation.request_fingerprint,
            kind=invocation.kind,
            execution_mode=invocation.execution_mode,
            idempotency=invocation.idempotency,
            expected_invocation_revision=int(invocation.revision),
            expected_invocation_state=invocation.state,
            expected_remote_outcome_state=invocation.remote_outcome_state,
            origin_client_id=invocation.origin_client_id,
            origin_connection_id=invocation.connection_id,
            action=kind,
            continuation_authority=continuation,
            tool_quota_authority=quota,
        )


__all__ = [
    "AgentRecoveryPlanningService",
    "RecoveryPlanDeferred",
    "RecoveryPlanError",
    "RecoveryPlanRejected",
]
