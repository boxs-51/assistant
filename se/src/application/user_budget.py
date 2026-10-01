from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.user_budget import (
    UserBudgetPolicy,
    UserBudgetReservationIntent,
    UserBudgetReservationState,
    UserBudgetResourceKind,
    decimal_to_atomic,
)
from se.src.infrastructure.storage.repositories.user_budget import (
    UserBudgetConflictError,
    UserBudgetIntegrityError,
    UserBudgetSerializationError,
)


UBQ2_ENROLLMENT_VERSION = "ubq2-v1"
UBQ2_IDEMPOTENCY_VERSION = "ubq2-v1"
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


class UserBudgetDualAccountingError(RuntimeError):
    code = "USER_BUDGET_DUAL_ACCOUNTING_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class UserBudgetOwnerUnresolvedError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_OWNER_UNRESOLVED"


class UserBudgetParentOwnerMismatchError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_PARENT_OWNER_MISMATCH"


class UserBudgetParentUnboundError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_PARENT_UNBOUND"


class UserBudgetPolicyAuthorityConflictError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_POLICY_AUTHORITY_CONFLICT"


class UserBudgetBindingDriftError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_BINDING_DRIFT"


class UserBudgetUnsupportedLegacyMirrorError(UserBudgetDualAccountingError):
    code = "USER_BUDGET_UNSUPPORTED_LEGACY_MIRROR"


@dataclass(frozen=True, slots=True)
class DualAccountingSettings:
    enabled: bool = False
    policy_version: str = "ubq2-shadow-v1"
    window_duration_seconds: int = 86400


@dataclass(frozen=True, slots=True)
class BudgetOwnerResolution:
    owner_user_id: str
    source_auth_type: str
    source_api_key_id: str | None
    source_application_id: str | None
    source_organization_id: str | None
    resolution_fingerprint: str


@dataclass(frozen=True, slots=True)
class TaskBindingSnapshot:
    task_id: str
    owner_user_id: str
    enrollment_version: str
    resolution_fingerprint: str


@dataclass(frozen=True, slots=True)
class MirrorDimension:
    resource_kind: str
    amount_atomic: int
    capability_id: str | None = None


