from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.user_budget import (
    UserBudgetReservationIntent,
    UserBudgetReservationState,
    UserBudgetResourceKind,
    canonical_decimal_string,
    decimal_to_atomic,
)
from se.src.infrastructure.storage.repositories.user_budget import (
    UserBudgetConflictError,
    UserBudgetSerializationError,
)

from .user_budget import (
    UserBudgetBindingDriftError,
    UserBudgetDualAccountingService,
    UserBudgetPolicyAuthorityConflictError,
    _canonical_sha256,
    _record_utc,
)


UBQ4_RESERVATION_VERSION = "ubq4-v1"
UBQ4_FINGERPRINT_VERSION = "ubq4-logical-fingerprint-v1"
UBQ4_SKILL_ID_VERSION = "ubq4-skill-inf-v1"


class UserInferenceQuotaError(RuntimeError):
    code = "USER_INFERENCE_QUOTA_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class UserInferenceQuotaContextError(UserInferenceQuotaError):
    code = "USER_INFERENCE_QUOTA_CONTEXT_INVALID"


class UserInferenceQuotaConflictError(UserInferenceQuotaError):
    code = "USER_INFERENCE_QUOTA_CONFLICT"


class UserInferenceQuotaExceededError(UserInferenceQuotaError):
    code = "USER_INFERENCE_QUOTA_EXHAUSTED"


class UserTokenQuotaExceededError(UserInferenceQuotaError):
    code = "USER_TOKEN_QUOTA_EXHAUSTED"


class UserComputeQuotaExceededError(UserInferenceQuotaError):
    code = "USER_COMPUTE_QUOTA_EXHAUSTED"


class UserCostQuotaExceededError(UserInferenceQuotaError):
    code = "USER_COST_QUOTA_EXHAUSTED"


class UserInferenceEstimateUnavailableError(UserInferenceQuotaError):
    code = "USER_INFERENCE_ESTIMATE_UNAVAILABLE"


class UserBudgetUnsupportedGovernedOperationError(UserInferenceQuotaError):
    code = "USER_BUDGET_UNSUPPORTED_GOVERNED_OPERATION"


@dataclass(frozen=True, slots=True)
class InferenceQuotaSettings:
    enabled: bool = False
    max_conflict_retries: int = 8
    estimator_policy_version: str = "ubq4-estimator-v1"
    usage_normalization_version: str = "ubq4-usage-v1"
    default_output_token_reservation: int = 4096
    compute_units_per_1k_tokens: Decimal | None = None
    cost_usd_per_1k_tokens: Decimal | None = None


@dataclass(frozen=True, slots=True)
class InferenceQuotaContext:
    budget_identity: Identity
    logical_request_id: str
    logical_request_fingerprint: str | None
    source_surface: str
    owner_user_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    workflow_id: str | None = None
    iteration: int | None = None
    agent_iteration_id: str | None = None
    outer_request_id: str | None = None
    capability_invocation_id: str | None = None
    estimator_policy_identity: str = ""
    usage_normalization_identity: str = ""


@dataclass(frozen=True, slots=True)
class InferenceQuotaEstimate:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    compute_units: Decimal | None = None
    cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class NormalizedInferenceUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    compute_units: Decimal | None
    cost_usd: Decimal | None
    normalization_identity: str
    provider: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class InferenceQuotaAdmission:
    owner_user_id: str
    logical_request_id: str
    logical_request_fingerprint: str
    window_epoch: int
    reservation_keys: Mapping[str, str]
    replayed: bool = False
    inference_reservation_state: str = UserBudgetReservationState.RESERVED.value


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, float):
        # Do not fingerprint binary-float object identity. Convert the
        # provider-neutral semantic value to a canonical decimal string.
        return canonical_decimal_string(Decimal(str(value)))
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if hasattr(value, "model_dump"):
        return _canonicalize(value.model_dump(mode="json", exclude_none=True))
    raise TypeError(f"unsupported UBQ-4 canonical value: {type(value)!r}")


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_skill_inference_request_id(
    invocation_id: str,
    *,
    ordinal: int = 1,
) -> str:
    if not invocation_id:
        raise ValueError("Skill inference requires a non-empty invocation_id")
    if ordinal <= 0:
        raise ValueError("Skill inference ordinal must be positive")
    digest = _fingerprint(
        {
            "capability_invocation_id": invocation_id,
            "inference_ordinal": ordinal,
            "contract_version": UBQ4_SKILL_ID_VERSION,
        }
    )
    return "skillinf:v1:" + digest


