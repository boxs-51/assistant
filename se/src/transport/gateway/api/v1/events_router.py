import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import ValidationError

from .....infrastructure.event_bus.ws_manager import WebSocketConnectionManager
from .....runtimes.capability.contracts.registration import ClientCapabilityRegistration
from .....runtimes.connection.protocol import RealtimeEnvelope
from .....runtimes.agent.contracts.resume import (
    ResumeClaimConsumeSpec,
    ResumeClaimIntent,
    ResumeClaimState,
    ResumeTriggerType,
)
from .....runtimes.agent.resume_claim import ResumeClaimError
from .....runtimes.agent.supervisor import AgentExecutionOwnershipError
from .....runtimes.agent.resume_planning import (
    ResumePlanDeferred,
    ResumePlanRejected,
)
from ...authentication.dependency import get_current_identity, get_websocket_identity
from .....domain.schemas.identity import Identity
from ...dependencies import get_container
from .....application.container import ApplicationContainer

router = APIRouter(prefix="/v1/events", tags=["Events"])
logger = structlog.get_logger(__name__)


async def _send_realtime(websocket: WebSocket, envelope: RealtimeEnvelope):
    await websocket.send_json(envelope.model_dump(mode="json"))


async def _ensure_connection(websocket, identity, connection_runtime, envelope):
    if connection_runtime is None:
        raise RuntimeError("Connection runtime is unavailable.")

    connection_id = envelope.connection_id or envelope.payload.get("connection_id")
    if not connection_id:
        raise ValueError("connection.register requires connection_id")

    session_id = (
        envelope.session_id
        or envelope.payload.get("session_id")
        or getattr(identity, "session_id", None)
        or f"ws-{uuid.uuid4().hex}"
    )
    snapshot = connection_runtime.registry.register(
        session_id=session_id,
        user_id=identity.user_id,
        socket=websocket,
        metadata={
            "client_id": envelope.payload.get("client_id", ""),
            "transport": "websocket",
        },
        connection_id=connection_id,
    )
    snapshot = connection_runtime.registry.activate(connection_id)
    await _send_realtime(
        websocket,
        RealtimeEnvelope(
            type="connection.registered",
            message_id=f"registered-{uuid.uuid4().hex}",
            session_id=snapshot.session_id,
            connection_id=snapshot.connection_id,
            payload={"state": snapshot.state.value},
        ),
    )
    return connection_id


_R7_G_CLAIM_TTL = timedelta(minutes=1)


async def _publish_waiting_tickets(
    websocket: WebSocket,
    identity: Identity,
    container: ApplicationContainer,
    *,
    connection_id: str,
    client_id: str,
) -> None:
    """Replay current normalized WAITING(CONNECTION) tickets after capability ACK.

    Publication is read-only and deliberately does not build ResumePlan,
    create/consume ResumeClaim, or mutate AgentExecution lifecycle state.
    """

    durable_store = getattr(container, "agent_durable_store", None)
    loader = getattr(durable_store, "load_pending_resume_tickets", None)
    if not callable(loader):
        return
    try:
        tickets = await loader(
            owner_user_id=identity.user_id or "",
            client_id=client_id,
        )
    except Exception:
        # Ticket replay is read-only publication after capability ACK. A
        # malformed/stale durable row must fail closed for auto-resume without
        # retroactively invalidating the successful registration handshake.
        logger.exception(
            "Failed to replay R7-H waiting tickets",
            user_id=identity.user_id,
            client_id=client_id,
            connection_id=connection_id,
        )
        return
    for payload in tickets:
        await _send_realtime(
            websocket,
            RealtimeEnvelope(
                type="execution.waiting",
                message_id=f"waiting-{uuid.uuid4().hex}",
                connection_id=connection_id,
                execution_id=payload.get("execution_id"),
                payload=dict(payload),
            ),
        )


def _resume_exception_code(exc: BaseException, fallback: str) -> str:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    prefix = str(exc).partition(":")[0].strip()
    if (
        prefix
        and prefix.upper() == prefix
        and all(char.isalnum() or char == "_" for char in prefix)
    ):
        return prefix
    return fallback


