from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .contracts.recovery import (
    RecoveryActivationResult,
    RecoveryActivationSpec,
    RecoveryPlan,
    recovery_plan_fingerprint,
)
from .contracts.resume import ResumeClaimState
from .recovery_planning import (
    AgentRecoveryPlanningService,
    RecoveryPlanRejected,
)


class AgentRecoveryActivationService:
    """R12-F2 coordinator for fresh planning proof + atomic durable activation."""

    def __init__(
        self,
        planning_service: AgentRecoveryPlanningService,
        durable_store,
    ) -> None:
        self._planning = planning_service
        self._durable_store = durable_store

    async def activate(
        self,
        plan: RecoveryPlan,
        *,
        claim_id: str,
        resume_request_id: str,
        expected_claim_revision: int,
        activation_owner_instance_id: str,
        activation_now_utc: datetime,
        activation_lease_expires_at: datetime,
    ) -> RecoveryActivationResult:
        """Re-prove read-only authority, then consume one recovery claim atomically."""

        existing_claim = (
            await self._durable_store.load_resume_claim_by_request_id(
                resume_request_id
            )
        )
        if (
            existing_claim is not None
            and existing_claim.claim_id == claim_id
            and existing_claim.state is ResumeClaimState.CONSUMED
        ):
            durable_plan = plan
        else:
            fresh = await self._planning.build_recovery_plan(
                plan.execution_id,
                target_connection_id=plan.target_connection_id,
            )
            if fresh.plan_fingerprint != plan.plan_fingerprint:
                # F1 preserves mutable Task/Branch/TaskBudget revisions as
                # planning evidence. F2 revalidates their semantic lineage and
                # TaskBudget incarnation inside the activation UoW, so benign
                # revision-only movement must not become immutable activation
                # identity. Normalize only those three evidence fields back to
                # the claim-bound snapshot and require every other plan field
                # to remain byte-equivalent.
                normalized = replace(
                    fresh,
                    task_revision=plan.task_revision,
                    branch_revision=plan.branch_revision,
                    task_budget_revision=plan.task_budget_revision,
                    plan_fingerprint="",
                )
                if (
                    recovery_plan_fingerprint(normalized)
                    != plan.plan_fingerprint
                ):
                    raise RecoveryPlanRejected(
                        "RECOVERY_PLAN_DRIFT",
                        "Recovery authority changed after the claim plan was frozen.",
                    )
                durable_plan = plan
            else:
                durable_plan = fresh

        return await self._durable_store.consume_recovery_claim(
            RecoveryActivationSpec(
                plan=durable_plan,
                claim_id=claim_id,
                resume_request_id=resume_request_id,
                expected_claim_revision=expected_claim_revision,
                activation_owner_instance_id=activation_owner_instance_id,
                activation_now_utc=activation_now_utc,
                activation_lease_expires_at=activation_lease_expires_at,
            )
        )


__all__ = ["AgentRecoveryActivationService"]