class UserInferenceQuotaService:
    """UBQ-4 logical CHAT_INFERENCE admission/finalization authority."""

    def __init__(
        self,
        uow_factory,
        *,
        owner_authority: UserBudgetDualAccountingService,
        settings: InferenceQuotaSettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._owner_authority = owner_authority
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    @property
    def estimator_identity(self) -> str:
        return (
            f"ubq4-estimator:{self.settings.estimator_policy_version}:"
            + _fingerprint(
                {
                    "version": self.settings.estimator_policy_version,
                    "default_output_token_reservation": (
                        self.settings.default_output_token_reservation
                    ),
                    "compute_units_per_1k_tokens": (
                        self.settings.compute_units_per_1k_tokens
                    ),
                    "cost_usd_per_1k_tokens": (
                        self.settings.cost_usd_per_1k_tokens
                    ),
                }
            )
        )

    @property
    def normalization_identity(self) -> str:
        return f"ubq4-usage:{self.settings.usage_normalization_version}"

    def build_context(
        self,
        *,
        budget_identity: Identity,
        logical_request_id: str,
        source_surface: str,
        logical_request_fingerprint: str | None = None,
        owner_user_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        workflow_id: str | None = None,
        iteration: int | None = None,
        agent_iteration_id: str | None = None,
        outer_request_id: str | None = None,
        capability_invocation_id: str | None = None,
    ) -> InferenceQuotaContext | None:
        if not self.enabled:
            return None
        if not isinstance(budget_identity, Identity):
            raise UserInferenceQuotaContextError(
                "trusted Identity is required for governed inference"
            )
        if not logical_request_id or not source_surface:
            raise UserInferenceQuotaContextError(
                "logical_request_id and source_surface are required"
            )
        return InferenceQuotaContext(
            budget_identity=budget_identity,
            logical_request_id=logical_request_id,
            logical_request_fingerprint=logical_request_fingerprint,
            source_surface=source_surface,
            owner_user_id=owner_user_id,
            session_id=session_id,
            task_id=task_id,
            execution_id=execution_id,
            workflow_id=workflow_id,
            iteration=iteration,
            agent_iteration_id=agent_iteration_id,
            outer_request_id=outer_request_id,
            capability_invocation_id=capability_invocation_id,
            estimator_policy_identity=self.estimator_identity,
            usage_normalization_identity=self.normalization_identity,
        )

    def logical_fingerprint(
        self,
        body: Mapping[str, Any],
        context: InferenceQuotaContext,
        *,
        streaming_mode: bool,
    ) -> str:
        if (
            context.estimator_policy_identity != self.estimator_identity
            or context.usage_normalization_identity
            != self.normalization_identity
        ):
            raise UserInferenceQuotaContextError(
                "quota policy/normalization identity drifted"
            )
        payload = {
            "fingerprint_version": UBQ4_FINGERPRINT_VERSION,
            "semantic_model_id": body.get("model") or "",
            "normalized_messages": body.get("messages") or [],
            "normalized_tool_definitions": body.get("tools") or [],
            "normalized_semantic_config": body.get("config") or {},
            "source_surface": context.source_surface,
            "stable_lineage": {
                "session_id": context.session_id,
                "task_id": context.task_id,
                "execution_id": context.execution_id,
                "workflow_id": context.workflow_id,
                "iteration": context.iteration,
                "agent_iteration_id": context.agent_iteration_id,
                "outer_request_id": context.outer_request_id,
                "capability_invocation_id": context.capability_invocation_id,
            },
            "estimator_policy_identity": context.estimator_policy_identity,
            "usage_normalization_identity": context.usage_normalization_identity,
            "streaming_mode": bool(streaming_mode),
        }
        value = _fingerprint(payload)
        supplied = context.logical_request_fingerprint
        if supplied is not None and supplied != value:
            raise UserInferenceQuotaConflictError(
                "supplied logical request fingerprint disagrees with canonical recomputation"
            )
        return value

    def _estimate(
        self,
        body: Mapping[str, Any],
    ) -> InferenceQuotaEstimate:
        logical_input = {
            "messages": body.get("messages") or [],
            "tools": body.get("tools") or [],
        }
        encoded = json.dumps(
            _canonicalize(logical_input),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        # One token per UTF-8 byte is deliberately conservative and does not
        # consume a provider retry/probe token.
        input_tokens = max(1, len(encoded))
        config = body.get("config") or {}
        max_tokens = config.get("max_tokens")
        if max_tokens is None:
            output_tokens = int(
                self.settings.default_output_token_reservation
            )
        else:
            if (
                isinstance(max_tokens, bool)
                or not isinstance(max_tokens, int)
                or max_tokens <= 0
            ):
                raise UserInferenceEstimateUnavailableError(
                    "max_tokens must be a positive integer"
                )
            output_tokens = max_tokens
        total_tokens = input_tokens + output_tokens

        compute = None
        if self.settings.compute_units_per_1k_tokens is not None:
            compute = (
                Decimal(total_tokens)
                * self.settings.compute_units_per_1k_tokens
                / Decimal(1000)
            )
        cost = None
        if self.settings.cost_usd_per_1k_tokens is not None:
            cost = (
                Decimal(total_tokens)
                * self.settings.cost_usd_per_1k_tokens
                / Decimal(1000)
            )
        return InferenceQuotaEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            compute_units=compute,
            cost_usd=cost,
        )

    @staticmethod
    def _reservation_identity(
        logical_request_id: str,
        kind: UserBudgetResourceKind,
    ) -> tuple[str, str]:
        digest = _canonical_sha256(
            {
                "version": UBQ4_RESERVATION_VERSION,
                "logical_request_id": logical_request_id,
                "resource_kind": kind.value,
            }
        )
        return "ubq4:v1:" + digest, "ubq4r:" + digest

    @staticmethod
    def _amounts(
        estimate: InferenceQuotaEstimate,
        *,
        include_compute: bool,
        include_cost: bool,
    ) -> dict[UserBudgetResourceKind, int]:
        result = {
            UserBudgetResourceKind.INFERENCE_CALL: 1,
            UserBudgetResourceKind.INPUT_TOKEN: estimate.input_tokens,
            UserBudgetResourceKind.OUTPUT_TOKEN: estimate.output_tokens,
            UserBudgetResourceKind.TOTAL_TOKEN: estimate.total_tokens,
        }
        if include_compute:
            if estimate.compute_units is None:
                raise UserInferenceEstimateUnavailableError(
                    "finite/recorded compute quota has no server-owned estimator"
                )
            result[UserBudgetResourceKind.COMPUTE_UNIT] = decimal_to_atomic(
                estimate.compute_units,
                positive=True,
            )
        if include_cost:
            if estimate.cost_usd is None:
                raise UserInferenceEstimateUnavailableError(
                    "finite/recorded cost quota has no server-owned estimator"
                )
            result[UserBudgetResourceKind.COST_USD] = decimal_to_atomic(
                estimate.cost_usd,
                positive=True,
            )
        return result

    @staticmethod
    def _attribution(
        context: InferenceQuotaContext,
        *,
        logical_fingerprint: str,
        kind: UserBudgetResourceKind,
    ) -> dict[str, Any]:
        return {
            "version": UBQ4_RESERVATION_VERSION,
            "logical_request_id": context.logical_request_id,
            "logical_request_fingerprint": logical_fingerprint,
            "resource_kind": kind.value,
            "source_surface": context.source_surface,
            "session_id": context.session_id,
            "task_id": context.task_id,
            "execution_id": context.execution_id,
            "workflow_id": context.workflow_id,
            "iteration": context.iteration,
            "agent_iteration_id": context.agent_iteration_id,
            "outer_request_id": context.outer_request_id,
            "capability_invocation_id": context.capability_invocation_id,
            "estimator_policy_identity": context.estimator_policy_identity,
            "usage_normalization_identity": context.usage_normalization_identity,
        }

    def _intent(
        self,
        *,
        owner_user_id: str,
        window_epoch: int,
        context: InferenceQuotaContext,
        logical_fingerprint: str,
        kind: UserBudgetResourceKind,
        amount: int,
    ) -> UserBudgetReservationIntent:
        key, reservation_id = self._reservation_identity(
            context.logical_request_id,
            kind,
        )
        return UserBudgetReservationIntent(
            reservation_id=reservation_id,
            owner_user_id=owner_user_id,
            window_epoch=window_epoch,
            idempotency_key=key,
            resource_kind=kind,
            capability_id=None,
            reserved_amount_atomic=amount,
            attribution=self._attribution(
                context,
                logical_fingerprint=logical_fingerprint,
                kind=kind,
            ),
        )

    @staticmethod
    def _verify_row(row, intent: UserBudgetReservationIntent) -> None:
        if (
            str(row.reservation_id) != intent.reservation_id
            or str(row.owner_user_id) != intent.owner_user_id
            or int(row.window_epoch) != intent.window_epoch
            or str(row.idempotency_key) != intent.idempotency_key
            or str(row.resource_kind) != intent.resource_kind.value
            or int(row.reserved_amount_atomic) != intent.reserved_amount_atomic
            or str(row.payload_fingerprint) != intent.payload_fingerprint
        ):
            raise UserInferenceQuotaConflictError(
                "logical inference reservation replay conflicts with canonical payload"
            )

    @staticmethod
    def _task_budget_inference_fingerprint(request_id: str) -> str:
        encoded = json.dumps(
            {
                "kind": "INFERENCE",
                "reservation_key": request_id,
                "payload": {},
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _require_no_unmigrated_historical_inference_in_uow(
        self,
        uow,
        *,
        owner_user_id: str,
        context: InferenceQuotaContext,
    ) -> None:
        """Fail closed instead of silently double-charging an UBQ-2 replay.

        V1 freezes historical bridge suppression behind exact durable lineage.
        Until UBQ-4 has a separately proven cross-window suppression path, an
        exact historical INFERENCE bridge is a migration/recovery boundary,
        not permission to create a second renewable-user charge.
        """
        if context.task_id is None:
            return
        bridge = await uow.user_budgets.get_dual_accounting_receipt(
            context.task_id,
            "INFERENCE",
            context.logical_request_id,
            UserBudgetResourceKind.INFERENCE_CALL.value,
        )
        if bridge is None:
            return

        binding = await uow.user_budgets.get_task_binding(context.task_id)
        budget = await uow.agents.get_task_budget(context.task_id)
        source = None
        if budget is not None:
            source = await uow.agents.get_task_budget_reservation(
                context.task_id,
                "INFERENCE",
                context.logical_request_id,
                expected_incarnation_generation=int(
                    budget.incarnation_generation
                ),
            )
        expected = self._task_budget_inference_fingerprint(
            context.logical_request_id
        )
        mirrored = None
        if bridge.ubq_idempotency_key is not None:
            mirrored = await uow.user_budgets.get_reservation(
                owner_user_id,
                str(bridge.ubq_idempotency_key),
            )

        iteration = None
        if context.agent_iteration_id is not None:
            iteration = await uow.agents.get_iteration(
                context.agent_iteration_id
            )

        exact_lineage = (
            binding is not None
            and str(binding.owner_user_id) == owner_user_id
            and source is not None
            and str(source.payload_fingerprint) == expected
            and str(bridge.source_payload_fingerprint) == expected
            and str(bridge.owner_user_id) == owner_user_id
            and int(bridge.amount_atomic) == 1
            and mirrored is not None
            and str(mirrored.reservation_id)
            == str(bridge.ubq_reservation_id)
            and int(mirrored.window_epoch) == int(bridge.window_epoch)
            and str(mirrored.resource_kind)
            == UserBudgetResourceKind.INFERENCE_CALL.value
            and str(mirrored.payload_fingerprint)
            == str(bridge.ubq_payload_fingerprint)
            and str(mirrored.state)
            == UserBudgetReservationState.SETTLED.value
            and int(mirrored.settled_amount_atomic or 0) == 1
            and context.execution_id is not None
            and context.agent_iteration_id is not None
            and iteration is not None
            and str(iteration.execution_id) == context.execution_id
            and str(iteration.inference_request_id)
            == context.logical_request_id
        )
        if not exact_lineage:
            raise UserInferenceQuotaConflictError(
                "historical UBQ-2 inference bridge has incomplete or "
                "conflicting durable lineage"
            )
        raise UserInferenceQuotaConflictError(
            "historical UBQ-2 inference charge is proven but cross-window "
            "UBQ-4 suppression is not enabled; defer instead of double-charge"
        )

    async def _resolve_owner_in_uow(
        self,
        uow,
        context: InferenceQuotaContext,
    ) -> str:
        resolution = await self._owner_authority.resolve_budget_owner_in_uow(
            uow,
            context.budget_identity,
        )
        if (
            context.owner_user_id is not None
            and context.owner_user_id != resolution.owner_user_id
        ):
            raise UserInferenceQuotaContextError(
                "compatibility owner_user_id disagrees with resolved budget owner"
            )
        if context.task_id is not None:
            binding = await uow.user_budgets.get_task_binding(context.task_id)
            if (
                binding is not None
                and str(binding.owner_user_id) != resolution.owner_user_id
            ):
                raise UserBudgetBindingDriftError(
                    "resolved inference quota owner disagrees with Task binding"
                )
        return resolution.owner_user_id

    async def require_embedding_allowed(
        self,
        *,
        budget_identity: Identity,
    ) -> None:
        """Fence unsupported user-owned embeddings only when policy is finite."""
        if not self.enabled:
            return
        if not isinstance(budget_identity, Identity):
            raise UserInferenceQuotaContextError(
                "trusted Identity is required for embedding policy fencing"
            )
        async with self._uow_factory() as uow:
            resolution = await self._owner_authority.resolve_budget_owner_in_uow(
                uow,
                budget_identity,
            )
            owner = resolution.owner_user_id
            window = await uow.user_budgets.get_active_window(owner)
            if (
                window is not None
                and datetime.now(timezone.utc) < _record_utc(window.expires_at)
            ):
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
                        "active embedding owner window has invalid policy authority"
                    )
            else:
                account = await uow.user_budgets.get_account(owner)
                if account is None or account.next_policy_id is None:
                    raise UserBudgetPolicyAuthorityConflictError(
                        "embedding owner has no selected UBQ policy"
                    )
                policy = await uow.user_budgets.get_policy(
                    owner,
                    str(account.next_policy_id),
                )
                if policy is None:
                    raise UserBudgetPolicyAuthorityConflictError(
                        "embedding owner selected policy is missing"
                    )

            governed_finite = any(
                getattr(policy, field) is not None
                for field in (
                    "max_compute_atomic",
                    "max_inference_calls",
                    "max_input_tokens",
                    "max_output_tokens",
                    "max_total_tokens",
                    "max_cost_usd_atomic",
                )
            )
            await uow.commit()
            if governed_finite:
                raise UserBudgetUnsupportedGovernedOperationError(
                    "embedding execution has no accepted UBQ estimator/"
                    "normalizer while the active/selected user policy has a "
                    "finite governed inference/token/compute/cost dimension"
                )

    async def reserve(
        self,
        *,
        context: InferenceQuotaContext | None,
        body: Mapping[str, Any],
        streaming_mode: bool,
        now: datetime | None = None,
    ) -> InferenceQuotaAdmission | None:
        if not self.enabled:
            return None
        if context is None:
            raise UserInferenceQuotaContextError(
                "governed inference is missing trusted quota context"
            )
        logical_fp = self.logical_fingerprint(
            body,
            context,
            streaming_mode=streaming_mode,
        )
        estimate = self._estimate(body)
        now = now or datetime.now(timezone.utc)
        retries = max(1, int(self.settings.max_conflict_retries))

        for attempt in range(retries):
            try:
                async with self._uow_factory() as uow:
                    await uow.user_budgets.begin_write_intent()
                    owner = await self._resolve_owner_in_uow(uow, context)

                    inf_key, _ = self._reservation_identity(
                        context.logical_request_id,
                        UserBudgetResourceKind.INFERENCE_CALL,
                    )
                    existing_inf = await uow.user_budgets.get_reservation(
                        owner,
                        inf_key,
                    )
                    if existing_inf is not None:
                        attr = dict(existing_inf.attribution_json or {})
                        if (
                            attr.get("logical_request_fingerprint") != logical_fp
                            or attr.get("logical_request_id")
                            != context.logical_request_id
                        ):
                            raise UserInferenceQuotaConflictError(
                                "request id replay has different logical semantics"
                            )
                        window = await uow.user_budgets.get_window(
                            owner,
                            int(existing_inf.window_epoch),
                        )
                        if window is None:
                            raise UserInferenceQuotaConflictError(
                                "replayed inference reservation lost its window"
                            )
                        policy = await uow.user_budgets.get_policy(
                            owner,
                            str(window.governing_policy_id),
                        )
                        if policy is None:
                            raise UserBudgetPolicyAuthorityConflictError(
                                "replayed inference window lost policy authority"
                            )
                        include_compute = (
                            policy.max_compute_atomic is not None
                            or self.settings.compute_units_per_1k_tokens is not None
                        )
                        include_cost = (
                            policy.max_cost_usd_atomic is not None
                            or self.settings.cost_usd_per_1k_tokens is not None
                        )
                        amounts = self._amounts(
                            estimate,
                            include_compute=include_compute,
                            include_cost=include_cost,
                        )
                        keys: dict[str, str] = {}
                        for kind, amount in amounts.items():
                            key, _rid = self._reservation_identity(
                                context.logical_request_id,
                                kind,
                            )
                            row = await uow.user_budgets.get_reservation(
                                owner,
                                key,
                            )
                            if row is None:
                                raise UserInferenceQuotaConflictError(
                                    "logical inference replay has a partial reservation set"
                                )
                            intent = self._intent(
                                owner_user_id=owner,
                                window_epoch=int(existing_inf.window_epoch),
                                context=context,
                                logical_fingerprint=logical_fp,
                                kind=kind,
                                amount=amount,
                            )
                            self._verify_row(row, intent)
                            keys[kind.value] = key
                        await uow.commit()
                        return InferenceQuotaAdmission(
                            owner_user_id=owner,
                            logical_request_id=context.logical_request_id,
                            logical_request_fingerprint=logical_fp,
                            window_epoch=int(existing_inf.window_epoch),
                            reservation_keys=keys,
                            replayed=True,
                            inference_reservation_state=str(existing_inf.state),
                        )

                    await self._require_no_unmigrated_historical_inference_in_uow(
                        uow,
                        owner_user_id=owner,
                        context=context,
                    )

                    account = await uow.user_budgets.get_account(
                        owner,
                        for_update=True,
                    )
                    if account is None or account.next_policy_id is None:
                        raise UserBudgetPolicyAuthorityConflictError(
                            "inference quota owner has no selected UBQ policy"
                        )

                    # A concurrent winner may have committed while this
                    # transaction waited for owner serialization.
                    existing_inf = await uow.user_budgets.get_reservation(
                        owner,
                        inf_key,
                    )
                    if existing_inf is not None:
                        await uow.rollback()
                        continue

                    window = await uow.user_budgets.get_active_window(owner)
                    if window is None or now >= _record_utc(window.expires_at):
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
                            "active inference window has invalid policy authority"
                        )

                    include_compute = (
                        policy.max_compute_atomic is not None
                        or self.settings.compute_units_per_1k_tokens is not None
                    )
                    include_cost = (
                        policy.max_cost_usd_atomic is not None
                        or self.settings.cost_usd_per_1k_tokens is not None
                    )
                    amounts = self._amounts(
                        estimate,
                        include_compute=include_compute,
                        include_cost=include_cost,
                    )

                    pending_input = await uow.user_budgets.sum_pending_reservation_amount(
                        owner,
                        int(window.epoch),
                        UserBudgetResourceKind.INPUT_TOKEN,
                    )
                    pending_output = await uow.user_budgets.sum_pending_reservation_amount(
                        owner,
                        int(window.epoch),
                        UserBudgetResourceKind.OUTPUT_TOKEN,
                    )

                    if (
                        policy.max_inference_calls is not None
                        and int(window.inference_used)
                        + int(window.inference_reserved)
                        + 1
                        > int(policy.max_inference_calls)
                    ):
                        raise UserInferenceQuotaExceededError(
                            "logical inference-call quota exhausted"
                        )
                    if (
                        policy.max_input_tokens is not None
                        and int(window.input_tokens_used)
                        + pending_input
                        + estimate.input_tokens
                        > int(policy.max_input_tokens)
                    ):
                        raise UserTokenQuotaExceededError(
                            "input-token quota exhausted"
                        )
                    if (
                        policy.max_output_tokens is not None
                        and int(window.output_tokens_used)
                        + pending_output
                        + estimate.output_tokens
                        > int(policy.max_output_tokens)
                    ):
                        raise UserTokenQuotaExceededError(
                            "output-token quota exhausted"
                        )
                    if (
                        policy.max_total_tokens is not None
                        and int(window.total_tokens_used)
                        + int(window.tokens_reserved)
                        + estimate.total_tokens
                        > int(policy.max_total_tokens)
                    ):
                        raise UserTokenQuotaExceededError(
                            "total-token quota exhausted"
                        )

                    compute_atomic = amounts.get(
                        UserBudgetResourceKind.COMPUTE_UNIT,
                        0,
                    )
                    if (
                        policy.max_compute_atomic is not None
                        and int(window.compute_used_atomic)
                        + int(window.compute_reserved_atomic)
                        + compute_atomic
                        > int(policy.max_compute_atomic)
                    ):
                        raise UserComputeQuotaExceededError(
                            "compute-unit quota exhausted"
                        )
                    cost_atomic = amounts.get(
                        UserBudgetResourceKind.COST_USD,
                        0,
                    )
                    if (
                        policy.max_cost_usd_atomic is not None
                        and int(window.cost_used_atomic)
                        + int(window.cost_reserved_atomic)
                        + cost_atomic
                        > int(policy.max_cost_usd_atomic)
                    ):
                        raise UserCostQuotaExceededError(
                            "cost quota exhausted"
                        )

                    keys: dict[str, str] = {}
                    for kind, amount in amounts.items():
                        intent = self._intent(
                            owner_user_id=owner,
                            window_epoch=int(window.epoch),
                            context=context,
                            logical_fingerprint=logical_fp,
                            kind=kind,
                            amount=amount,
                        )
                        row = await uow.user_budgets.create_or_get_reservation(
                            intent
                        )
                        self._verify_row(row, intent)
                        keys[kind.value] = intent.idempotency_key

                    targets: dict[str, int] = {
                        "inference_reserved": int(window.inference_reserved) + 1,
                        "tokens_reserved": (
                            int(window.tokens_reserved) + estimate.total_tokens
                        ),
                    }
                    if compute_atomic:
                        targets["compute_reserved_atomic"] = (
                            int(window.compute_reserved_atomic) + compute_atomic
                        )
                    if cost_atomic:
                        targets["cost_reserved_atomic"] = (
                            int(window.cost_reserved_atomic) + cost_atomic
                        )
                    await uow.user_budgets.mutate_window_usage(
                        owner,
                        int(window.epoch),
                        expected_revision=int(window.revision),
                        **targets,
                    )
                    await uow.commit()
                    return InferenceQuotaAdmission(
                        owner_user_id=owner,
                        logical_request_id=context.logical_request_id,
                        logical_request_fingerprint=logical_fp,
                        window_epoch=int(window.epoch),
                        reservation_keys=keys,
                        replayed=False,
                        inference_reservation_state=(
                            UserBudgetReservationState.RESERVED.value
                        ),
                    )
            except (UserBudgetConflictError, UserBudgetSerializationError):
                if attempt + 1 >= retries:
                    raise
                continue
        raise AssertionError("unreachable UBQ-4 reservation retry loop")

    def normalize_gateway_usage(
        self,
        response: Any,
    ) -> NormalizedInferenceUsage:
        usage = getattr(response, "usage", None)
        provider = getattr(getattr(response, "metadata", None), "provider", None)
        model = getattr(response, "model", None)
        if usage is None:
            fields = set()
        else:
            fields = set(getattr(usage, "model_fields_set", set()))
        if provider == "mock" and usage is not None:
            fields.update({"prompt_tokens", "completion_tokens", "total_tokens"})

        input_tokens = (
            int(usage.prompt_tokens)
            if usage is not None and "prompt_tokens" in fields
            else None
        )
        output_tokens = (
            int(usage.completion_tokens)
            if usage is not None and "completion_tokens" in fields
            else None
        )
        total_tokens = (
            int(usage.total_tokens)
            if usage is not None and "total_tokens" in fields
            else None
        )
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        compute_units = None
        cost_usd = None
        if total_tokens is not None:
            if self.settings.compute_units_per_1k_tokens is not None:
                compute_units = (
                    Decimal(total_tokens)
                    * self.settings.compute_units_per_1k_tokens
                    / Decimal(1000)
                )
            if self.settings.cost_usd_per_1k_tokens is not None:
                cost_usd = (
                    Decimal(total_tokens)
                    * self.settings.cost_usd_per_1k_tokens
                    / Decimal(1000)
                )
        return NormalizedInferenceUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            compute_units=compute_units,
            cost_usd=cost_usd,
            normalization_identity=self.normalization_identity,
            provider=provider,
            model=model,
        )

    def normalize_stream_usage(
        self,
        *,
        usage: Any,
        provider: str | None,
        model: str | None,
    ) -> NormalizedInferenceUsage:
        if usage is None:
            fields: set[str] = set()
        else:
            fields = set(getattr(usage, "model_fields_set", set()))
        input_tokens = (
            int(usage.prompt_tokens)
            if usage is not None and "prompt_tokens" in fields
            else None
        )
        output_tokens = (
            int(usage.completion_tokens)
            if usage is not None and "completion_tokens" in fields
            else None
        )
        total_tokens = (
            int(usage.total_tokens)
            if usage is not None and "total_tokens" in fields
            else None
        )
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        compute_units = None
        cost_usd = None
        if total_tokens is not None:
            if self.settings.compute_units_per_1k_tokens is not None:
                compute_units = (
                    Decimal(total_tokens)
                    * self.settings.compute_units_per_1k_tokens
                    / Decimal(1000)
                )
            if self.settings.cost_usd_per_1k_tokens is not None:
                cost_usd = (
                    Decimal(total_tokens)
                    * self.settings.cost_usd_per_1k_tokens
                    / Decimal(1000)
                )
        return NormalizedInferenceUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            compute_units=compute_units,
            cost_usd=cost_usd,
            normalization_identity=self.normalization_identity,
            provider=provider,
            model=model,
        )

    async def settle_success(
        self,
        admission: InferenceQuotaAdmission | None,
        usage: NormalizedInferenceUsage,
        *,
        now: datetime | None = None,
    ) -> None:
        if admission is None:
            return
        if usage.normalization_identity != self.normalization_identity:
            raise UserInferenceQuotaConflictError(
                "usage normalization identity mismatch"
            )
        actuals: dict[UserBudgetResourceKind, int | None] = {
            UserBudgetResourceKind.INFERENCE_CALL: 1,
            UserBudgetResourceKind.INPUT_TOKEN: usage.input_tokens,
            UserBudgetResourceKind.OUTPUT_TOKEN: usage.output_tokens,
            UserBudgetResourceKind.TOTAL_TOKEN: usage.total_tokens,
            UserBudgetResourceKind.COMPUTE_UNIT: (
                None
                if usage.compute_units is None
                else decimal_to_atomic(usage.compute_units)
            ),
            UserBudgetResourceKind.COST_USD: (
                None
                if usage.cost_usd is None
                else decimal_to_atomic(usage.cost_usd)
            ),
        }
        now = now or datetime.now(timezone.utc)
        retries = max(1, int(self.settings.max_conflict_retries))

        for attempt in range(retries):
            try:
                async with self._uow_factory() as uow:
                    await uow.user_budgets.begin_write_intent()
                    window = await uow.user_budgets.get_window(
                        admission.owner_user_id,
                        admission.window_epoch,
                    )
                    if window is None:
                        raise UserInferenceQuotaConflictError(
                            "inference settlement window is missing"
                        )
                    deltas = {
                        "inference_used": 0,
                        "input_tokens_used": 0,
                        "output_tokens_used": 0,
                        "total_tokens_used": 0,
                        "compute_used_atomic": 0,
                        "cost_used_atomic": 0,
                    }
                    reserved_decrements = {
                        "inference_reserved": 0,
                        "tokens_reserved": 0,
                        "compute_reserved_atomic": 0,
                        "cost_reserved_atomic": 0,
                    }
                    transitions: list[tuple[Any, int]] = []

                    for kind_name, key in admission.reservation_keys.items():
                        kind = UserBudgetResourceKind(kind_name)
                        row = await uow.user_budgets.get_reservation(
                            admission.owner_user_id,
                            key,
                        )
                        if row is None:
                            raise UserInferenceQuotaConflictError(
                                f"missing reservation for {kind.value}"
                            )
                        attr = dict(row.attribution_json or {})
                        if (
                            attr.get("logical_request_fingerprint")
                            != admission.logical_request_fingerprint
                            or attr.get("logical_request_id")
                            != admission.logical_request_id
                        ):
                            raise UserInferenceQuotaConflictError(
                                "settlement reservation lineage mismatch"
                            )
                        actual = actuals[kind]
                        if actual is None:
                            # Unknown usage remains RESERVED/non-refundable.
                            continue
                        actual = int(actual)
                        if actual < 0:
                            raise UserInferenceQuotaConflictError(
                                "normalized usage cannot be negative"
                            )
                        if str(row.state) == UserBudgetReservationState.SETTLED.value:
                            if int(row.settled_amount_atomic or 0) != actual:
                                raise UserInferenceQuotaConflictError(
                                    "settled replay has different actual usage"
                                )
                            continue
                        if str(row.state) != UserBudgetReservationState.RESERVED.value:
                            raise UserInferenceQuotaConflictError(
                                "quota reservation is terminal/incompatible"
                            )

                        if kind is UserBudgetResourceKind.INFERENCE_CALL:
                            deltas["inference_used"] += actual
                            reserved_decrements["inference_reserved"] += int(
                                row.reserved_amount_atomic
                            )
                        elif kind is UserBudgetResourceKind.INPUT_TOKEN:
                            deltas["input_tokens_used"] += actual
                        elif kind is UserBudgetResourceKind.OUTPUT_TOKEN:
                            deltas["output_tokens_used"] += actual
                        elif kind is UserBudgetResourceKind.TOTAL_TOKEN:
                            deltas["total_tokens_used"] += actual
                            reserved_decrements["tokens_reserved"] += int(
                                row.reserved_amount_atomic
                            )
                        elif kind is UserBudgetResourceKind.COMPUTE_UNIT:
                            deltas["compute_used_atomic"] += actual
                            reserved_decrements["compute_reserved_atomic"] += int(
                                row.reserved_amount_atomic
                            )
                        elif kind is UserBudgetResourceKind.COST_USD:
                            deltas["cost_used_atomic"] += actual
                            reserved_decrements["cost_reserved_atomic"] += int(
                                row.reserved_amount_atomic
                            )
                        transitions.append((row, actual))

                    targets: dict[str, int] = {}
                    for field, delta in deltas.items():
                        if delta:
                            targets[field] = int(getattr(window, field)) + delta
                    for field, decrement in reserved_decrements.items():
                        if decrement:
                            current = int(getattr(window, field))
                            if decrement > current:
                                raise UserInferenceQuotaConflictError(
                                    f"reserved counter underflow: {field}"
                                )
                            targets[field] = current - decrement

                    if targets:
                        window = await uow.user_budgets.mutate_window_usage(
                            admission.owner_user_id,
                            admission.window_epoch,
                            expected_revision=int(window.revision),
                            **targets,
                        )
                    for row, actual in transitions:
                        await uow.user_budgets.transition_reservation(
                            admission.owner_user_id,
                            str(row.idempotency_key),
                            expected_revision=int(row.revision),
                            target_state=UserBudgetReservationState.SETTLED,
                            settled_amount_atomic=actual,
                            settled_at=now,
                        )
                    await uow.commit()
                    return
            except (UserBudgetConflictError, UserBudgetSerializationError):
                if attempt + 1 >= retries:
                    raise
                continue
        raise AssertionError("unreachable UBQ-4 settlement retry loop")
