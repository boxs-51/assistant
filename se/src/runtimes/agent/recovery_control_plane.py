from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from .persistence import (
    DurableAgentStore,
    ExecutionConflictError,
    LeaseAuthorityConflictError,
)
from .safe_point_reconstruction import SafePointReconstructionError
from .stale_lease_scanner import (
    StaleLeaseObservation,
    StaleLeaseScanCoordinator,
)
from .task_budget import TaskBudgetConflictError, TaskBudgetService


DEFAULT_H1A_SWEEP_INTERVAL_SECONDS: Final[float] = 5.0

H1A_TERMINAL_SAFE_POINT_REASONS: Final[frozenset[str]] = frozenset(
    {
        "SAFE_POINT_TRANSCRIPT_CORRUPT",
        "SAFE_POINT_ACTIVE_BATCH_AMBIGUOUS",
        "SAFE_POINT_TOOL_RESULT_CONFLICT",
        "SAFE_POINT_EXECUTION_INVALID",
        "SAFE_POINT_ITERATION_LINEAGE_CONFLICT",
        "SAFE_POINT_ITERATION_AMBIGUOUS",
        "SAFE_POINT_CHECKPOINT_MISSING",
        "SAFE_POINT_CHECKPOINT_LINEAGE_CONFLICT",
        "SAFE_POINT_CHECKPOINT_ITERATION_MISSING",
        "SAFE_POINT_POST_RECOVERY_PROGRESS_UNPROVEN",
        "SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_CORRUPT",
        "SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_CONFLICT",
        "SAFE_POINT_RECOVERY_BATCH_SNAPSHOT_MISSING",
        "SAFE_POINT_TOOL_CALL_AMBIGUOUS",
        "SAFE_POINT_TOOL_CALL_MISSING",
        "SAFE_POINT_TOOL_CALL_CONFLICT",
        "SAFE_POINT_COMMITTED_RESULT_CONFLICT",
        "SAFE_POINT_INVOCATION_MISSING",
        "SAFE_POINT_INVOCATION_CONFLICT",
        "SAFE_POINT_ACTIVE_BATCH_MEMBERSHIP_MISMATCH",
        "SAFE_POINT_ACTIVE_BATCH_AUTHORITY_MISSING",
        "SAFE_POINT_TRANSCRIPT_DIVERGENCE",
        "INVALID_CHECKPOINT_REPRESENTATION_STATE",
        "MISSING_TRANSCRIPT_REPRESENTATION",
        "TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH",
        "TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID",
        "TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED",
        "TRANSCRIPT_REPRESENTATION_CORRUPT",
        "DUAL_TRANSCRIPT_MISMATCH",
    }
)


@dataclass(frozen=True, slots=True)
class H1ASweepReport:
    observed: int
    waiting: int
    failed: int
    noops: int
    deferred: int
    errors: tuple[str, ...] = ()
    scan_error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.scan_error is None and self.deferred == 0


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_point_reason(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SafePointReconstructionError):
            return str(current).split(":", 1)[0].strip()
        current = current.__cause__
    return None


