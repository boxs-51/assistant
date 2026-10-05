from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.user_budget import (
    UserBudgetReservationIntent,
    UserBudgetReservationState,
    UserBudgetResourceKind,
)
from se.src.infrastructure.storage.repositories.user_budget import (
    UserBudgetConflictError,
    UserBudgetSerializationError,
)

from .user_budget import (
    BudgetOwnerResolution,
    UserBudgetBindingDriftError,
    UserBudgetDualAccountingService,
    UserBudgetPolicyAuthorityConflictError,
    _canonical_sha256,
    _record_utc,
)


UBQ3_IDEMPOTENCY_VERSION = "ubq3-v1"


class UserToolQuotaError(RuntimeError):
    code = "USER_TOOL_QUOTA_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class UserToolQuotaExceededError(UserToolQuotaError):
    code = "USER_TOOL_QUOTA_EXHAUSTED"


class UserToolCapabilityQuotaExceededError(UserToolQuotaError):
    code = "USER_TOOL_CAPABILITY_QUOTA_EXHAUSTED"


class UserToolQuotaConflictError(UserToolQuotaError):
    code = "USER_TOOL_QUOTA_CONFLICT"


@dataclass(frozen=True, slots=True)
class ToolQuotaSettings:
    enabled: bool = False
    max_conflict_retries: int = 8


@dataclass(frozen=True, slots=True)
class ToolQuotaAdmission:
    owner_user_id: str
    invocation_id: str
    capability_id: str
    request_fingerprint: str
    idempotency_key: str | None
    reservation_id: str | None
    window_epoch: int | None
    reservation_state: str
    historical_bridge: bool = False