def _resume_retry_claim_id(claim, exc: BaseException) -> str | None:
    """Preserve one CREATED claim identity only for retryable RESUME_CONFLICT."""

    if (
        claim is None
        or str(getattr(exc, "code", "")) != "RESUME_CONFLICT"
        or not bool(getattr(exc, "retryable", False))
    ):
        return None
    claim_id = getattr(claim, "claim_id", None)
    return str(claim_id) if claim_id else None


def _resume_claim_matches_wire_request(
    claim,
    *,
    execution_id: str,
    checkpoint_id: str,
    resume_request_id: str,
    user_id: str,
    client_id: str,
    connection_id: str,
) -> bool:
    state = getattr(claim.state, "value", str(claim.state))
    connection_matches = (
        claim.connection_id == connection_id
        or state == ResumeClaimState.CONSUMED.value
    )
    return (
        claim.resume_request_id == resume_request_id
        and claim.execution_id == execution_id
        and claim.checkpoint_id == checkpoint_id
        and claim.user_id == user_id
        and claim.client_id == client_id
        and connection_matches
    )


def _resume_claim_matches_plan(claim, plan, resume_request_id: str) -> bool:
    return (
        claim.resume_request_id == resume_request_id
        and claim.execution_id == plan.execution_id
        and claim.checkpoint_id == plan.checkpoint_id
        and claim.expected_execution_revision
        == plan.expected_execution_revision
        and claim.plan_fingerprint == plan.plan_fingerprint
        and claim.user_id == plan.target_user_id
        and claim.client_id == plan.target_client_id
        and claim.connection_id == plan.target_connection_id
    )


async def _send_resume_rejected(
    websocket,
    *,
    connection_id: str,
    execution_id: str,
    checkpoint_id: str,
    resume_request_id: str,
    code: str,
    message: str,
    retryable: bool = False,
    claim_id: str | None = None,
) -> None:
    payload = {
        "execution_id": execution_id,
        "checkpoint_id": checkpoint_id,
        "resume_request_id": resume_request_id,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if claim_id is not None:
        payload["claim_id"] = claim_id
    await _send_realtime(
        websocket,
        RealtimeEnvelope(
            type="execution.resume.rejected",
            message_id=f"resume-rejected-{uuid.uuid4().hex}",
            connection_id=connection_id,
            execution_id=execution_id,
            payload=payload,
        ),
    )


async def _send_resume_failed(
    websocket,
    *,
    connection_id: str,
    execution_id: str,
    payload: dict,
) -> None:
    await _send_realtime(
        websocket,
        RealtimeEnvelope(
            type="execution.resume.failed",
            message_id=f"resume-failed-{uuid.uuid4().hex}",
            connection_id=connection_id,
            execution_id=execution_id,
            payload=payload,
        ),
    )


async def _replay_resume_claim_outcome(
    websocket,
    *,
    connection_id: str,
    execution_id: str,
    checkpoint_id: str,
    resume_request_id: str,
    claim,
    supervisor,
) -> bool:
    state = getattr(claim.state, "value", str(claim.state))
    metadata = dict(getattr(claim, "metadata", {}) or {})
    handoff = metadata.get("r7_g_handoff")

    if state == ResumeClaimState.CONSUMED.value:
        if isinstance(handoff, dict):
            status = str(handoff.get("status") or "").upper()
            wire_payload = {
                key: value
                for key, value in handoff.items()
                if key != "status"
            }
            if status == "ACCEPTED":
                await _send_realtime(
                    websocket,
                    RealtimeEnvelope(
                        type="execution.resume.accepted",
                        message_id=f"resume-replay-{uuid.uuid4().hex}",
                        connection_id=connection_id,
                        execution_id=execution_id,
                        payload=wire_payload,
                    ),
                )
                return True
            if status == "FAILED":
                await _send_resume_failed(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    payload=wire_payload,
                )
                return True

        if supervisor.is_running(execution_id):
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                claim_id=claim.claim_id,
                code="RESUME_CONFLICT",
                message=(
                    "Resume authority is already owned and the durable "
                    "handoff outcome is not ready yet."
                ),
                retryable=True,
            )
        else:
            await _send_resume_failed(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                payload={
                    "execution_id": execution_id,
                    "checkpoint_id": checkpoint_id,
                    "resume_request_id": resume_request_id,
                    "claim_id": claim.claim_id,
                    "code": "RUNTIME_HANDOFF_FAILED",
                    "message": (
                        "ResumeClaim is CONSUMED without a durable R7-G "
                        "handoff outcome; automatic re-activation is unsafe."
                    ),
                    "state": "RUNNING",
                },
            )
        return True

    if state == ResumeClaimState.REJECTED.value:
        await _send_resume_rejected(
            websocket,
            connection_id=connection_id,
            execution_id=execution_id,
            checkpoint_id=checkpoint_id,
            resume_request_id=resume_request_id,
            claim_id=claim.claim_id,
            code=claim.rejection_code or "STALE_RESUME_CLAIM",
            message=claim.rejection_code or "ResumeClaim was rejected.",
        )
        return True

    if state == ResumeClaimState.EXPIRED.value:
        await _send_resume_rejected(
            websocket,
            connection_id=connection_id,
            execution_id=execution_id,
            checkpoint_id=checkpoint_id,
            resume_request_id=resume_request_id,
            claim_id=claim.claim_id,
            code="CLAIM_EXPIRED",
            message="ResumeClaim expired before authority was acquired.",
            retryable=True,
        )
        return True

    return False