class H1ARecoveryControlPlane:
    """Evacuate expired owned RUNNING rows without reactivating Agent work."""

    def __init__(
        self,
        scanner: StaleLeaseScanCoordinator,
        store: DurableAgentStore,
        task_budget_service: TaskBudgetService,
        *,
        sweep_interval_seconds: float = DEFAULT_H1A_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        if (
            isinstance(sweep_interval_seconds, bool)
            or not isinstance(sweep_interval_seconds, (int, float))
            or not math.isfinite(float(sweep_interval_seconds))
            or float(sweep_interval_seconds) <= 0.0
        ):
            raise ValueError(
                "sweep_interval_seconds must be a positive finite number"
            )
        self._scanner = scanner
        self._store = store
        self._task_budget_service = task_budget_service
        self._sweep_interval_seconds = float(sweep_interval_seconds)
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._started = False
        self._last_report: H1ASweepReport | None = None

    @property
    def last_report(self) -> H1ASweepReport | None:
        return self._last_report

    async def start(self) -> H1ASweepReport:
        """Run one immediate bounded sweep, then start one periodic worker."""

        if self._started:
            if self._last_report is None:
                raise RuntimeError("H1-A control plane started without a report")
            return self._last_report
        self._started = True
        report = await self.sweep_once()
        if not self._stop.is_set():
            self._worker = asyncio.create_task(
                self._run_loop(),
                name="ae-r12-h1a-stale-running-evacuation",
            )
        return report

    async def quiesce(self) -> None:
        """Stop future sweeps and drain the one current bounded worker."""

        self._stop.set()
        worker = self._worker
        if worker is not None:
            await worker
            self._worker = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._sweep_interval_seconds,
                )
            except asyncio.TimeoutError:
                await self.sweep_once()

    async def sweep_once(self) -> H1ASweepReport:
        try:
            sweep = await self._scanner.scan_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report = H1ASweepReport(
                observed=0,
                waiting=0,
                failed=0,
                noops=0,
                deferred=1,
                errors=(f"scan:{type(exc).__name__}:{exc}",),
                scan_error=f"{type(exc).__name__}: {exc}",
            )
            self._last_report = report
            return report

        waiting = 0
        failed = 0
        noops = 0
        scan_observation_errors = tuple(
            getattr(sweep, "observation_errors", ())
        )
        deferred = len(scan_observation_errors)
        errors: list[str] = [
            f"scan-observation:{item}"
            for item in scan_observation_errors
        ]
        takeover_now_utc = sweep.scan_cutoff_utc

        for observation in sweep.observations:
            try:
                outcome, error = await self._evacuate_observation(
                    observation,
                    takeover_now_utc=takeover_now_utc,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcome = "DEFERRED"
                error = f"{type(exc).__name__}: {exc}"

            if outcome == "WAITING":
                waiting += 1
            elif outcome == "FAILED":
                failed += 1
            elif outcome == "NOOP":
                noops += 1
            else:
                deferred += 1
                if error:
                    errors.append(
                        f"{observation.execution_id}:{error}"
                    )

        report = H1ASweepReport(
            observed=(
                len(sweep.observations)
                + len(scan_observation_errors)
            ),
            waiting=waiting,
            failed=failed,
            noops=noops,
            deferred=deferred,
            errors=tuple(errors),
        )
        self._last_report = report
        return report

    async def _evacuate_observation(
        self,
        observation: StaleLeaseObservation,
        *,
        takeover_now_utc: datetime,
    ) -> tuple[str, str | None]:
        current = await self._store.load_execution(
            observation.execution_id
        )
        if not self._matches_receipt(
            current,
            observation,
            takeover_now_utc=takeover_now_utc,
        ):
            return "NOOP", None

        task_id = getattr(current, "task_id", None)
        try:
            if task_id is None:
                await self._store.commit_recovery_waiting_checkpoint(
                    observation.execution_id,
                    observed_owner_instance_id=(
                        observation.owner_instance_id
                    ),
                    observed_lease_generation=(
                        observation.lease_generation
                    ),
                    observed_lease_expires_at=(
                        observation.lease_expires_at
                    ),
                    takeover_now_utc=takeover_now_utc,
                    observed_revision=observation.revision,
                )
            else:
                await self._task_budget_service.recover_task_scoped_execution(
                    str(task_id),
                    execution_id=observation.execution_id,
                    observed_owner_instance_id=(
                        observation.owner_instance_id
                    ),
                    observed_lease_generation=(
                        observation.lease_generation
                    ),
                    observed_lease_expires_at=(
                        observation.lease_expires_at
                    ),
                    takeover_now_utc=takeover_now_utc,
                    observed_revision=observation.revision,
                )
            return "WAITING", None
        except asyncio.CancelledError:
            raise
        except (
            ExecutionConflictError,
            LeaseAuthorityConflictError,
            TaskBudgetConflictError,
        ) as exc:
            reason_code = _safe_point_reason(exc)
            if reason_code in H1A_TERMINAL_SAFE_POINT_REASONS:
                return await self._terminalize(
                    observation,
                    task_id=task_id,
                    takeover_now_utc=takeover_now_utc,
                    reason_code=reason_code,
                )
            if reason_code is not None:
                return "DEFERRED", f"non-terminal-safe-point:{reason_code}"
            if not await self._receipt_is_still_current(
                observation,
                takeover_now_utc=takeover_now_utc,
            ):
                return "NOOP", None
            return "DEFERRED", f"{type(exc).__name__}:{exc}"

    async def _terminalize(
        self,
        observation: StaleLeaseObservation,
        *,
        task_id: str | None,
        takeover_now_utc: datetime,
        reason_code: str,
    ) -> tuple[str, str | None]:
        try:
            if task_id is None:
                await self._store.fail_expired_execution_unrecoverable(
                    observation.execution_id,
                    source_revision=observation.revision,
                    observed_owner_instance_id=(
                        observation.owner_instance_id
                    ),
                    observed_lease_generation=(
                        observation.lease_generation
                    ),
                    observed_lease_expires_at=(
                        observation.lease_expires_at
                    ),
                    takeover_now_utc=takeover_now_utc,
                    reason_code=reason_code,
                )
            else:
                await (
                    self._task_budget_service
                    .fail_unrecoverable_task_scoped_execution(
                        str(task_id),
                        execution_id=observation.execution_id,
                        source_revision=observation.revision,
                        observed_owner_instance_id=(
                            observation.owner_instance_id
                        ),
                        observed_lease_generation=(
                            observation.lease_generation
                        ),
                        observed_lease_expires_at=(
                            observation.lease_expires_at
                        ),
                        takeover_now_utc=takeover_now_utc,
                        reason_code=reason_code,
                    )
                )
            return "FAILED", None
        except asyncio.CancelledError:
            raise
        except (
            ExecutionConflictError,
            LeaseAuthorityConflictError,
            TaskBudgetConflictError,
        ) as exc:
            if not await self._receipt_is_still_current(
                observation,
                takeover_now_utc=takeover_now_utc,
            ):
                return "NOOP", None
            return "DEFERRED", f"terminal-race:{type(exc).__name__}:{exc}"

    async def _receipt_is_still_current(
        self,
        observation: StaleLeaseObservation,
        *,
        takeover_now_utc: datetime,
    ) -> bool:
        current = await self._store.load_execution(
            observation.execution_id
        )
        return self._matches_receipt(
            current,
            observation,
            takeover_now_utc=takeover_now_utc,
        )

    @staticmethod
    def _matches_receipt(
        current,
        observation: StaleLeaseObservation,
        *,
        takeover_now_utc: datetime,
    ) -> bool:
        if current is None:
            return False
        expiry = _utc(getattr(current, "lease_expires_at", None))
        return (
            str(getattr(current, "state", "")) == "RUNNING"
            and int(getattr(current, "revision", -1))
            == observation.revision
            and getattr(current, "owner_instance_id", None)
            == observation.owner_instance_id
            and int(getattr(current, "lease_generation", -1))
            == observation.lease_generation
            and expiry == observation.lease_expires_at
            and expiry is not None
            and expiry <= takeover_now_utc
        )


__all__ = [
    "DEFAULT_H1A_SWEEP_INTERVAL_SECONDS",
    "H1ARecoveryControlPlane",
    "H1ASweepReport",
    "H1A_TERMINAL_SAFE_POINT_REASONS",
]
