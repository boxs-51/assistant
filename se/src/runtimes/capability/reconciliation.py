from __future__ import annotations

import uuid
from typing import Any

from .contracts.error import (
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_RESULT_RECONCILIATION_REQUIRED,
)
from .contracts.invocation import (
    CapabilityInvocation,
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from .contracts.reconciliation import (
    RemoteReconciliationResult,
    RemoteReconciliationStatus,
    RemoteTerminalType,
)
from ..connection.protocol import RealtimeEnvelope


class RemoteInvocationReconciliationService:
    """Authorize and reconcile one previously-dispatched remote invocation."""

    def __init__(
        self,
        invocation_lifecycle,
        connection_registry,
        realtime,
    ) -> None:
        self._lifecycle = invocation_lifecycle
        self._connections = connection_registry
        self._realtime = realtime

    async def reconcile(
        self,
        invocation_id: str,
        connection_id: str,
        *,
        timeout: float | None = None,
    ) -> RemoteReconciliationResult:
        invocation = await self._lifecycle.store.get(invocation_id)
        if invocation is None:
            raise KeyError(f"Unknown capability invocation: {invocation_id}")

        local_terminal = self._local_terminal(invocation)
        if local_terminal is not None:
            return local_terminal

        self._require_reconciliation_snapshot(invocation)
        snapshot = self._connections.get(connection_id)
        if snapshot.user_id != invocation.owner_user_id:
            raise self._authorization_error(
                invocation,
                "Reconciliation connection owner does not match invocation owner.",
            )
        client_id = str(snapshot.metadata.get("client_id") or "")
        if client_id != invocation.origin_client_id:
            raise self._authorization_error(
                invocation,
                "Reconciliation must use the originating client installation.",
            )

        envelope = RealtimeEnvelope(
            type="capability.reconcile",
            message_id=f"reconcile-{uuid.uuid4().hex}",
            session_id=snapshot.session_id,
            connection_id=connection_id,
            execution_id=invocation.execution_id,
            invocation_id=invocation.invocation_id,
            trace_id=invocation.trace_id,
            payload={
                "capability_id": invocation.capability_id,
                "capability_version": invocation.capability_version,
                "request_fingerprint": invocation.request_fingerprint,
            },
        )
        payload = await self._realtime.reconcile(
            envelope,
            timeout=timeout,
        )
        result = RemoteReconciliationResult.model_validate(
            {
                "invocation_id": invocation.invocation_id,
                **payload,
            }
        )
        self._validate_response_identity(invocation, result)

        if result.status is RemoteReconciliationStatus.CONFLICT:
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message=(
                    "Client reconciliation reported a semantic invocation "
                    "conflict."
                ),
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "request_fingerprint": invocation.request_fingerprint,
                },
            )

        if result.status is RemoteReconciliationStatus.TERMINAL:
            await self._commit_terminal(invocation, result)

        return result

    @staticmethod
    def _require_reconciliation_snapshot(
        invocation: CapabilityInvocation,
    ) -> None:
        missing = [
            field
            for field, value in (
                ("capability_version", invocation.capability_version),
                ("request_fingerprint", invocation.request_fingerprint),
                ("owner_user_id", invocation.owner_user_id),
                ("origin_client_id", invocation.origin_client_id),
            )
            if not value
        ]
        if missing:
            raise CapabilityError(
                code=REMOTE_RESULT_RECONCILIATION_REQUIRED,
                message=(
                    "Invocation lacks the durable reconciliation snapshot "
                    "required for safe client query."
                ),
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={"missing_fields": missing},
            )

    @staticmethod
    def _authorization_error(
        invocation: CapabilityInvocation,
        message: str,
    ) -> CapabilityError:
        return CapabilityError(
            code="CAPABILITY_UNAUTHORIZED",
            message=message,
            category="AUTHORIZATION",
            retryable=False,
            safe_for_client=True,
            capability_id=invocation.capability_id,
            invocation_id=invocation.invocation_id,
        )

    @staticmethod
    def _validate_response_identity(
        invocation: CapabilityInvocation,
        result: RemoteReconciliationResult,
    ) -> None:
        if (
            result.capability_id != invocation.capability_id
            or result.capability_version != invocation.capability_version
            or result.request_fingerprint != invocation.request_fingerprint
        ):
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message="Client reconciliation identity does not match invocation.",
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={
                    "response_capability_id": result.capability_id,
                    "response_capability_version": result.capability_version,
                    "response_request_fingerprint": result.request_fingerprint,
                },
            )

    async def _commit_terminal(
        self,
        invocation: CapabilityInvocation,
        result: RemoteReconciliationResult,
    ) -> None:
        if invocation.state is not CapabilityInvocationState.WAITING:
            raise CapabilityError(
                code=REMOTE_RESULT_RECONCILIATION_REQUIRED,
                message=(
                    "Recovered remote terminal outcome can only be committed "
                    "from a WAITING invocation."
                ),
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
                details={"state": invocation.state.value},
            )
        if result.terminal_type is None or result.terminal_payload is None:
            raise CapabilityError(
                code=REMOTE_INVOCATION_CONFLICT,
                message="TERMINAL reconciliation requires terminal payload.",
                category="RECONCILIATION",
                retryable=False,
                safe_for_client=True,
                capability_id=invocation.capability_id,
                invocation_id=invocation.invocation_id,
            )

        if result.terminal_type is RemoteTerminalType.RESULT:
            target = CapabilityInvocationState.COMPLETED
            changes: dict[str, Any] = {
                "output": result.terminal_payload.get("output"),
            }
        elif result.terminal_type is RemoteTerminalType.ERROR:
            target = CapabilityInvocationState.FAILED
            changes = {"error": dict(result.terminal_payload)}
        else:
            target = CapabilityInvocationState.CANCELLED
            changes = {
                "error": {
                    "code": "CAPABILITY_CANCELLED",
                    "message": "Remote capability reported cancellation.",
                    "details": dict(result.terminal_payload),
                }
            }

        await self._lifecycle.transition(
            invocation,
            target,
            remote_outcome_state=RemoteOutcomeState.TERMINAL_COMMITTED,
            **changes,
        )

    @staticmethod
    def _local_terminal(
        invocation: CapabilityInvocation,
    ) -> RemoteReconciliationResult | None:
        if (
            invocation.remote_outcome_state
            is not RemoteOutcomeState.TERMINAL_COMMITTED
        ):
            return None
        if not invocation.capability_version or not invocation.request_fingerprint:
            return None
        if invocation.state is CapabilityInvocationState.COMPLETED:
            terminal_type = RemoteTerminalType.RESULT
            terminal_payload = {"output": invocation.output}
        elif invocation.state is CapabilityInvocationState.FAILED:
            terminal_type = RemoteTerminalType.ERROR
            terminal_payload = dict(invocation.error or {})
        elif invocation.state is CapabilityInvocationState.CANCELLED:
            terminal_type = RemoteTerminalType.CANCELLED
            terminal_payload = {}
        else:
            return None
        return RemoteReconciliationResult(
            invocation_id=invocation.invocation_id,
            status=RemoteReconciliationStatus.TERMINAL,
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            request_fingerprint=invocation.request_fingerprint,
            terminal_type=terminal_type,
            terminal_payload=terminal_payload,
        )