async def _recover_r7_g_handoff_failure(
    websocket,
    *,
    container,
    supervisor,
    context,
    plan,
    consumed,
    connection_id: str,
    error: BaseException,
    send_wire: bool = True,
) -> None:
    message = f"{type(error).__name__}: {error}"
    recovery_checkpoint_id = None
    recovery_revision = None
    state = "FAILED"
    wait_reason = None

    try:
        recovery_checkpoint_id, recovery_revision = (
            await container.agent_runtime.recover_claimed_resume(
                context,
                plan=plan,
                consumed=consumed,
                error_message=message,
            )
        )
        state = "WAITING"
        wait_reason = "RECOVERY"
    except BaseException as recovery_error:
        terminalized = False
        try:
            terminalized = await container.agent_runtime.fail_claimed_resume(
                context,
                plan=plan,
                consumed=consumed,
                error_message=(
                    f"{message}; recovery failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                ),
            )
        except BaseException:
            terminalized = False
        state = "FAILED" if terminalized else "RUNNING"

    failed_payload = {
        "execution_id": plan.execution_id,
        "checkpoint_id": plan.checkpoint_id,
        "resume_request_id": consumed.resume_request_id,
        "claim_id": consumed.claim_id,
        "code": "RUNTIME_HANDOFF_FAILED",
        "message": message,
        "state": state,
    }
    if recovery_checkpoint_id is not None:
        failed_payload["recovery_checkpoint_id"] = recovery_checkpoint_id
    if recovery_revision is not None:
        failed_payload["recovery_revision"] = recovery_revision
    if wait_reason is not None:
        failed_payload["wait_reason"] = wait_reason

    try:
        await container.agent_durable_store.record_resume_claim_handoff(
            consumed.claim_id,
            status="FAILED",
            payload=failed_payload,
        )
    except BaseException:
        logger.exception(
            "Failed to persist R7-G failed handoff outcome",
            execution_id=plan.execution_id,
            claim_id=consumed.claim_id,
        )

    if send_wire:
        await _send_resume_failed(
            websocket,
            connection_id=connection_id,
            execution_id=plan.execution_id,
            payload=failed_payload,
        )


async def _resume_execution(websocket, identity, container, connection_id, envelope):
    payload = envelope.payload
    execution_id = str(payload.get("execution_id") or "")
    checkpoint_id = str(payload.get("checkpoint_id") or "")
    resume_request_id = str(payload.get("resume_request_id") or "").strip()
    if not execution_id or not checkpoint_id:
        raise ValueError("execution.resume requires execution_id and checkpoint_id")
    if envelope.connection_id != connection_id:
        raise ValueError("execution.resume connection_id does not match active connection")

    snapshot = container.connection_runtime.registry.get(connection_id)
    if not snapshot.is_usable or snapshot.user_id != identity.user_id:
        raise PermissionError("Resume connection is not active for this principal")

    planning_service = getattr(container, "resume_planning_service", None)
    durable_store = getattr(container, "agent_durable_store", None)
    supervisor = getattr(container, "agent_execution_supervisor", None)
    client_id = str(snapshot.metadata.get("client_id") or "")

    if planning_service is None:
        raise RuntimeError(
            "R7_CANONICAL_RESUME_AUTHORITY_UNAVAILABLE: "
            "resume planning service is required."
        )
    if resume_request_id and (
        durable_store is None
        or supervisor is None
    ):
        raise RuntimeError(
            "R7_CANONICAL_RESUME_AUTHORITY_UNAVAILABLE: "
            "durable store and supervisor are required for resume authority."
        )

    # Lost-ACK replay must precede planning. The original accepted execution
    # may already be RUNNING or terminal, in which case rebuilding a WAITING
    # ResumePlan would incorrectly turn a successful resume into a rejection.
    if (
        planning_service is not None
        and resume_request_id
        and durable_store is not None
        and supervisor is not None
    ):
        replay_claim = await durable_store.load_resume_claim_by_request_id(
            resume_request_id
        )
        if replay_claim is not None:
            if not _resume_claim_matches_wire_request(
                replay_claim,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                user_id=identity.user_id,
                client_id=client_id,
                connection_id=connection_id,
            ):
                await _send_resume_rejected(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    checkpoint_id=checkpoint_id,
                    resume_request_id=resume_request_id,
                    claim_id=replay_claim.claim_id,
                    code="RESUME_REQUEST_CONFLICT",
                    message=(
                        "resume_request_id was reused with different "
                        "resume semantics."
                    ),
                )
                return
            if await _replay_resume_claim_outcome(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                claim=replay_claim,
                supervisor=supervisor,
            ):
                return

    if planning_service is not None:
        try:
            plan = await planning_service.build_resume_plan(
                execution_id,
                checkpoint_id,
                target_user_id=identity.user_id,
                target_client_id=client_id or None,
                target_connection_id=connection_id,
            )
        except ResumePlanDeferred as exc:
            if resume_request_id:
                await _send_resume_rejected(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    checkpoint_id=checkpoint_id,
                    resume_request_id=resume_request_id,
                    code=exc.code,
                    message=str(exc),
                    retryable=True,
                )
            else:
                await _send_realtime(
                    websocket,
                    RealtimeEnvelope(
                        type="execution.resume.preflight",
                        message_id=f"resume-preflight-{uuid.uuid4().hex}",
                        connection_id=connection_id,
                        execution_id=execution_id,
                        payload={
                            "execution_id": execution_id,
                            "checkpoint_id": checkpoint_id,
                            "status": "DEFERRED",
                            "code": exc.code,
                            "message": str(exc),
                        },
                    ),
                )
            return
        except ResumePlanRejected as exc:
            if resume_request_id:
                await _send_resume_rejected(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    checkpoint_id=checkpoint_id,
                    resume_request_id=resume_request_id,
                    code=exc.code,
                    message=str(exc),
                )
            else:
                await _send_realtime(
                    websocket,
                    RealtimeEnvelope(
                        type="execution.resume.preflight",
                        message_id=f"resume-preflight-{uuid.uuid4().hex}",
                        connection_id=connection_id,
                        execution_id=execution_id,
                        payload={
                            "execution_id": execution_id,
                            "checkpoint_id": checkpoint_id,
                            "status": "REJECTED",
                            "code": exc.code,
                            "message": str(exc),
                        },
                    ),
                )
            return

        # R7-H owns client ticket generation/retry. Until it lands, requests
        # without a stable resume_request_id retain the R7-D preflight-only
        # compatibility behavior and acquire no execution authority.
        if not resume_request_id:
            await _send_realtime(
                websocket,
                RealtimeEnvelope(
                    type="execution.resume.preflight",
                    message_id=f"resume-preflight-{uuid.uuid4().hex}",
                    connection_id=connection_id,
                    execution_id=execution_id,
                    payload={
                        "execution_id": plan.execution_id,
                        "checkpoint_id": plan.checkpoint_id,
                        "status": "PLAN_READY",
                        "plan_fingerprint": plan.plan_fingerprint,
                        "expected_execution_revision": (
                            plan.expected_execution_revision
                        ),
                        "actions": [
                            {
                                "ordinal": item.ordinal,
                                "invocation_id": item.invocation_id,
                                "tool_call_id": item.tool_call_id,
                                "capability_id": item.capability_id,
                                "action": item.action.value,
                                "expected_invocation_revision": (
                                    item.expected_invocation_revision
                                ),
                            }
                            for item in plan.invocation_actions
                        ],
                        "activation": "R7_D_PREFLIGHT_ONLY",
                    },
                ),
            )
            return

        runtime = getattr(container, "agent_runtime", None)
        if durable_store is None or runtime is None or supervisor is None:
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                code="RESUME_AUTHORITY_UNAVAILABLE",
                message="Canonical R7-G resume authority is unavailable.",
                retryable=True,
            )
            return

        existing_claim = await durable_store.load_resume_claim_by_request_id(
            resume_request_id
        )
        if existing_claim is not None:
            if not _resume_claim_matches_plan(
                existing_claim,
                plan,
                resume_request_id,
            ):
                await _send_resume_rejected(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    checkpoint_id=checkpoint_id,
                    resume_request_id=resume_request_id,
                    claim_id=existing_claim.claim_id,
                    code="RESUME_REQUEST_CONFLICT",
                    message=(
                        "resume_request_id was reused with different "
                        "resume semantics."
                    ),
                )
                return
            if await _replay_resume_claim_outcome(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                claim=existing_claim,
                supervisor=supervisor,
            ):
                return

        agent_registry = getattr(container, "agent_registry", None)
        agent = (
            agent_registry.get(plan.agent_id)
            if agent_registry is not None
            else None
        )
        if agent_registry is not None and agent is None:
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                code="RESUME_AUTHORITY_UNAVAILABLE",
                message=f"Agent '{plan.agent_id}' is not registered.",
            )
            return

        try:
            context = await durable_store.prepare_resume_plan_context(
                plan,
                identity=identity,
                agent=agent,
            )
        except BaseException as exc:
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                code=_resume_exception_code(exc, "STALE_RESUME_PLAN"),
                message=str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            )
            return

        try:
            token = await supervisor.reserve(context)
        except AgentExecutionOwnershipError as exc:
            # A duplicate request can race the first request between claim
            # consume and durable handoff recording. Re-read before deciding.
            current = await durable_store.load_resume_claim_by_request_id(
                resume_request_id
            )
            if (
                current is not None
                and _resume_claim_matches_plan(
                    current,
                    plan,
                    resume_request_id,
                )
                and await _replay_resume_claim_outcome(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    checkpoint_id=checkpoint_id,
                    resume_request_id=resume_request_id,
                    claim=current,
                    supervisor=supervisor,
                )
            ):
                return
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                code="RESUME_CONFLICT",
                message=str(exc),
                retryable=True,
            )
            return

        claim = None
        consumed = None
        owned_task = None
        continue_after_ack = asyncio.Event()
        activation_ready = asyncio.get_running_loop().create_future()
        try:
            now_utc = datetime.now(timezone.utc)
            claim_expires_at = now_utc + _R7_G_CLAIM_TTL
            if (
                plan.wait_expires_at is not None
                and plan.wait_expires_at < claim_expires_at
            ):
                claim_expires_at = plan.wait_expires_at

            claim = await durable_store.get_or_create_resume_claim(
                ResumeClaimIntent(
                    resume_request_id=resume_request_id,
                    execution_id=plan.execution_id,
                    checkpoint_id=plan.checkpoint_id,
                    expected_execution_revision=(
                        plan.expected_execution_revision
                    ),
                    plan_fingerprint=plan.plan_fingerprint,
                    user_id=plan.target_user_id,
                    client_id=plan.target_client_id,
                    connection_id=plan.target_connection_id,
                    wait_reason="CONNECTION",
                    trigger_type=ResumeTriggerType.CLIENT_RECONNECT,
                    claim_expires_at=claim_expires_at,
                    metadata={
                        "trace_id": plan.trace_id,
                        "request_id": plan.request_id,
                    },
                )
            )

            if await _replay_resume_claim_outcome(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                claim=claim,
                supervisor=supervisor,
            ):
                await supervisor.release_reserved(token)
                return

            consumed = await durable_store.consume_resume_claim(
                ResumeClaimConsumeSpec(
                    plan=plan,
                    claim_id=claim.claim_id,
                    resume_request_id=resume_request_id,
                    expected_claim_revision=claim.revision,
                    now_utc=now_utc,
                )
            )

            if consumed.already_consumed:
                await supervisor.release_reserved(token)
                current = await durable_store.load_resume_claim_by_request_id(
                    resume_request_id
                )
                if current is not None:
                    await _replay_resume_claim_outcome(
                        websocket,
                        connection_id=connection_id,
                        execution_id=execution_id,
                        checkpoint_id=checkpoint_id,
                        resume_request_id=resume_request_id,
                        claim=current,
                        supervisor=supervisor,
                    )
                    return
                await _send_resume_failed(
                    websocket,
                    connection_id=connection_id,
                    execution_id=execution_id,
                    payload={
                        "execution_id": execution_id,
                        "checkpoint_id": checkpoint_id,
                        "resume_request_id": resume_request_id,
                        "claim_id": claim.claim_id,
                        "code": "RUNTIME_HANDOFF_FAILED",
                        "message": (
                            "Consumed resume authority could not be "
                            "reconstructed for replay."
                        ),
                        "state": "RUNNING",
                    },
                )
                return

            async def _owned_resume_runner():
                try:
                    resumed_results = (
                        await runtime.prepare_claimed_resume_activation(
                            context,
                            plan=plan,
                            consumed=consumed,
                        )
                    )
                except BaseException as exc:
                    if not activation_ready.done():
                        activation_ready.set_exception(exc)
                    raise

                if not activation_ready.done():
                    activation_ready.set_result(resumed_results)
                await continue_after_ack.wait()
                return await runtime.execute(
                    context,
                    durable_revision=consumed.consumed_execution_revision,
                    initial_tool_results=resumed_results,
                )

            owned_task = await supervisor.start_reserved(
                token,
                context,
                _owned_resume_runner,
            )

            try:
                await asyncio.shield(activation_ready)
            except asyncio.CancelledError as exc:
                # Request/socket cancellation after claim consumption is a
                # post-claim handoff failure, not permission to abandon a
                # supervisor-owned task at RUNNING.
                await supervisor.cancel_execution(
                    plan.execution_id,
                    cascade=False,
                )
                if activation_ready.done() and not activation_ready.cancelled():
                    activation_ready.exception()
                recovery = asyncio.create_task(
                    _recover_r7_g_handoff_failure(
                        websocket,
                        container=container,
                        supervisor=supervisor,
                        context=context,
                        plan=plan,
                        consumed=consumed,
                        connection_id=connection_id,
                        error=exc,
                        send_wire=False,
                    )
                )
                try:
                    await asyncio.shield(recovery)
                except BaseException:
                    logger.exception(
                        "R7-G cancellation recovery failed",
                        execution_id=plan.execution_id,
                        claim_id=consumed.claim_id,
                    )
                raise
            except BaseException as exc:
                await asyncio.gather(owned_task, return_exceptions=True)
                await _recover_r7_g_handoff_failure(
                    websocket,
                    container=container,
                    supervisor=supervisor,
                    context=context,
                    plan=plan,
                    consumed=consumed,
                    connection_id=connection_id,
                    error=exc,
                )
                return

            accepted_payload = {
                "execution_id": plan.execution_id,
                "checkpoint_id": plan.checkpoint_id,
                "resume_request_id": resume_request_id,
                "claim_id": consumed.claim_id,
                "accepted_revision": (
                    consumed.consumed_execution_revision
                ),
                "state_at_accept": "RUNNING",
            }

            handoff_task = asyncio.create_task(
                durable_store.record_resume_claim_handoff(
                    consumed.claim_id,
                    status="ACCEPTED",
                    payload=accepted_payload,
                ),
                name=f"r7-g-handoff:{plan.execution_id}",
            )
            try:
                await asyncio.shield(handoff_task)
            except asyncio.CancelledError as request_cancel:
                # The ACCEPTED metadata write is itself a durable authority
                # boundary.  Caller cancellation must not make its commit
                # outcome ambiguous: drain the shielded write before choosing
                # between accepted continuation and RECOVERY.
                try:
                    await asyncio.shield(handoff_task)
                except BaseException as handoff_error:
                    await supervisor.cancel_execution(
                        plan.execution_id,
                        cascade=False,
                    )
                    recovery = asyncio.create_task(
                        _recover_r7_g_handoff_failure(
                            websocket,
                            container=container,
                            supervisor=supervisor,
                            context=context,
                            plan=plan,
                            consumed=consumed,
                            connection_id=connection_id,
                            error=handoff_error,
                            send_wire=False,
                        ),
                        name=f"r7-g-recovery:{plan.execution_id}",
                    )
                    try:
                        await asyncio.shield(recovery)
                    except BaseException:
                        logger.exception(
                            "R7-G handoff cancellation recovery failed",
                            execution_id=plan.execution_id,
                            claim_id=consumed.claim_id,
                        )
                    raise request_cancel

                # ACCEPTED is durably known.  The request may disappear, but
                # the owned runtime must continue; a retry replays the stored
                # semantic ACK without another claim/CAS/task.
                continue_after_ack.set()
                raise request_cancel
            except BaseException as exc:
                await supervisor.cancel_execution(
                    plan.execution_id,
                    cascade=False,
                )
                await _recover_r7_g_handoff_failure(
                    websocket,
                    container=container,
                    supervisor=supervisor,
                    context=context,
                    plan=plan,
                    consumed=consumed,
                    connection_id=connection_id,
                    error=exc,
                )
                return

            try:
                await _send_realtime(
                    websocket,
                    RealtimeEnvelope(
                        type="execution.resume.accepted",
                        message_id=f"resume-{uuid.uuid4().hex}",
                        connection_id=connection_id,
                        execution_id=execution_id,
                        payload=accepted_payload,
                    ),
                )
            finally:
                # ACK delivery is not execution authority. A dropped ACK must
                # not abandon an already-consumed, supervisor-owned resume.
                continue_after_ack.set()
            return
        except ResumeClaimError as exc:
            await supervisor.release_reserved(token)
            # No resume authority was acquired. For a retryable
            # RESUME_CONFLICT preserve the exact CREATED claim/request
            # identity so ClientRuntime retries the same logical attempt.
            retry_claim_id = _resume_retry_claim_id(claim, exc)
            await _send_resume_rejected(
                websocket,
                connection_id=connection_id,
                execution_id=execution_id,
                checkpoint_id=checkpoint_id,
                resume_request_id=resume_request_id,
                code=exc.code,
                message=str(exc),
                retryable=bool(exc.retryable),
                claim_id=retry_claim_id,
            )
            return
        except BaseException as exc:
            if consumed is None:
                await supervisor.release_reserved(token)
            elif owned_task is None:
                await _recover_r7_g_handoff_failure(
                    websocket,
                    container=container,
                    supervisor=supervisor,
                    context=context,
                    plan=plan,
                    consumed=consumed,
                    connection_id=connection_id,
                    error=exc,
                )
                return
            raise



