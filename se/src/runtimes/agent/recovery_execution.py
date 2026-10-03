from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from ..capability.contracts.definition import CapabilityKind
from ..capability.contracts.error import (
    CapabilityContinuationDispatchGuardError,
    CapabilityError,
)
from .contracts.context import AgentExecutionContext
from .contracts.recovery import (
    RecoveryActivationResult,
    RecoveryInferenceDisposition,
    RecoveryInvocationAction,
    RecoveryPlan,
    RecoveryToolQuotaAuthority,
    recovery_plan_fingerprint,
)
from .contracts.resume import (
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumeTriggerType,
)
from .contracts.inference import InferenceMessage
from .contracts.tool import ToolExecutionResult


from .persistence import ExecutionConflictError


class RecoveryExecutionError(RuntimeError):
    """Fail-closed R12-F3 recovered active-batch execution error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


class AgentRecoveryExecutionService:
    """R12-F3-A executor for the activated recovery TOOL batch only.

    The service deliberately stops before provider/model inference and normal
    Agent-loop progression. It reuses the existing R7 continuation coordinator
    and CapabilityRuntime continuation lifecycle rather than creating a second
    continuation/admission/quota authority.
    """

    def __init__(
        self,
        durable_store,
        capability_runtime,
        tool_execution,
    ) -> None:
        self._store = durable_store
        self._capabilities = capability_runtime
        self._tool_execution = tool_execution

    @staticmethod
    def _validate_plan_activation(
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
    ) -> None:
        if recovery_plan_fingerprint(plan) != plan.plan_fingerprint:
            raise RecoveryExecutionError(
                "STALE_RECOVERY_PLAN",
                "RecoveryPlan fingerprint does not match its semantics.",
            )
        if plan.target_trigger is not ResumeTriggerType.SERVER_RECOVERY:
            raise RecoveryExecutionError(
                "RECOVERY_TRIGGER_CONFLICT",
                "F3-A requires SERVER_RECOVERY authority.",
            )
        if (
            activation.execution_id != plan.execution_id
            or activation.checkpoint_id != plan.checkpoint_id
            or activation.source_execution_revision
            != plan.expected_execution_revision
            or activation.consumed_execution_revision
            != plan.expected_execution_revision + 1
            or activation.lease_generation
            != plan.expected_unowned_lease_generation + 1
            or not str(activation.activation_owner_instance_id or "").strip()
        ):
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "RecoveryActivationResult differs from RecoveryPlan authority.",
            )
        expiry = activation.lease_expires_at
        if not isinstance(expiry, datetime) or expiry.tzinfo is None:
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "Recovery activation expiry must be timezone-aware.",
            )

    @staticmethod
    def _validate_context(
        context: AgentExecutionContext,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
    ) -> None:
        if (
            context.execution_id != plan.execution_id
            or context.agent_id != plan.agent_id
            or context.session_id != plan.session_id
            or context.task_id != plan.task_id
            or context.branch_id != plan.branch_id
            or context.parent_execution_id != plan.parent_execution_id
            or context.retry_of_execution_id != plan.retry_of_execution_id
            or context.base_execution_id != plan.base_execution_id
            or context.base_checkpoint_id != plan.base_checkpoint_id
            or context.connection_id != plan.target_connection_id
            or context.iteration != plan.iteration
            or context.resume_revision
            != activation.consumed_execution_revision
            or str(context.identity.user_id or "")
            != plan.resolved_recovery_principal
        ):
            raise RecoveryExecutionError(
                "RECOVERY_CONTEXT_CONFLICT",
                "Prepared execution context differs from recovery authority.",
            )
        if context.active_budget_running:
            raise RecoveryExecutionError(
                "RECOVERY_CONTEXT_CONFLICT",
                "F3-A requires a prepared context with frozen active budget.",
            )

    async def _require_consumed_activation_handoff(
        self,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
    ) -> None:
        loader = getattr(
            self._store,
            "load_resume_claim_by_request_id",
            None,
        )
        if not callable(loader):
            raise RecoveryExecutionError(
                "RECOVERY_ACTIVATION_HANDOFF_UNAVAILABLE",
                "Durable recovery claim reader is unavailable.",
            )
        claim = await loader(activation.resume_request_id)
        state = str(
            getattr(getattr(claim, "state", None), "value", None)
            or getattr(claim, "state", "")
        )
        if (
            claim is None
            or str(getattr(claim, "claim_id", "") or "") != activation.claim_id
            or str(getattr(claim, "resume_request_id", "") or "")
            != activation.resume_request_id
            or str(getattr(claim, "execution_id", "") or "")
            != plan.execution_id
            or str(getattr(claim, "checkpoint_id", "") or "")
            != plan.checkpoint_id
            or int(getattr(claim, "expected_execution_revision", -1))
            != plan.expected_execution_revision
            or str(getattr(claim, "plan_fingerprint", "") or "")
            != plan.plan_fingerprint
            or str(getattr(claim, "user_id", "") or "")
            != plan.resolved_recovery_principal
            or getattr(claim, "client_id", None) != plan.target_client_id
            or getattr(claim, "connection_id", None)
            != plan.target_connection_id
            or str(getattr(claim, "wait_reason", "") or "") != "RECOVERY"
            or str(getattr(claim, "trigger_type", "") or "")
            != ResumeTriggerType.SERVER_RECOVERY.value
            or state != "CONSUMED"
            or int(getattr(claim, "consumed_execution_revision", -1))
            != activation.consumed_execution_revision
        ):
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "Durable consumed recovery claim differs from F2 activation.",
            )

        metadata = dict(getattr(claim, "metadata", {}) or {})
        handoff = metadata.get("r12_f2_activation_handoff")
        if not isinstance(handoff, dict):
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "Consumed recovery claim lacks F2 activation handoff proof.",
            )
        try:
            handoff_expiry = datetime.fromisoformat(
                str(handoff["lease_expires_at"])
            )
            handoff_now = datetime.fromisoformat(
                str(handoff["activation_now_utc"])
            )
            handoff_generation = int(handoff["lease_generation"])
            handoff_revision = int(
                handoff["consumed_execution_revision"]
            )
            handoff_version = int(handoff["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "F2 activation handoff proof is malformed.",
            ) from exc

        if (
            handoff_expiry.tzinfo is None
            or handoff_now.tzinfo is None
            or handoff_now >= handoff_expiry
            or handoff_version != 1
            or str(handoff.get("kind") or "")
            != "SERVER_RECOVERY_ACTIVATION"
            or str(handoff.get("execution_id") or "") != plan.execution_id
            or str(handoff.get("checkpoint_id") or "") != plan.checkpoint_id
            or str(handoff.get("recovery_fingerprint") or "")
            != plan.recovery_fingerprint
            or str(handoff.get("recovery_plan_fingerprint") or "")
            != plan.plan_fingerprint
            or str(handoff.get("activation_owner_instance_id") or "")
            != activation.activation_owner_instance_id
            or handoff_generation != activation.lease_generation
            or handoff_expiry != activation.lease_expires_at
            or handoff_revision != activation.consumed_execution_revision
        ):
            raise RecoveryExecutionError(
                "STALE_RECOVERY_ACTIVATION",
                "F2 durable activation handoff no longer matches authority.",
            )

    async def _require_exact_active_fence(
        self,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        context: AgentExecutionContext,
    ) -> None:
        checker = getattr(
            self._store,
            "has_active_execution_lease_fence",
            None,
        )
        if not callable(checker):
            raise RecoveryExecutionError(
                "RECOVERY_LEASE_FENCE_UNAVAILABLE",
                "Durable store has no execution lease-fence predicate.",
            )
        active = await checker(
            plan.execution_id,
            owner_instance_id=activation.activation_owner_instance_id,
            lease_generation=activation.lease_generation,
            now_utc=context.clock.now_utc(),
            expected_lease_expires_at=activation.lease_expires_at,
        )
        if not active:
            raise RecoveryExecutionError(
                "RECOVERY_ACTIVE_LEASE_FENCE_LOST",
                "Exact recovery owner/generation/activation-expiry fence is stale.",
                retryable=True,
            )

    async def _load_invocation(self, action: RecoveryInvocationAction):
        lifecycle = getattr(self._capabilities, "invocation_lifecycle", None)
        store = getattr(lifecycle, "store", None)
        getter = getattr(store, "get", None)
        if not callable(getter):
            raise RecoveryExecutionError(
                "RECOVERY_INVOCATION_AUTHORITY_UNAVAILABLE",
                "Capability invocation store is unavailable.",
            )
        invocation = await getter(action.invocation_id)
        if invocation is None:
            raise RecoveryExecutionError(
                "RECOVERY_INVOCATION_MISSING",
                f"Missing CapabilityInvocation {action.invocation_id!r}.",
            )
        return invocation

    @staticmethod
    def _same_remote_outcome(left, right) -> bool:
        if left is right:
            return True
        if left is None or right is None:
            return False
        return str(getattr(left, "value", left)) == str(
            getattr(right, "value", right)
        )

    async def _require_quota_authority(
        self,
        plan: RecoveryPlan,
        action: RecoveryInvocationAction,
        invocation,
    ) -> None:
        quota = getattr(self._capabilities, "tool_quota_service", None)
        enabled = (
            invocation.kind is CapabilityKind.TOOL
            and quota is not None
            and bool(getattr(quota, "enabled", False))
        )
        frozen = action.tool_quota_authority

        if not enabled:
            if frozen is not None:
                raise RecoveryExecutionError(
                    "RECOVERY_TOOL_QUOTA_AUTHORITY_CHANGED",
                    "Frozen quota authority exists but canonical quota is disabled.",
                )
            return
        if frozen is None:
            raise RecoveryExecutionError(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_CHANGED",
                "Canonical quota is enabled but RecoveryPlan lacks authority.",
            )

        finder = getattr(quota, "find_tool_call_authority", None)
        if not callable(finder):
            raise RecoveryExecutionError(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_UNAVAILABLE",
                "Canonical read-only TOOL quota authority lookup is unavailable.",
            )
        current = await finder(
            owner_user_id=plan.resolved_recovery_principal,
            invocation_id=invocation.invocation_id,
            capability_id=invocation.capability_id,
            request_fingerprint=str(invocation.request_fingerprint or ""),
            arguments=dict(invocation.arguments or {}),
            execution_id=invocation.execution_id,
            tool_call_id=invocation.tool_call_id,
            workflow_id=invocation.workflow_id,
            session_id=invocation.session_id,
        )
        if current is None or not self._quota_authority_matches(frozen, current):
            raise RecoveryExecutionError(
                "RECOVERY_TOOL_QUOTA_AUTHORITY_CHANGED",
                "UBQ-3 continuation authority differs from the F1 snapshot.",
            )

    @staticmethod
    def _quota_authority_matches(
        frozen: RecoveryToolQuotaAuthority,
        current: Any,
    ) -> bool:
        return (
            str(getattr(current, "reservation_id", "") or "")
            == frozen.reservation_id
            and (
                str(getattr(current, "idempotency_key"))
                if getattr(current, "idempotency_key", None) is not None
                else None
            )
            == frozen.idempotency_key
            and int(getattr(current, "window_epoch", -1))
            == frozen.window_epoch
            and str(getattr(current, "reservation_state", "") or "")
            == frozen.reservation_state
            and bool(getattr(current, "historical_bridge", False))
            == frozen.historical_bridge
        )

    async def _prepare_continuation_action(
        self,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        context: AgentExecutionContext,
        action: RecoveryInvocationAction,
    ) -> ResumeInvocationAction:
        if action.action is not ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED:
            raise RecoveryExecutionError(
                "RECOVERY_ACTION_NOT_RELEASED",
                "F3-A only releases DISPATCH_NOT_DISPATCHED continuation.",
            )
        authority = action.continuation_authority
        if authority is None:
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AUTHORITY_MISSING",
                "Executable recovery action lacks F1 continuation authority.",
            )

        invocation = await self._load_invocation(action)
        if (
            invocation.invocation_id != action.invocation_id
            or invocation.execution_id != plan.execution_id
            or invocation.tool_call_id != action.tool_call_id
            or invocation.capability_id != action.capability_id
            or invocation.capability_version != action.capability_version
            or invocation.kind is not action.kind
            or invocation.execution_mode is not action.execution_mode
            or invocation.idempotency is not action.idempotency
            or invocation.request_fingerprint != action.request_fingerprint
            or int(invocation.revision)
            != int(action.expected_invocation_revision)
            or invocation.state is not action.expected_invocation_state
            or not self._same_remote_outcome(
                invocation.remote_outcome_state,
                action.expected_remote_outcome_state,
            )
            or invocation.origin_client_id != action.origin_client_id
            or invocation.connection_id != action.origin_connection_id
            or str(invocation.owner_user_id or "")
            != plan.resolved_recovery_principal
        ):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_IDENTITY_CHANGED",
                "Current invocation differs from the F1 recovery snapshot.",
            )

        resolver = getattr(
            self._capabilities,
            "_resolve_continuation_target",
            None,
        )
        if not callable(resolver):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AUTHORITY_UNAVAILABLE",
                "Canonical continuation target resolver is unavailable.",
            )
        try:
            _driver, implementation = resolver(
                invocation,
                target_connection_id=authority.target_connection_id,
            )
        except CapabilityError as exc:
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AFFINITY_CHANGED",
                str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc

        snapshot = self._capabilities.connection_registry.get(
            authority.target_connection_id
        )
        current_client_id = str(
            getattr(snapshot, "metadata", {}).get("client_id") or ""
        )
        implementation_id = str(
            getattr(implementation, "implementation_id", "") or ""
        )
        if (
            plan.target_client_id != authority.target_client_id
            or plan.target_connection_id != authority.target_connection_id
            or current_client_id != authority.target_client_id
            or implementation_id != authority.implementation_id
        ):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AFFINITY_CHANGED",
                "Continuation client/connection/implementation differs from F1.",
            )

        await self._require_quota_authority(plan, action, invocation)
        await self._require_exact_active_fence(plan, activation, context)

        return ResumeInvocationAction(
            invocation_id=action.invocation_id,
            tool_call_id=action.tool_call_id,
            ordinal=action.ordinal,
            capability_id=action.capability_id,
            capability_version=action.capability_version,
            request_fingerprint=action.request_fingerprint,
            idempotency=action.idempotency,
            expected_invocation_revision=action.expected_invocation_revision,
            expected_invocation_state=action.expected_invocation_state,
            expected_remote_outcome_state=action.expected_remote_outcome_state,
            action=action.action,
        )

    async def _require_canonical_dispatch_authority(
        self,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        context: AgentExecutionContext,
        action: RecoveryInvocationAction,
        invocation,
        selected_implementation_id: str,
        target_connection_id: str | None,
        origin_connection_id: str | None,
    ) -> None:
        """Re-prove R12 + F1 authority at the physical dispatch boundary."""

        authority = action.continuation_authority
        if authority is None:
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AUTHORITY_MISSING",
                "Executable recovery action lacks continuation authority.",
            )

        # Keep the lease query as the final await before synchronous live
        # catalog/connection revalidation and the driver's send boundary.
        await self._require_exact_active_fence(plan, activation, context)

        revalidator = getattr(
            self._capabilities,
            "_revalidate_continuation_target_at_dispatch",
            None,
        )
        if not callable(revalidator):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AUTHORITY_UNAVAILABLE",
                "Canonical dispatch-boundary target revalidator is unavailable.",
            )
        try:
            live_implementation = revalidator(
                invocation,
                target_connection_id=target_connection_id,
            )
        except CapabilityError as exc:
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AFFINITY_CHANGED",
                str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc

        snapshot = self._capabilities.connection_registry.get(
            target_connection_id
        )
        live_client_id = str(
            getattr(snapshot, "metadata", {}).get("client_id") or ""
        )
        live_implementation_id = str(
            getattr(live_implementation, "implementation_id", "") or ""
        )

        if (
            target_connection_id != authority.target_connection_id
            or plan.target_connection_id != authority.target_connection_id
            or live_client_id != authority.target_client_id
            or plan.target_client_id != authority.target_client_id
            or selected_implementation_id != authority.implementation_id
            or live_implementation_id != authority.implementation_id
            or origin_connection_id != action.origin_connection_id
            or invocation.invocation_id != action.invocation_id
            or invocation.execution_id != plan.execution_id
            or invocation.tool_call_id != action.tool_call_id
            or invocation.capability_id != action.capability_id
            or invocation.capability_version != action.capability_version
            or invocation.kind is not action.kind
            or invocation.execution_mode is not action.execution_mode
            or invocation.idempotency is not action.idempotency
            or invocation.request_fingerprint != action.request_fingerprint
            or invocation.origin_client_id != action.origin_client_id
            or str(invocation.owner_user_id or "")
            != plan.resolved_recovery_principal
        ):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_AFFINITY_CHANGED",
                "Canonical dispatch authority differs from F1 recovery authority.",
            )

    @staticmethod
    def _tool_result_from_record(
        record,
        *,
        iteration: int,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            execution_id=record.execution_id,
            iteration=iteration,
            invocation_id=record.invocation_id,
            tool_call_id=record.tool_call_id,
            capability_id=record.capability_id,
            success=record.success,
            output=record.output,
            error_code=record.error_code,
            error_message=record.error_message,
            retryable=record.retryable,
            metadata=dict(record.extra_metadata or {}),
        )

    async def _load_committed_read_only(
        self,
        plan: RecoveryPlan,
        tool_call_id: str,
    ) -> ToolExecutionResult | None:
        loader = getattr(self._store, "load_tool_result", None)
        if not callable(loader):
            raise RecoveryExecutionError(
                "RECOVERY_TOOL_RESULT_STORE_UNAVAILABLE",
                "Durable tool-result reader is unavailable.",
            )
        record = await loader(plan.execution_id, tool_call_id)
        if record is None:
            return None
        if str(getattr(record, "commit_state", "PROVISIONAL")) != "COMMITTED":
            return None
        return self._tool_result_from_record(
            record,
            iteration=plan.iteration,
        )

    async def _project_continuation_result(
        self,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        context: AgentExecutionContext,
        action: RecoveryInvocationAction,
        result: ToolExecutionResult,
    ) -> ToolExecutionResult:
        existing = await self._load_committed_read_only(
            plan,
            action.tool_call_id,
        )
        if existing is not None:
            await self._require_exact_active_fence(plan, activation, context)
            return existing

        recovery_fence = {
            "owner_instance_id": activation.activation_owner_instance_id,
            "lease_generation": activation.lease_generation,
            "lease_expires_at": activation.lease_expires_at,
        }
        saver = getattr(self._store, "save_tool_result", None)
        if not callable(saver):
            raise RecoveryExecutionError(
                "RECOVERY_TOOL_RESULT_STORE_UNAVAILABLE",
                "Durable tool-result projection writer is unavailable.",
            )
        iteration_id = (
            plan.recovery_iteration_id
            or f"{plan.execution_id}:iteration:{plan.iteration}"
        )
        try:
            await saver(
                {
                    "id": f"{result.execution_id}:{result.tool_call_id}",
                    "execution_id": result.execution_id,
                    "iteration_id": iteration_id,
                    "tool_call_id": result.tool_call_id,
                    "invocation_id": result.invocation_id,
                    "capability_id": result.capability_id,
                    "success": result.success,
                    "output": result.output,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "retryable": result.retryable,
                    "extra_metadata": result.metadata,
                    "attempt": result.metadata.get("attempt", 1),
                },
                recovery_fence=recovery_fence,
            )
        except ExecutionConflictError as exc:
            raise RecoveryExecutionError(
                "RECOVERY_ACTIVE_LEASE_FENCE_LOST",
                "Exact recovery authority was lost before AgentToolResult write.",
                retryable=True,
            ) from exc

        committed = await self._load_committed_read_only(
            plan,
            action.tool_call_id,
        )
        if committed is None:
            promoter = getattr(
                self._store,
                "load_committed_tool_result",
                None,
            )
            if not callable(promoter):
                raise RecoveryExecutionError(
                    "RECOVERY_TOOL_RESULT_NOT_COMMITTED",
                    "Continuation has no canonical COMMITTED projection.",
                    retryable=True,
                )
            try:
                record = await promoter(
                    plan.execution_id,
                    action.tool_call_id,
                    recovery_fence=recovery_fence,
                )
            except ExecutionConflictError as exc:
                raise RecoveryExecutionError(
                    "RECOVERY_ACTIVE_LEASE_FENCE_LOST",
                    "Exact recovery authority was lost before result promotion.",
                    retryable=True,
                ) from exc
            if (
                record is None
                or str(getattr(record, "commit_state", "PROVISIONAL"))
                != "COMMITTED"
            ):
                raise RecoveryExecutionError(
                    "RECOVERY_TOOL_RESULT_NOT_COMMITTED",
                    "Continuation R6 outcome is not model-consumable.",
                    retryable=True,
                )
            committed = self._tool_result_from_record(
                record,
                iteration=plan.iteration,
            )

        await self._require_exact_active_fence(plan, activation, context)
        return committed

    @staticmethod
    def _validate_action_order(plan: RecoveryPlan) -> tuple[RecoveryInvocationAction, ...]:
        actions = tuple(
            sorted(plan.invocation_actions, key=lambda item: item.ordinal)
        )
        ordered_ids = tuple(plan.ordered_tool_call_ids)
        if (
            len(set(ordered_ids)) != len(ordered_ids)
            or len({item.ordinal for item in actions}) != len(actions)
            or len({item.invocation_id for item in actions}) != len(actions)
            or len({item.tool_call_id for item in actions}) != len(actions)
            or any(
                item.ordinal < 0
                or item.ordinal >= len(ordered_ids)
                or ordered_ids[item.ordinal] != item.tool_call_id
                for item in actions
            )
        ):
            raise RecoveryExecutionError(
                "STALE_RECOVERY_PLAN",
                "Recovery action ordering differs from canonical tool-call order.",
            )
        return actions

    async def execute_active_tool_batch(
        self,
        context: AgentExecutionContext,
        *,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
    ) -> tuple[ToolExecutionResult, ...]:
        """Execute only the F3-A recovered active TOOL batch."""

        self._validate_plan_activation(plan, activation)
        self._validate_context(context, plan, activation)
        await self._require_consumed_activation_handoff(
            plan,
            activation,
        )
        await self._require_exact_active_fence(plan, activation, context)

        context.restore_active_budget(plan.remaining_active_budget_seconds)
        context.begin_iteration_budget()
        try:
            actions = self._validate_action_order(plan)
            ordered_ids = tuple(plan.ordered_tool_call_ids)
            by_tool_call: dict[str, ToolExecutionResult] = {}
            action_ids = {item.tool_call_id for item in actions}

            for tool_call_id in ordered_ids:
                if tool_call_id in action_ids:
                    continue
                committed = await self._load_committed_read_only(
                    plan,
                    tool_call_id,
                )
                if committed is None:
                    raise RecoveryExecutionError(
                        "RECOVERY_COMMITTED_RESULT_MISSING",
                        "Frozen active-batch slot is no longer COMMITTED.",
                    )
                by_tool_call[tool_call_id] = committed

            continuation_actions: list[RecoveryInvocationAction] = []
            for action in actions:
                if action.action is ResumeInvocationActionKind.REUSE_COMMITTED:
                    committed = await self._load_committed_read_only(
                        plan,
                        action.tool_call_id,
                    )
                    if committed is None:
                        raise RecoveryExecutionError(
                            "RECOVERY_COMMITTED_RESULT_MISSING",
                            "REUSE_COMMITTED is no longer model-consumable.",
                        )
                    by_tool_call[action.tool_call_id] = committed
                else:
                    continuation_actions.append(action)

            if continuation_actions:
                runner = getattr(
                    self._tool_execution,
                    "continue_invocations",
                    None,
                )
                if not callable(runner):
                    raise RecoveryExecutionError(
                        "RECOVERY_CONTINUATION_UNAVAILABLE",
                        "Existing R7 continuation coordinator is unavailable.",
                    )

                async def prepare(raw_action: RecoveryInvocationAction):
                    return await self._prepare_continuation_action(
                        plan,
                        activation,
                        context,
                        raw_action,
                    )

                async def canonical_dispatch_guard(
                    raw_action: RecoveryInvocationAction,
                    invocation,
                    selected_implementation_id: str,
                    target_connection_id: str | None,
                    origin_connection_id: str | None,
                ) -> None:
                    try:
                        await self._require_canonical_dispatch_authority(
                            plan,
                            activation,
                            context,
                            raw_action,
                            invocation,
                            selected_implementation_id,
                            target_connection_id,
                            origin_connection_id,
                        )
                    except RecoveryExecutionError as exc:
                        raise CapabilityContinuationDispatchGuardError(
                            exc.code,
                            str(exc),
                            retryable=exc.retryable,
                        ) from exc

                try:
                    raw_results = await runner(
                        context,
                        continuation_actions,
                        max_parallel=context.limits.max_parallel_tools,
                        pre_dispatch_prepare=prepare,
                        canonical_dispatch_guard=canonical_dispatch_guard,
                        preserve_started_on_failure=True,
                    )
                except CapabilityContinuationDispatchGuardError as exc:
                    if isinstance(exc.__cause__, RecoveryExecutionError):
                        raise exc.__cause__
                    raise RecoveryExecutionError(
                        exc.code,
                        str(exc),
                        retryable=exc.retryable,
                    ) from exc
                raw_by_id = {
                    item.tool_call_id: item for item in raw_results
                }
                if len(raw_by_id) != len(continuation_actions):
                    raise RecoveryExecutionError(
                        "RECOVERY_CONTINUATION_RESULT_CONFLICT",
                        "Continuation batch returned duplicate/missing results.",
                    )

                for action in continuation_actions:
                    result = raw_by_id.get(action.tool_call_id)
                    if (
                        result is None
                        or result.execution_id != plan.execution_id
                        or result.invocation_id != action.invocation_id
                        or result.capability_id != action.capability_id
                    ):
                        raise RecoveryExecutionError(
                            "RECOVERY_CONTINUATION_RESULT_CONFLICT",
                            "Continuation result identity differs from RecoveryPlan.",
                        )
                    by_tool_call[action.tool_call_id] = (
                        await self._project_continuation_result(
                            plan,
                            activation,
                            context,
                            action,
                            result,
                        )
                    )

            if set(by_tool_call) != set(ordered_ids):
                raise RecoveryExecutionError(
                    "RECOVERY_CONTINUATION_RESULT_CONFLICT",
                    "Recovered active batch lacks complete committed coverage.",
                )

            await self._require_exact_active_fence(
                plan,
                activation,
                context,
            )
            return tuple(
                by_tool_call[tool_call_id]
                for tool_call_id in ordered_ids
            )
        finally:
            context.freeze_active_budget()

    @staticmethod
    def _f3b_tool_result_message(
        result: ToolExecutionResult,
    ) -> InferenceMessage:
        return InferenceMessage(
            role="tool",
            name=result.capability_id,
            tool_call_id=result.tool_call_id,
            content=(
                result.output
                if result.success
                else {
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                }
            ),
            metadata={
                "success": result.success,
                "retryable": result.retryable,
            },
        )

    @staticmethod
    def _f3b_digest(value: Any) -> str:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _f3b_handoff_marker(
        cls,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        recovered_results: tuple[ToolExecutionResult, ...],
    ) -> dict[str, Any]:
        transcript_payload = [
            item.model_dump(mode="json")
            for item in plan.transcript_snapshot
        ]
        result_payload = [
            item.model_dump(mode="json")
            for item in recovered_results
        ]
        seed = {
            "version": 1,
            "kind": "R12_F3B_INFERENCE_HANDOFF",
            "execution_id": plan.execution_id,
            "checkpoint_id": plan.checkpoint_id,
            "plan_fingerprint": plan.plan_fingerprint,
            "recovery_fingerprint": plan.recovery_fingerprint,
            "activation_owner_instance_id": (
                activation.activation_owner_instance_id
            ),
            "lease_generation": activation.lease_generation,
            "lease_expires_at": activation.lease_expires_at.isoformat(),
            "consumed_execution_revision": (
                activation.consumed_execution_revision
            ),
            "iteration": plan.iteration,
            "transcript_sha256": cls._f3b_digest(transcript_payload),
            "recovered_results_sha256": cls._f3b_digest(result_payload),
        }
        return {
            **seed,
            "handoff_id": cls._f3b_digest(seed),
        }

    async def execute_recovered_next_iteration(
        self,
        context: AgentExecutionContext,
        *,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        runtime,
    ):
        """Execute F3-A, commit the one-shot handoff, then run bounded F3-B."""

        if (
            plan.inference_disposition
            is not RecoveryInferenceDisposition.NO_INFERENCE
        ):
            raise RecoveryExecutionError(
                "RECOVERY_INFERENCE_CUT_UNPROVEN",
                "The frozen recovery cut does not prove NO_INFERENCE.",
            )

        recovered_results = await self.execute_active_tool_batch(
            context,
            plan=plan,
            activation=activation,
        )
        ordered_ids = tuple(plan.ordered_tool_call_ids)
        if (
            len(recovered_results) != len(ordered_ids)
            or tuple(item.tool_call_id for item in recovered_results)
            != ordered_ids
            or any(
                item.execution_id != plan.execution_id
                or item.iteration != plan.iteration
                for item in recovered_results
            )
        ):
            raise RecoveryExecutionError(
                "RECOVERY_CONTINUATION_RESULT_CONFLICT",
                "F3-A results do not exactly cover the frozen ordered batch.",
            )

        expected_messages = list(plan.transcript_snapshot)
        handoff_messages = [
            *expected_messages,
            *[
                self._f3b_tool_result_message(item)
                for item in recovered_results
            ],
        ]
        marker = self._f3b_handoff_marker(
            plan,
            activation,
            recovered_results,
        )
        recovery_fence = {
            "owner_instance_id": activation.activation_owner_instance_id,
            "lease_generation": activation.lease_generation,
            "lease_expires_at": activation.lease_expires_at,
        }
        writer = getattr(
            self._store,
            "commit_recovery_inference_handoff",
            None,
        )
        if not callable(writer):
            raise RecoveryExecutionError(
                "RECOVERY_INFERENCE_HANDOFF_UNAVAILABLE",
                "Durable store has no atomic F3-B handoff writer.",
            )

        await self._require_exact_active_fence(
            plan,
            activation,
            context,
        )
        disposition = await writer(
            plan.execution_id,
            expected_revision=activation.consumed_execution_revision,
            expected_checkpoint_id=plan.checkpoint_id,
            expected_transcript=[
                item.model_dump(mode="json")
                for item in expected_messages
            ],
            handoff_transcript=[
                item.model_dump(mode="json")
                for item in handoff_messages
            ],
            handoff_marker=marker,
            recovery_fence=recovery_fence,
        )
        await self._require_exact_active_fence(
            plan,
            activation,
            context,
        )

        if disposition == "REUSED":
            # Once the deterministic handoff already exists, this process
            # cannot prove whether a prior owner minted/sent the fresh
            # inference. F3-B deliberately sacrifices liveness over replay.
            raise RecoveryExecutionError(
                "RECOVERY_INFERENCE_CUT_UNPROVEN",
                "F3-B handoff already exists; fresh inference replay is forbidden.",
            )
        if disposition != "WRITTEN":
            raise RecoveryExecutionError(
                "RECOVERY_INFERENCE_HANDOFF_CONFLICT",
                "Atomic F3-B handoff returned an unknown disposition.",
            )

        executor = getattr(
            runtime,
            "execute_recovered_next_iteration",
            None,
        )
        if not callable(executor):
            raise RecoveryExecutionError(
                "RECOVERY_RUNTIME_HANDOFF_UNAVAILABLE",
                "AgentRuntime has no bounded F3-B execution entrypoint.",
            )
        return await executor(
            context,
            plan=plan,
            activation=activation,
            handoff_transcript=handoff_messages,
            recovered_tool_results=recovered_results,
        )


__all__ = [
    "AgentRecoveryExecutionService",
    "RecoveryExecutionError",
]