@dataclass(frozen=True, slots=True)
class DualAccountingReconciliationItem:
    status: str
    task_id: str
    bridge_receipt_id: str | None = None
    mirror_dimension: str | None = None


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UserBudgetDualAccountingService:
    """UBQ-2 application authority.

    TaskBudget remains admission authority. This service owns only enrollment,
    unbounded shadow-policy provisioning, exact mirror receipts and read-only
    binding preflight.
    """

    def __init__(self, uow_factory, settings: DualAccountingSettings) -> None:
        self._uow_factory = uow_factory
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    @staticmethod
    def is_retryable_error(exc: BaseException) -> bool:
        return isinstance(
            exc,
            (UserBudgetConflictError, UserBudgetSerializationError),
        )

    async def reconcile_task(
        self,
        task_id: str,
    ) -> tuple[DualAccountingReconciliationItem, ...]:
        """Read-only UBQ-2 provenance reconciliation for one Task.

        Missing source rows are deliberately not called canonical GC because
        R11 persists no durable GC receipt/tombstone proving why they vanished.
        """
        async with self._uow_factory() as uow:
            binding = await uow.user_budgets.get_task_binding(task_id)
            if binding is None:
                await uow.commit()
                return (
                    DualAccountingReconciliationItem(
                        status="LEGACY_UNBOUND",
                        task_id=task_id,
                    ),
                )

            bridges = await uow.user_budgets.list_dual_accounting_receipts(
                task_id
            )
            task = await uow.agents.get_task(task_id)
            if task is None:
                await uow.commit()
                if not bridges:
                    return (
                        DualAccountingReconciliationItem(
                            status="SOURCE_ABSENT_UNPROVEN",
                            task_id=task_id,
                        ),
                    )
                return tuple(
                    DualAccountingReconciliationItem(
                        status="SOURCE_ABSENT_UNPROVEN",
                        task_id=task_id,
                        bridge_receipt_id=str(row.bridge_receipt_id),
                        mirror_dimension=str(row.mirror_dimension),
                    )
                    for row in bridges
                )

            budget = await uow.agents.get_task_budget(task_id)
            if budget is None:
                await uow.commit()
                return (
                    DualAccountingReconciliationItem(
                        status="SOURCE_RESERVATION_MISSING",
                        task_id=task_id,
                    ),
                )
            generation = int(budget.incarnation_generation)

            if not bridges:
                await uow.commit()
                return (
                    DualAccountingReconciliationItem(
                        status="OK",
                        task_id=task_id,
                    ),
                )

            items: list[DualAccountingReconciliationItem] = []
            for bridge in bridges:
                status = "OK"
                source = await uow.agents.get_task_budget_reservation(
                    task_id,
                    str(bridge.task_budget_kind),
                    str(bridge.task_budget_reservation_key),
                    expected_incarnation_generation=generation,
                )
                if source is None:
                    status = "SOURCE_RESERVATION_MISSING"
                elif (
                    str(source.payload_fingerprint)
                    != str(bridge.source_payload_fingerprint)
                ):
                    status = "PAYLOAD_MISMATCH"
                elif str(bridge.mirror_dimension) != "NO_CHARGE":
                    ubq = await uow.user_budgets.get_reservation(
                        str(bridge.owner_user_id),
                        str(bridge.ubq_idempotency_key),
                    )
                    if ubq is None:
                        status = "MISSING_UBQ"
                    elif (
                        str(ubq.reservation_id)
                        != str(bridge.ubq_reservation_id)
                        or int(ubq.window_epoch) != int(bridge.window_epoch)
                        or str(ubq.resource_kind)
                        != str(bridge.mirror_dimension)
                        or str(ubq.payload_fingerprint)
                        != str(bridge.ubq_payload_fingerprint)
                        or str(ubq.state)
                        != UserBudgetReservationState.SETTLED.value
                        or int(ubq.settled_amount_atomic or 0)
                        != int(bridge.amount_atomic)
                        or (ubq.capability_id or None)
                        != (bridge.capability_id or None)
                    ):
                        status = "PAYLOAD_MISMATCH"

                items.append(
                    DualAccountingReconciliationItem(
                        status=status,
                        task_id=task_id,
                        bridge_receipt_id=str(bridge.bridge_receipt_id),
                        mirror_dimension=str(bridge.mirror_dimension),
                    )
                )
            await uow.commit()
            return tuple(items)

    async def preflight_binding(
        self,
        task_id: str,
    ) -> TaskBindingSnapshot | None:
        async with self._uow_factory() as uow:
            row = await uow.user_budgets.get_task_binding(task_id)
            if row is None:
                await uow.commit()
                return None
            snapshot = self.binding_snapshot(row)
            await uow.commit()
            return snapshot

    async def resolve_budget_owner_in_uow(
        self,
        uow,
        identity: Identity,
    ) -> BudgetOwnerResolution:
        if identity.auth_type in {"admin_key", "guest"}:
            raise UserBudgetOwnerUnresolvedError(
                "principal type cannot own renewable user budget"
            )

        if identity.user_id:
            user = await uow.users.get_by_id(str(identity.user_id))
            if user is None:
                raise UserBudgetOwnerUnresolvedError(
                    "identity user_id has no durable User"
                )
            owner = str(user.id)
            return self._resolution(
                owner,
                identity,
                source_api_key_id=identity.api_key_id,
                source_application_id=identity.application_id,
                source_organization_id=identity.organization_id,
            )

        if identity.auth_type != "api_key" or not identity.api_key_id:
            raise UserBudgetOwnerUnresolvedError(
                "principal has no canonical renewable budget owner"
            )

        key = await uow.api_keys.get_by_id(str(identity.api_key_id))
        if key is None or str(getattr(key, "status", "")) != "active":
            raise UserBudgetOwnerUnresolvedError(
                "API key identity is missing or inactive"
            )
        application = await uow.applications.get_by_id(str(key.application_id))
        if application is None:
            raise UserBudgetOwnerUnresolvedError(
                "API key application is missing"
            )
        organization = await uow.organizations.get_by_id(
            str(application.organization_id)
        )
        if organization is None:
            raise UserBudgetOwnerUnresolvedError(
                "API key organization is missing"
            )

        if (
            str(identity.application_id or "") != str(application.id)
            or str(identity.organization_id or "") != str(organization.id)
        ):
            raise UserBudgetOwnerUnresolvedError(
                "authenticated API key lineage disagrees with durable lineage"
            )

        owner = str(organization.owner_id)
        if await uow.users.get_by_id(owner) is None:
            raise UserBudgetOwnerUnresolvedError(
                "organization owner has no durable User"
            )
        return self._resolution(
            owner,
            identity,
            source_api_key_id=str(key.id),
            source_application_id=str(application.id),
            source_organization_id=str(organization.id),
        )

    def _resolution(
        self,
        owner_user_id: str,
        identity: Identity,
        *,
        source_api_key_id: str | None,
        source_application_id: str | None,
        source_organization_id: str | None,
    ) -> BudgetOwnerResolution:
        payload = {
            "version": UBQ2_ENROLLMENT_VERSION,
            "owner_user_id": owner_user_id,
            "source_auth_type": str(identity.auth_type),
            "source_api_key_id": source_api_key_id,
            "source_application_id": source_application_id,
            "source_organization_id": source_organization_id,
        }
        return BudgetOwnerResolution(
            owner_user_id=owner_user_id,
            source_auth_type=str(identity.auth_type),
            source_api_key_id=source_api_key_id,
            source_application_id=source_application_id,
            source_organization_id=source_organization_id,
            resolution_fingerprint=_canonical_sha256(payload),
        )

    def shadow_policy(self, owner_user_id: str) -> UserBudgetPolicy:
        identity_payload = {
            "authority": "UBQ2_SHADOW_ACCOUNTING",
            "owner_user_id": owner_user_id,
            "policy_version": self.settings.policy_version,
            "window_duration_seconds": self.settings.window_duration_seconds,
            "unbounded": True,
        }
        policy_id = "ubq2-shadow-" + _canonical_sha256(identity_payload)[:48]
        return UserBudgetPolicy(
            policy_id=policy_id,
            owner_user_id=owner_user_id,
            policy_version=self.settings.policy_version,
            window_duration_seconds=self.settings.window_duration_seconds,
            max_compute_units=None,
            max_inference_calls=None,
            max_input_tokens=None,
            max_output_tokens=None,
            max_total_tokens=None,
            max_tool_calls_total=None,
            default_per_tool_limit=None,
            tool_limits={},
            max_cost_usd=None,
        )

    def _is_recognized_shadow_policy(self, row) -> bool:
        version = str(row.policy_version)
        if not version.startswith("ubq2-shadow-"):
            return False
        if any(
            getattr(row, field) is not None
            for field in (
                "max_compute_atomic",
                "max_inference_calls",
                "max_input_tokens",
                "max_output_tokens",
                "max_total_tokens",
                "max_tool_calls_total",
                "default_per_tool_limit",
                "max_cost_usd_atomic",
            )
        ):
            return False
        if dict(row.tool_limits_json or {}) != {}:
            return False

        identity_payload = {
            "authority": "UBQ2_SHADOW_ACCOUNTING",
            "owner_user_id": str(row.owner_user_id),
            "policy_version": version,
            "window_duration_seconds": int(row.window_duration_seconds),
            "unbounded": True,
        }
        expected_id = "ubq2-shadow-" + _canonical_sha256(identity_payload)[:48]
        if str(row.policy_id) != expected_id:
            return False

        expected = UserBudgetPolicy(
            policy_id=expected_id,
            owner_user_id=str(row.owner_user_id),
            policy_version=version,
            window_duration_seconds=int(row.window_duration_seconds),
            max_compute_units=None,
            max_inference_calls=None,
            max_input_tokens=None,
            max_output_tokens=None,
            max_total_tokens=None,
            max_tool_calls_total=None,
            default_per_tool_limit=None,
            tool_limits={},
            max_cost_usd=None,
        )
        return str(row.policy_fingerprint) == expected.policy_fingerprint

    async def ensure_shadow_account_in_uow(
        self,
        uow,
        owner_user_id: str,
    ):
        expected = self.shadow_policy(owner_user_id)
        policy = await uow.user_budgets.create_or_get_immutable_policy(expected)
        account = await uow.user_budgets.create_or_get_account(owner_user_id)

        if account.next_policy_id is None:
            account = await uow.user_budgets.select_next_policy(
                owner_user_id,
                expected_revision=int(account.revision),
                next_policy_id=policy.policy_id,
            )
        else:
            selected = await uow.user_budgets.get_policy(
                owner_user_id,
                str(account.next_policy_id),
            )
            if selected is None or not self._is_recognized_shadow_policy(selected):
                raise UserBudgetPolicyAuthorityConflictError(
                    "mirror-only accounting cannot overwrite finite/external policy authority"
                )

        active = await uow.user_budgets.get_active_window(owner_user_id)
        if active is not None:
            governing = await uow.user_budgets.get_policy(
                owner_user_id,
                str(active.governing_policy_id),
            )
            if (
                governing is None
                or not self._is_recognized_shadow_policy(governing)
                or str(active.governing_policy_fingerprint)
                != str(governing.policy_fingerprint)
            ):
                raise UserBudgetPolicyAuthorityConflictError(
                    "ACTIVE mirror-only window is not governed by a recognized shadow policy"
                )
        return account

    async def prepare_enrollment_in_uow(
        self,
        uow,
        *,
        parent_task_id: str | None,
        identity: Identity,
    ) -> BudgetOwnerResolution:
        resolution = await self.resolve_budget_owner_in_uow(uow, identity)
        await self.ensure_shadow_account_in_uow(
            uow,
            resolution.owner_user_id,
        )

        if parent_task_id is not None:
            parent_binding = await uow.user_budgets.get_task_binding(
                str(parent_task_id)
            )
            if parent_binding is None:
                raise UserBudgetParentUnboundError(
                    "enrolled child requires a bound parent task"
                )
            if str(parent_binding.owner_user_id) != resolution.owner_user_id:
                raise UserBudgetParentOwnerMismatchError(
                    "child budget owner differs from parent budget owner"
                )
        return resolution

    async def bind_prepared_task_in_uow(
        self,
        uow,
        *,
        task_id: str,
        resolution: BudgetOwnerResolution,
    ) -> TaskBindingSnapshot:
        row = await uow.user_budgets.create_task_binding(
            task_id=task_id,
            owner_user_id=resolution.owner_user_id,
            enrollment_version=UBQ2_ENROLLMENT_VERSION,
            source_auth_type=resolution.source_auth_type,
            source_api_key_id=resolution.source_api_key_id,
            source_application_id=resolution.source_application_id,
            source_organization_id=resolution.source_organization_id,
            resolution_fingerprint=resolution.resolution_fingerprint,
        )
        return self.binding_snapshot(row)

    @staticmethod
    def binding_snapshot(row) -> TaskBindingSnapshot:
        return TaskBindingSnapshot(
            task_id=str(row.task_id),
            owner_user_id=str(row.owner_user_id),
            enrollment_version=str(row.enrollment_version),
            resolution_fingerprint=str(row.resolution_fingerprint),
        )

    @staticmethod
    def require_same_binding(
        expected: TaskBindingSnapshot,
        row,
    ) -> None:
        if (
            str(row.task_id) != expected.task_id
            or str(row.owner_user_id) != expected.owner_user_id
            or str(row.enrollment_version) != expected.enrollment_version
            or str(row.resolution_fingerprint) != expected.resolution_fingerprint
        ):
            raise UserBudgetBindingDriftError(
                "durable task binding changed between preflight and mutation"
            )

    def _mirror_identity(
        self,
        *,
        owner_user_id: str,
        task_id: str,
        task_budget_kind: str,
        reservation_key: str,
        resource_kind: str,
    ) -> dict[str, Any]:
        return {
            "version": UBQ2_IDEMPOTENCY_VERSION,
            "owner_user_id": owner_user_id,
            "task_id": task_id,
            "task_budget_kind": task_budget_kind,
            "task_budget_reservation_key": reservation_key,
            "resource_kind": resource_kind,
        }

    async def _ensure_active_shadow_window(
        self,
        uow,
        owner_user_id: str,
        *,
        now: datetime,
    ):
        account = await self.ensure_shadow_account_in_uow(uow, owner_user_id)
        active = await uow.user_budgets.get_active_window(owner_user_id)
        if active is not None and now < _record_utc(active.expires_at):
            return active

        return await uow.user_budgets.rollover_window(
            owner_user_id,
            expected_account_revision=int(account.revision),
            authoritative_server_now=now,
        )

    async def require_mirror_replay_in_uow(
        self,
        uow,
        *,
        binding: TaskBindingSnapshot,
        task_budget_kind: str,
        reservation_key: str,
        source_payload_fingerprint: str,
        dimensions: Iterable[MirrorDimension],
    ) -> tuple[Any, ...]:
        rows = []
        for dimension in tuple(dimensions):
            existing = await uow.user_budgets.get_dual_accounting_receipt(
                binding.task_id,
                task_budget_kind,
                reservation_key,
                dimension.resource_kind,
            )
            if existing is None:
                raise UserBudgetIntegrityError(
                    "committed TaskBudget resource reservation is missing its UBQ bridge"
                )
            if (
                str(existing.owner_user_id) != binding.owner_user_id
                or str(existing.source_payload_fingerprint)
                != source_payload_fingerprint
                or int(existing.amount_atomic) != int(dimension.amount_atomic)
                or (existing.capability_id or None)
                != (dimension.capability_id or None)
            ):
                raise UserBudgetConflictError(
                    "dual-accounting replay conflicts with immutable bridge receipt"
                )
            rows.append(existing)
        return tuple(rows)

    @staticmethod
    def inference_dimensions() -> tuple[MirrorDimension, ...]:
        return (
            MirrorDimension(UserBudgetResourceKind.INFERENCE_CALL.value, 1),
        )

    @staticmethod
    def tool_call_dimensions(
        capability_id: str,
    ) -> tuple[MirrorDimension, ...]:
        if not capability_id:
            raise ValueError("canonical capability_id is required")
        return (
            MirrorDimension(
                UserBudgetResourceKind.TOOL_CALL.value,
                1,
                capability_id,
            ),
        )

    async def mirror_resource_in_uow(
        self,
        uow,
        *,
        binding: TaskBindingSnapshot,
        task_budget_kind: str,
        reservation_key: str,
        source_payload_fingerprint: str,
        dimensions: Iterable[MirrorDimension],
        now: datetime | None = None,
    ) -> tuple[Any, ...]:
        dimensions = tuple(dimensions)
        if not dimensions:
            raise ValueError("dual accounting mirror requires at least one dimension")
        now = now or datetime.now(timezone.utc)

        # Replay authority is checked before active-window lookup/rollover.
        existing_rows = []
        missing = []
        for dimension in dimensions:
            existing = await uow.user_budgets.get_dual_accounting_receipt(
                binding.task_id,
                task_budget_kind,
                reservation_key,
                dimension.resource_kind,
            )
            if existing is None:
                missing.append(dimension)
                continue
            if (
                str(existing.owner_user_id) != binding.owner_user_id
                or str(existing.source_payload_fingerprint)
                != source_payload_fingerprint
                or int(existing.amount_atomic) != int(dimension.amount_atomic)
                or (existing.capability_id or None) != (dimension.capability_id or None)
            ):
                raise UserBudgetConflictError(
                    "dual-accounting replay conflicts with immutable bridge receipt"
                )
            existing_rows.append(existing)

        if not missing:
            return tuple(existing_rows)
        if existing_rows:
            raise UserBudgetConflictError(
                "dual-accounting logical operation is only partially mirrored"
            )

        if len(dimensions) == 1 and dimensions[0].resource_kind == "NO_CHARGE":
            dimension = dimensions[0]
            identity = self._mirror_identity(
                owner_user_id=binding.owner_user_id,
                task_id=binding.task_id,
                task_budget_kind=task_budget_kind,
                reservation_key=reservation_key,
                resource_kind="NO_CHARGE",
            )
            digest = _canonical_sha256(identity)
            row = await uow.user_budgets.create_or_get_dual_accounting_receipt(
                {
                    "bridge_receipt_id": "ubq2b:" + digest,
                    "owner_user_id": binding.owner_user_id,
                    "task_id": binding.task_id,
                    "task_budget_kind": task_budget_kind,
                    "task_budget_reservation_key": reservation_key,
                    "mirror_dimension": "NO_CHARGE",
                    "source_payload_fingerprint": source_payload_fingerprint,
                    "window_epoch": None,
                    "ubq_reservation_id": None,
                    "ubq_idempotency_key": None,
                    "ubq_payload_fingerprint": None,
                    "amount_atomic": 0,
                    "capability_id": None,
                }
            )
            return (row,)

        window = await self._ensure_active_shadow_window(
            uow,
            binding.owner_user_id,
            now=now,
        )
        current_window = window
        rows = []

        for dimension in dimensions:
            kind = UserBudgetResourceKind(dimension.resource_kind)
            amount = int(dimension.amount_atomic)
            if amount <= 0:
                raise ValueError("charged UBQ mirror amount must be positive")
            if kind is UserBudgetResourceKind.TOOL_CALL and not dimension.capability_id:
                raise ValueError("TOOL_CALL mirror requires capability_id")
            if kind is not UserBudgetResourceKind.TOOL_CALL and dimension.capability_id:
                raise ValueError("non-tool mirror cannot carry capability_id")

            identity = self._mirror_identity(
                owner_user_id=binding.owner_user_id,
                task_id=binding.task_id,
                task_budget_kind=task_budget_kind,
                reservation_key=reservation_key,
                resource_kind=kind.value,
            )
            digest = _canonical_sha256(identity)
            idempotency_key = "ubq2:v1:" + digest
            reservation_id = "ubq2r:" + digest
            attribution = {
                "version": UBQ2_IDEMPOTENCY_VERSION,
                "task_id": binding.task_id,
                "task_budget_kind": task_budget_kind,
                "task_budget_reservation_key": reservation_key,
                "resource_kind": kind.value,
            }
            if dimension.capability_id is not None:
                attribution["capability_id"] = dimension.capability_id

            intent = UserBudgetReservationIntent(
                reservation_id=reservation_id,
                owner_user_id=binding.owner_user_id,
                window_epoch=int(current_window.epoch),
                idempotency_key=idempotency_key,
                resource_kind=kind,
                capability_id=dimension.capability_id,
                reserved_amount_atomic=amount,
                attribution=attribution,
            )
            reservation = await uow.user_budgets.create_or_get_reservation(intent)

            usage_targets: dict[str, int] = {}
            if kind is UserBudgetResourceKind.INFERENCE_CALL:
                usage_targets["inference_used"] = (
                    int(current_window.inference_used) + amount
                )
            elif kind is UserBudgetResourceKind.TOTAL_TOKEN:
                usage_targets["total_tokens_used"] = (
                    int(current_window.total_tokens_used) + amount
                )
            elif kind is UserBudgetResourceKind.COST_USD:
                usage_targets["cost_used_atomic"] = (
                    int(current_window.cost_used_atomic) + amount
                )
            elif kind is UserBudgetResourceKind.TOOL_CALL:
                usage_targets["tool_calls_used"] = (
                    int(current_window.tool_calls_used) + amount
                )
            else:
                raise ValueError(
                    f"UBQ-2 does not mirror resource kind {kind.value}"
                )

            current_window = await uow.user_budgets.mutate_window_usage(
                binding.owner_user_id,
                int(current_window.epoch),
                expected_revision=int(current_window.revision),
                **usage_targets,
            )

            if kind is UserBudgetResourceKind.TOOL_CALL:
                tool_usage = await uow.user_budgets.create_or_get_tool_usage(
                    binding.owner_user_id,
                    int(current_window.epoch),
                    str(dimension.capability_id),
                )
                await uow.user_budgets.mutate_tool_usage(
                    binding.owner_user_id,
                    int(current_window.epoch),
                    str(dimension.capability_id),
                    expected_revision=int(tool_usage.revision),
                    used_calls=int(tool_usage.used_calls) + amount,
                    reserved_calls=int(tool_usage.reserved_calls),
                )

            settled = await uow.user_budgets.transition_reservation(
                binding.owner_user_id,
                idempotency_key,
                expected_revision=int(reservation.revision),
                target_state=UserBudgetReservationState.SETTLED,
                settled_amount_atomic=amount,
                settled_at=now,
            )

            bridge = await uow.user_budgets.create_or_get_dual_accounting_receipt(
                {
                    "bridge_receipt_id": "ubq2b:" + digest,
                    "owner_user_id": binding.owner_user_id,
                    "task_id": binding.task_id,
                    "task_budget_kind": task_budget_kind,
                    "task_budget_reservation_key": reservation_key,
                    "mirror_dimension": kind.value,
                    "source_payload_fingerprint": source_payload_fingerprint,
                    "window_epoch": int(current_window.epoch),
                    "ubq_reservation_id": str(settled.reservation_id),
                    "ubq_idempotency_key": str(settled.idempotency_key),
                    "ubq_payload_fingerprint": str(settled.payload_fingerprint),
                    "amount_atomic": amount,
                    "capability_id": dimension.capability_id,
                }
            )
            rows.append(bridge)

        return tuple(rows)

    @staticmethod
    def usage_dimensions(
        *,
        tokens: int,
        cost_usd: Any,
    ) -> tuple[MirrorDimension, ...]:
        dimensions: list[MirrorDimension] = []
        if tokens > 0:
            dimensions.append(
                MirrorDimension(
                    UserBudgetResourceKind.TOTAL_TOKEN.value,
                    int(tokens),
                )
            )
        cost_atomic = decimal_to_atomic(cost_usd)
        if cost_atomic > 0:
            dimensions.append(
                MirrorDimension(
                    UserBudgetResourceKind.COST_USD.value,
                    cost_atomic,
                )
            )
        if not dimensions:
            return (MirrorDimension("NO_CHARGE", 0),)
        return tuple(dimensions)


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