@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    identity: Identity = Depends(get_websocket_identity),
    container: ApplicationContainer = Depends(get_container),
):
    """Endpoint cho phép client kết nối để nhận các sự kiện hệ thống theo thời gian thực."""
    ws_manager: WebSocketConnectionManager = container.eventing_manager.ws_manager
    connection_runtime = getattr(container, "connection_runtime", None)
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected", client_host=websocket.client.host, user_id=identity.user_id)
    resume_tasks: set[asyncio.Task] = set()

    async def run_resume_request(
        connection_id: str,
        envelope: RealtimeEnvelope,
    ) -> None:
        try:
            await _resume_execution(
                websocket,
                identity,
                container,
                connection_id,
                envelope,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Resume is intentionally detached from the socket receive loop so
            # R6 reconciliation can return on the same WebSocket. Unexpected
            # request-local failures are logged here instead of killing the
            # receiver that owns transport correlation.
            logger.exception(
                "R7-J detached resume request failed",
                execution_id=envelope.execution_id,
                connection_id=connection_id,
                error=str(error),
            )
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "status": "error",
                            "message": str(error),
                        }
                    )
                )
            except Exception:
                pass

    def resume_done(task: asyncio.Task) -> None:
        resume_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            # run_resume_request already logs request-local exceptions.
            pass

    try:
        active_connection_id = None
        # Vòng lặp để nhận tin nhắn từ client (ví dụ: yêu cầu subscribe)
        while True:
            data = await websocket.receive_text()
            if data:
                logger.info("data tu ws", data=data)
            try:
                message = json.loads(data)
                action = message.get("action")
                event_name = message.get("event_name")

                if action == "subscribe" and event_name:
                    await ws_manager.subscribe(websocket, event_name)
                    await websocket.send_text(json.dumps({"status": "success", "message": f"Subscribed to {event_name}"}))
                elif action == "unsubscribe" and event_name:
                    await ws_manager.unsubscribe(websocket, event_name)
                    await websocket.send_text(json.dumps({"status": "success", "message": f"Unsubscribed from {event_name}"}))
                else:
                    envelope = RealtimeEnvelope.model_validate(message)
                    if envelope.type == "connection.register":
                        active_connection_id = await _ensure_connection(
                            websocket,
                            identity,
                            connection_runtime,
                            envelope,
                        )
                    elif connection_runtime is None or active_connection_id is None:
                        raise ValueError("Client must send connection.register first")
                    elif envelope.type == "capability.register":
                        registration_service = connection_runtime.registration_service
                        if registration_service is None:
                            raise RuntimeError("Capability registration is unavailable.")
                        request = ClientCapabilityRegistration.model_validate(
                            envelope.payload
                        )
                        if request.connection_id != active_connection_id:
                            raise ValueError("Capability connection_id does not match active connection")
                        registered = registration_service.register(request)
                        await _send_realtime(
                            websocket,
                            RealtimeEnvelope(
                                type="capability.registered",
                                message_id=f"capabilities-{uuid.uuid4().hex}",
                                connection_id=active_connection_id,
                                payload={
                                    "capabilities": [
                                        item.model_dump(mode="json")
                                        for item in registered
                                    ]
                                },
                            ),
                        )
                        await _publish_waiting_tickets(
                            websocket,
                            identity,
                            container,
                            connection_id=active_connection_id,
                            client_id=request.client_id,
                        )
                    elif envelope.type == "execution.resume":
                        # Do not await inline. Resume planning may send
                        # capability.reconcile over this same socket and must
                        # allow the receive loop to ingest capability.reconciliation.
                        task = asyncio.create_task(
                            run_resume_request(
                                active_connection_id,
                                envelope,
                            ),
                            name=(
                                "r7-resume-request:"
                                f"{envelope.execution_id or 'unknown'}"
                            ),
                        )
                        resume_tasks.add(task)
                        task.add_done_callback(resume_done)
                    else:
                        await connection_runtime.handle_realtime_message(
                            active_connection_id,
                            envelope,
                        )
            except (ValidationError, ValueError, RuntimeError, LookupError, PermissionError) as error:
                logger.exception("Validation/Runtime error during WebSocket message processing", error=str(error))
                await websocket.send_text(json.dumps({"status": "error", "message": str(error)}))
            except json.JSONDecodeError:
                logger.error("Invalid JSON received from WebSocket client")
                await websocket.send_text(json.dumps({"status": "error", "message": "Invalid JSON format"}))

    except WebSocketDisconnect:
        if active_connection_id and connection_runtime is not None:
            # Fail connection-bound invocation/reconciliation futures first;
            # detached resume planners then observe transport loss rather than
            # hanging behind a dead socket.
            await connection_runtime.disconnect_connection(active_connection_id)
        for task in tuple(resume_tasks):
            if not task.done():
                task.cancel()
        if resume_tasks:
            await asyncio.gather(
                *tuple(resume_tasks),
                return_exceptions=True,
            )
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected", client_host=websocket.client.host, user_id=identity.user_id)