class UserToolQuotaService:
    """UBQ-3 renewable logical TOOL_CALL authority.

    Admission is keyed by CapabilityInvocation.invocation_id and reserves both
    the owner-wide tool counter and the canonical capability counter in one UoW.
    """

    def __init__(
        self,
        uow_factory,
        *,
        owner_authority: UserBudgetDualAccountingService,
        settings: ToolQuotaSettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._owner_authority = owner_authority
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    @staticmethod
    def _identity(invocation_id: str) -> tuple[str, str]:
        digest = _canonical_sha256(
            {
                "version": UBQ3_IDEMPOTENCY_VERSION,
                "invocation_id": invocation_id,
            }
        )
        return "ubq3:v1:" + digest, "ubq3r:" + digest

    @staticmethod
    def _attribution(
        *,
        invocation_id: str,
        request_fingerprint: str,
        capability_id: str,
        execution_id: str | None,
        tool_call_id: str | None,
        task_id: str | None,
        workflow_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        return {
            "version": UBQ3_IDEMPOTENCY_VERSION,
            "invocation_id": invocation_id,
            "request_fingerprint": request_fingerprint,
            "capability_id": capability_id,
            "execution_id": execution_id,
            "tool_call_id": tool_call_id,
            "task_id": task_id,
            "workflow_id": workflow_id,
            "session_id": session_id,
        }

    @staticmethod
    def _task_budget_tool_fingerprint(
        *,
        execution_id: str,
        tool_call_id: str,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> str:
        encoded = json.dumps(
            {
                "kind": "TOOL_CALL",
                "reservation_key": tool_call_id,
                "payload": {
                    "execution_id": execution_id,
                    "tool_call_id": tool_call_id,
                    "capability_id": capability_id,
                    "arguments": arguments,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _resolve_owner_in_uow(
        self,
        uow,
        *,
        identity: Identity,
        task_id: str | None,
    ) -> BudgetOwnerResolution:
        resolution = await self._owner_authority.resolve_budget_owner_in_uow(
            uow,
            identity,
        )
        if task_id is not None:
            binding = await uow.user_budgets.get_task_binding(task_id)
            if (
                binding is not None
                and str(binding.owner_user_id) != resolution.owner_user_id
            ):
                raise UserBudgetBindingDriftError(
                    "resolved tool quota owner disagrees with durable Task binding"
                )
        return resolution

    @staticmethod
    def _verify_direct_reservation(
        row,
        *,
        owner_user_id: str,
        invocation_id: str,
        capability_id: str,
        request_fingerprint: str,
        execution_id: str | None,
        tool_call_id: str | None,
        task_id: str | None,
        workflow_id: str | None,
        session_id: str | None,
    ) -> None:
        idempotency_key, reservation_id = UserToolQuotaService._identity(
            invocation_id
        )
        intent = UserBudgetReservationIntent(
            reservation_id=reservation_id,
            owner_user_id=owner_user_id,
            window_epoch=int(row.window_epoch),
            idempotency_key=idempotency_key,
            resource_kind=UserBudgetResourceKind.TOOL_CALL,
            capability_id=capability_id,
            reserved_amount_atomic=1,
            attribution=UserToolQuotaService._attribution(
                invocation_id=invocation_id,
                request_fingerprint=request_fingerprint,
                capability_id=capability_id,
                execution_id=execution_id,
                tool_call_id=tool_call_id,
                task_id=task_id,
                workflow_id=workflow_id,
                session_id=session_id,
            ),
        )
        if (
            str(row.reservation_id) != reservation_id
            or str(row.owner_user_id) != owner_user_id
            or str(row.idempotency_key) != idempotency_key
            or str(row.resource_kind) != UserBudgetResourceKind.TOOL_CALL.value
            or str(row.capability_id or "") != capability_id
            or int(row.reserved_amount_atomic) != 1
            or str(row.payload_fingerprint) != intent.payload_fingerprint
        ):
            raise UserToolQuotaConflictError(
                "logical invocation conflicts with existing UBQ-3 reservation"
            )

    async def _historical_bridge_admission_in_uow(
        self,
        uow,
        *,
        owner_user_id: str,
        invocation_id: str,
        capability_id: str,
        request_fingerprint: str,
        execution_id: str | None,
        tool_call_id: str | None,
        task_id: str | None,
        arguments: dict[str, Any],
    ) -> ToolQuotaAdmission | None:
        if not task_id or not execution_id or not tool_call_id:
            return None

        bridge = await uow.user_budgets.get_dual_accounting_receipt(
            task_id,
            "TOOL_CALL",
            tool_call_id,
            UserBudgetResourceKind.TOOL_CALL.value,
        )
        if bridge is None:
            return None

        binding = await uow.user_budgets.get_task_binding(task_id)
        execution = await uow.agents.get_execution(execution_id)
        tool_call = await uow.agents.get_tool_call(execution_id, tool_call_id)
        budget = await uow.agents.get_task_budget(task_id)
        if (
            binding is None
            or execution is None
            or tool_call is None
            or budget is None
        ):
            raise UserToolQuotaConflictError(
                "historical UBQ-2 bridge has incomplete Agent/TaskBudget lineage"
            )
        if (
            str(binding.owner_user_id) != owner_user_id
            or str(execution.task_id or "") != task_id
            or str(tool_call.invocation_id) != invocation_id
            or str(tool_call.tool_call_id) != tool_call_id
            or str(tool_call.capability_id) != capability_id
            or dict(tool_call.arguments or {}) != dict(arguments)
        ):
            raise UserToolQuotaConflictError(
                "historical UBQ-2 bridge lineage conflicts with logical invocation"
            )

        source = await uow.agents.get_task_budget_reservation(
            task_id,
            "TOOL_CALL",
            tool_call_id,
            expected_incarnation_generation=int(
                budget.incarnation_generation
            ),
        )
        expected_source_fingerprint = self._task_budget_tool_fingerprint(
            execution_id=execution_id,
            tool_call_id=tool_call_id,
            capability_id=capability_id,
            arguments=dict(arguments),
        )
        if (
            source is None
            or str(source.payload_fingerprint) != expected_source_fingerprint
            or str(bridge.source_payload_fingerprint)
            != expected_source_fingerprint
            or str(bridge.owner_user_id) != owner_user_id
            or str(bridge.capability_id or "") != capability_id
            or int(bridge.amount_atomic) != 1
            or bridge.ubq_idempotency_key is None
        ):
            raise UserToolQuotaConflictError(
                "historical UBQ-2 bridge does not prove exact charged lineage"
            )

        mirrored = await uow.user_budgets.get_reservation(
            owner_user_id,
            str(bridge.ubq_idempotency_key),
        )
        if (
            mirrored is None
            or str(mirrored.reservation_id)
            != str(bridge.ubq_reservation_id)
            or int(mirrored.window_epoch) != int(bridge.window_epoch)
            or str(mirrored.resource_kind)
            != UserBudgetResourceKind.TOOL_CALL.value
            or str(mirrored.capability_id or "") != capability_id
            or str(mirrored.payload_fingerprint)
            != str(bridge.ubq_payload_fingerprint)
            or str(mirrored.state)
            != UserBudgetReservationState.SETTLED.value
            or int(mirrored.settled_amount_atomic or 0) != 1
        ):
            raise UserToolQuotaConflictError(
                "historical UBQ-2 bridge references invalid settled reservation"
            )

        return ToolQuotaAdmission(
            owner_user_id=owner_user_id,
            invocation_id=invocation_id,
            capability_id=capability_id,
            request_fingerprint=request_fingerprint,
            idempotency_key=None,
            reservation_id=str(mirrored.reservation_id),
            window_epoch=int(mirrored.window_epoch),
            reservation_state=UserBudgetReservationState.SETTLED.value,
            historical_bridge=True,
        )

    async def resolve_owner(
        self,
        *,
        identity: Identity,
        task_id: str | None = None,
    ) -> BudgetOwnerResolution | None:
        """Resolve canonical budget owner without reserving or mutating quota."""
        if not self.enabled:
            return None
        async with self._uow_factory() as uow:
            resolution = await self._resolve_owner_in_uow(
                uow,
                identity=identity,
                task_id=task_id,
            )
            await uow.commit()
            return resolution

    async def find_tool_call_authority(
        self,
        *,
        owner_user_id: str,
        invocation_id: str,
        capability_id: str,
        request_fingerprint: str,
        arguments: dict[str, Any],
        execution_id: str | None,
        tool_call_id: str | None,
        workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> ToolQuotaAdmission | None:
        """Read existing direct/historical TOOL_CALL authority without mutation."""
        if not self.enabled:
            return None
        if (
            not owner_user_id
            or not invocation_id
            or not capability_id
            or not request_fingerprint
        ):
            raise UserToolQuotaConflictError(
                "quota authority lookup lacks durable logical identity"
            )

        idempotency_key, _reservation_id = self._identity(invocation_id)
        async with self._uow_factory() as uow:
            direct = await uow.user_budgets.get_reservation(
                owner_user_id,
                idempotency_key,
            )
            execution = None
            task_id = None
            if execution_id is not None:
                execution = await uow.agents.get_execution(execution_id)
                if execution is None:
                    raise UserToolQuotaConflictError(
                        "quota authority execution lineage is missing"
                    )
                task_id = (
                    str(execution.task_id)
                    if execution.task_id is not None
                    else None
                )

            if direct is not None:
                self._verify_direct_reservation(
                    direct,
                    owner_user_id=owner_user_id,
                    invocation_id=invocation_id,
                    capability_id=capability_id,
                    request_fingerprint=request_fingerprint,
                    execution_id=execution_id,
                    tool_call_id=tool_call_id,
                    task_id=task_id,
                    workflow_id=workflow_id,
                    session_id=session_id,
                )
                result = ToolQuotaAdmission(
                    owner_user_id=owner_user_id,
                    invocation_id=invocation_id,
                    capability_id=capability_id,
                    request_fingerprint=request_fingerprint,
                    idempotency_key=idempotency_key,
                    reservation_id=str(direct.reservation_id),
                    window_epoch=int(direct.window_epoch),
                    reservation_state=str(direct.state),
                    historical_bridge=False,
                )
                await uow.commit()
                return result

            if (
                execution is None
                or task_id is None
                or tool_call_id is None
            ):
                await uow.commit()
                return None

            historical = await self._historical_bridge_admission_in_uow(
                uow,
                owner_user_id=owner_user_id,
                invocation_id=invocation_id,
                capability_id=capability_id,
                request_fingerprint=request_fingerprint,
                execution_id=execution_id,
                tool_call_id=tool_call_id,
                task_id=task_id,
                arguments=arguments,
            )
            await uow.commit()
            return historical

    async def recover_tool_call(
        self,
        *,
        owner_user_id: str,
        invocation_id: str,
        capability_id: str,
        request_fingerprint: str,
        arguments: dict[str, Any],
        execution_id: str | None,
        tool_call_id: str | None,
        workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> ToolQuotaAdmission | None:
        """Recover existing unresolved UBQ authority for an R7 continuation."""
        authority = await self.find_tool_call_authority(
            owner_user_id=owner_user_id,
            invocation_id=invocation_id,
            capability_id=capability_id,
            request_fingerprint=request_fingerprint,
            arguments=arguments,
            execution_id=execution_id,
            tool_call_id=tool_call_id,
            workflow_id=workflow_id,
            session_id=session_id,
        )
        if authority is None:
            raise UserToolQuotaConflictError(
                "continuation has no exact UBQ charge authority"
            )
        if (
            not authority.historical_bridge
            and authority.reservation_state
            != UserBudgetReservationState.RESERVED.value
        ):
            raise UserToolQuotaConflictError(
                "continuation requires an unresolved RESERVED "
                "direct UBQ-3 reservation"
            )
        return authority

    async def reserve_tool_call(
        self,
        *,
        identity: Identity,
        invocation_id: str,
        capability_id: str,
        request_fingerprint: str,
        arguments: dict[str, Any],
        execution_id: str | None = None,
        tool_call_id: str | None = None,
        task_id: str | None = None,
        workflow_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> ToolQuotaAdmission | None:
        if not self.enabled:
            return None
        if not invocation_id or not capability_id or not request_fingerprint:
            raise ValueError(
                "UBQ-3 tool admission requires invocation_id, capability_id "
                "and request_fingerprint"
            )
        now = now or datetime.now(timezone.utc)
        idempotency_key, reservation_id = self._identity(invocation_id)
        attribution = self._attribution(
            invocation_id=invocation_id,
            request_fingerprint=request_fingerprint,
            capability_id=capability_id,
            execution_id=execution_id,
            tool_call_id=tool_call_id,
            task_id=task_id,
            workflow_id=workflow_id,
            session_id=session_id,
        )

        retries = max(1, int(self.settings.max_conflict_retries))
        for attempt_number in range(retries):
            try:
                async with self._uow_factory() as uow:
                    await uow.user_budgets.begin_write_intent()
                    resolution = await self._resolve_owner_in_uow(
                        uow,
                        identity=identity,
                        task_id=task_id,
                    )
                    owner = resolution.owner_user_id

                    # Replay authority precedes active-window lookup/rollover.
                    existing = await uow.user_budgets.get_reservation(
                        owner,
                        idempotency_key,
                    )
                    if existing is not None:
                        self._verify_direct_reservation(
                            existing,
                            owner_user_id=owner,
                            invocation_id=invocation_id,
                            capability_id=capability_id,
                            request_fingerprint=request_fingerprint,
                            execution_id=execution_id,
                            tool_call_id=tool_call_id,
                            task_id=task_id,
                            workflow_id=workflow_id,
                            session_id=session_id,
                        )
                        result = ToolQuotaAdmission(
                            owner_user_id=owner,
                            invocation_id=invocation_id,
                            capability_id=capability_id,
                            request_fingerprint=request_fingerprint,
                            idempotency_key=idempotency_key,
                            reservation_id=str(existing.reservation_id),
                            window_epoch=int(existing.window_epoch),
                            reservation_state=str(existing.state),
                        )
                        await uow.commit()
                        return result

                    historical = await self._historical_bridge_admission_in_uow(
                        uow,
                        owner_user_id=owner,
                        invocation_id=invocation_id,
                        capability_id=capability_id,
                        request_fingerprint=request_fingerprint,
                        execution_id=execution_id,
                        tool_call_id=tool_call_id,
                        task_id=task_id,
                        arguments=arguments,
                    )
                    if historical is not None:
                        await uow.commit()
                        return historical

                    account = await uow.user_budgets.get_account(
                        owner,
                        for_update=True,
                    )
                    if account is None or account.next_policy_id is None:
                        raise UserBudgetPolicyAuthorityConflictError(
                            "tool quota owner has no selected UBQ policy"
                        )

                    # PostgreSQL serializes new admissions on the owner account.
                    # Another transaction may have committed this exact logical
                    # reservation while we were waiting for that lock, so replay
                    # must be re-checked before rollover or counter mutation.
                    locked_replay = await uow.user_budgets.get_reservation(
                        owner,
                        idempotency_key,
                    )
                    if locked_replay is not None:
                        self._verify_direct_reservation(
                            locked_replay,
                            owner_user_id=owner,
                            invocation_id=invocation_id,
                            capability_id=capability_id,
                            request_fingerprint=request_fingerprint,
                            execution_id=execution_id,
                            tool_call_id=tool_call_id,
                            task_id=task_id,
                            workflow_id=workflow_id,
                            session_id=session_id,
                        )
                        result = ToolQuotaAdmission(
                            owner_user_id=owner,
                            invocation_id=invocation_id,
                            capability_id=capability_id,
                            request_fingerprint=request_fingerprint,
                            idempotency_key=idempotency_key,
                            reservation_id=str(locked_replay.reservation_id),
                            window_epoch=int(locked_replay.window_epoch),
                            reservation_state=str(locked_replay.state),
                        )
                        await uow.commit()
                        return result

                    window = await uow.user_budgets.get_active_window(owner)
                    if (
                        window is None
                        or now >= _record_utc(window.expires_at)
                    ):
                        window = await uow.user_budgets.rollover_window(
                            owner,
                            expected_account_revision=int(account.revision),
                            authoritative_server_now=now,
                        )

                    policy = await uow.user_budgets.get_policy(
                        owner,
                        str(window.governing_policy_id),
                    )
                    if (
                        policy is None
                        or str(policy.policy_fingerprint)
                        != str(window.governing_policy_fingerprint)
                    ):
                        raise UserBudgetPolicyAuthorityConflictError(
                            "active tool quota window has invalid policy authority"
                        )
                    if self._owner_authority.is_recognized_shadow_policy(policy):
                        raise UserBudgetPolicyAuthorityConflictError(
                            "automatic UBQ-2 shadow policy cannot authorize "
                            "canonical tool quota admission"
                        )

                    tool_usage = (
                        await uow.user_budgets.create_or_get_tool_usage(
                            owner,
                            int(window.epoch),
                            capability_id,
                        )
                    )
                    tool_limits = dict(policy.tool_limits_json or {})
                    per_limit = tool_limits.get(capability_id)
                    if per_limit is None:
                        per_limit = policy.default_per_tool_limit
                    total_limit = policy.max_tool_calls_total

                    per_exhausted = (
                        per_limit is not None
                        and int(tool_usage.used_calls)
                        + int(tool_usage.reserved_calls)
                        + 1
                        > int(per_limit)
                    )
                    total_exhausted = (
                        total_limit is not None
                        and int(window.tool_calls_used)
                        + int(window.tool_calls_reserved)
                        + 1
                        > int(total_limit)
                    )
                    if per_exhausted:
                        raise UserToolCapabilityQuotaExceededError(
                            f"capability_id={capability_id} quota exhausted"
                        )
                    if total_exhausted:
                        raise UserToolQuotaExceededError(
                            "total logical tool-call quota exhausted"
                        )

                    intent = UserBudgetReservationIntent(
                        reservation_id=reservation_id,
                        owner_user_id=owner,
                        window_epoch=int(window.epoch),
                        idempotency_key=idempotency_key,
                        resource_kind=UserBudgetResourceKind.TOOL_CALL,
                        capability_id=capability_id,
                        reserved_amount_atomic=1,
                        attribution=attribution,
                    )
                    reservation = (
                        await uow.user_budgets.create_or_get_reservation(
                            intent
                        )
                    )
                    self._verify_direct_reservation(
                        reservation,
                        owner_user_id=owner,
                        invocation_id=invocation_id,
                        capability_id=capability_id,
                        request_fingerprint=request_fingerprint,
                        execution_id=execution_id,
                        tool_call_id=tool_call_id,
                        task_id=task_id,
                        workflow_id=workflow_id,
                        session_id=session_id,
                    )

                    window = await uow.user_budgets.mutate_window_usage(
                        owner,
                        int(window.epoch),
                        expected_revision=int(window.revision),
                        tool_calls_reserved=(
                            int(window.tool_calls_reserved) + 1
                        ),
                    )
                    await uow.user_budgets.mutate_tool_usage(
                        owner,
                        int(window.epoch),
                        capability_id,
                        expected_revision=int(tool_usage.revision),
                        used_calls=int(tool_usage.used_calls),
                        reserved_calls=int(tool_usage.reserved_calls) + 1,
                    )
                    await uow.commit()
                    return ToolQuotaAdmission(
                        owner_user_id=owner,
                        invocation_id=invocation_id,
                        capability_id=capability_id,
                        request_fingerprint=request_fingerprint,
                        idempotency_key=idempotency_key,
                        reservation_id=str(reservation.reservation_id),
                        window_epoch=int(window.epoch),
                        reservation_state=UserBudgetReservationState.RESERVED.value,
                    )
            except (UserBudgetConflictError, UserBudgetSerializationError):
                if attempt_number + 1 >= retries:
                    raise
                continue
        raise AssertionError("unreachable UBQ-3 admission retry loop")

    async def _terminal_transition(
        self,
        admission: ToolQuotaAdmission | None,
        *,
        target_state: UserBudgetReservationState,
        now: datetime | None = None,
    ) -> ToolQuotaAdmission | None:
        if admission is None or admission.historical_bridge:
            return admission
        if admission.idempotency_key is None:
            raise UserToolQuotaConflictError(
                "direct UBQ-3 admission has no idempotency key"
            )
        if target_state not in {
            UserBudgetReservationState.SETTLED,
            UserBudgetReservationState.RELEASED,
        }:
            raise ValueError("UBQ-3 terminal transition must SETTLE or RELEASE")

        now = now or datetime.now(timezone.utc)
        retries = max(1, int(self.settings.max_conflict_retries))
        for attempt_number in range(retries):
            try:
                async with self._uow_factory() as uow:
                    await uow.user_budgets.begin_write_intent()
                    reservation = await uow.user_budgets.get_reservation(
                        admission.owner_user_id,
                        admission.idempotency_key,
                    )
                    if reservation is None:
                        raise UserToolQuotaConflictError(
                            "UBQ-3 reservation disappeared before terminal transition"
                        )
                    self._verify_direct_reservation(
                        reservation,
                        owner_user_id=admission.owner_user_id,
                        invocation_id=admission.invocation_id,
                        capability_id=admission.capability_id,
                        request_fingerprint=admission.request_fingerprint,
                        execution_id=(
                            reservation.attribution_json or {}
                        ).get("execution_id"),
                        tool_call_id=(
                            reservation.attribution_json or {}
                        ).get("tool_call_id"),
                        task_id=(
                            reservation.attribution_json or {}
                        ).get("task_id"),
                        workflow_id=(
                            reservation.attribution_json or {}
                        ).get("workflow_id"),
                        session_id=(
                            reservation.attribution_json or {}
                        ).get("session_id"),
                    )
                    if str(reservation.state) == target_state.value:
                        await uow.commit()
                        return ToolQuotaAdmission(
                            owner_user_id=admission.owner_user_id,
                            invocation_id=admission.invocation_id,
                            capability_id=admission.capability_id,
                            request_fingerprint=admission.request_fingerprint,
                            idempotency_key=admission.idempotency_key,
                            reservation_id=admission.reservation_id,
                            window_epoch=admission.window_epoch,
                            reservation_state=target_state.value,
                            historical_bridge=False,
                        )
                    if (
                        str(reservation.state)
                        != UserBudgetReservationState.RESERVED.value
                    ):
                        raise UserToolQuotaConflictError(
                            "UBQ-3 reservation is already terminal/incompatible"
                        )

                    window = await uow.user_budgets.get_window(
                        admission.owner_user_id,
                        int(reservation.window_epoch),
                    )
                    tool_usage = await uow.user_budgets.get_tool_usage(
                        admission.owner_user_id,
                        int(reservation.window_epoch),
                        admission.capability_id,
                    )
                    if (
                        window is None
                        or tool_usage is None
                        or int(window.tool_calls_reserved) <= 0
                        or int(tool_usage.reserved_calls) <= 0
                    ):
                        raise UserToolQuotaConflictError(
                            "UBQ-3 reserved counters are inconsistent"
                        )

                    if target_state is UserBudgetReservationState.SETTLED:
                        await uow.user_budgets.mutate_window_usage(
                            admission.owner_user_id,
                            int(window.epoch),
                            expected_revision=int(window.revision),
                            tool_calls_used=int(window.tool_calls_used) + 1,
                            tool_calls_reserved=(
                                int(window.tool_calls_reserved) - 1
                            ),
                        )
                        await uow.user_budgets.mutate_tool_usage(
                            admission.owner_user_id,
                            int(window.epoch),
                            admission.capability_id,
                            expected_revision=int(tool_usage.revision),
                            used_calls=int(tool_usage.used_calls) + 1,
                            reserved_calls=int(tool_usage.reserved_calls) - 1,
                        )
                        transitioned = (
                            await uow.user_budgets.transition_reservation(
                                admission.owner_user_id,
                                admission.idempotency_key,
                                expected_revision=int(reservation.revision),
                                target_state=target_state,
                                settled_amount_atomic=1,
                                settled_at=now,
                            )
                        )
                    else:
                        await uow.user_budgets.mutate_window_usage(
                            admission.owner_user_id,
                            int(window.epoch),
                            expected_revision=int(window.revision),
                            tool_calls_reserved=(
                                int(window.tool_calls_reserved) - 1
                            ),
                        )
                        await uow.user_budgets.mutate_tool_usage(
                            admission.owner_user_id,
                            int(window.epoch),
                            admission.capability_id,
                            expected_revision=int(tool_usage.revision),
                            used_calls=int(tool_usage.used_calls),
                            reserved_calls=int(tool_usage.reserved_calls) - 1,
                        )
                        transitioned = (
                            await uow.user_budgets.transition_reservation(
                                admission.owner_user_id,
                                admission.idempotency_key,
                                expected_revision=int(reservation.revision),
                                target_state=target_state,
                            )
                        )
                    await uow.commit()
                    return ToolQuotaAdmission(
                        owner_user_id=admission.owner_user_id,
                        invocation_id=admission.invocation_id,
                        capability_id=admission.capability_id,
                        request_fingerprint=admission.request_fingerprint,
                        idempotency_key=admission.idempotency_key,
                        reservation_id=admission.reservation_id,
                        window_epoch=admission.window_epoch,
                        reservation_state=str(transitioned.state),
                        historical_bridge=False,
                    )
            except (UserBudgetConflictError, UserBudgetSerializationError):
                if attempt_number + 1 >= retries:
                    raise
                continue
        raise AssertionError("unreachable UBQ-3 terminal retry loop")

    async def settle_tool_call(
        self,
        admission: ToolQuotaAdmission | None,
        *,
        now: datetime | None = None,
    ) -> ToolQuotaAdmission | None:
        return await self._terminal_transition(
            admission,
            target_state=UserBudgetReservationState.SETTLED,
            now=now,
        )

    async def release_tool_call(
        self,
        admission: ToolQuotaAdmission | None,
    ) -> ToolQuotaAdmission | None:
        return await self._terminal_transition(
            admission,
            target_state=UserBudgetReservationState.RELEASED,
        )
