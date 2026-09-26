# src/runtimes/workflow/runtime.py
from typing import Dict, Any
import uuid
import structlog

from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent
from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from ...domain.schemas.identity import Identity
from ...domain.schemas.message import contains_canonical_asset_content
from ...domain.schemas.agent_execution import AgentExecutionLimits
from ..agent.contracts.context import AgentExecutionContext
from ..agent.adapters.messages import jsonable
from ..agent.ids import AgentExecutionIdFactory
from ..agent.waiting_ticket import build_waiting_ticket_payload

logger = structlog.get_logger(__name__)


class WorkflowRuntime(BaseRuntime):
    def __init__(
        self,
        execution_id_factory: AgentExecutionIdFactory | None = None,
    ):
        manifest = RuntimeManifest(
            id="workflow_runtime",
            name="WorkflowRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.container = None
        self._execution_id_factory = (
            execution_id_factory or AgentExecutionIdFactory()
        )

    async def initialize(self, context: RuntimeContext) -> None:
        self.container = context.container
        self.event_bus = context.event_bus
        # Lắng nghe các Domain Event từ các Runtime khác
        self.event_bus.subscribe("session.event.loaded", self._handle_session_loaded)
        self.event_bus.subscribe("context.event.built", self._handle_context_built)
        self.event_bus.subscribe("capability.event.executed", self._handle_capability_executed)
        
        self._is_initialized = True
        logger.info("WorkflowRuntime initialized with updated event subscriptions")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    @staticmethod
    def _stream_text(content: Any) -> str:
        """Normalize message content to canonical SSE delta.content text."""
        value = jsonable(content)
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            raise TypeError(
                "Stream content must be a string or a list of content parts, "
                f"got {type(value).__name__}."
            )

        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue

            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)

            data = item.get("data")
            if isinstance(data, str):
                parts.append(data)
            elif isinstance(data, dict) and isinstance(data.get("data"), str):
                parts.append(data["data"])
        return "".join(parts)

    async def _handle_session_loaded(self, event: BaseEvent):
        """Bước 1: Sau khi Session tải xong -> Yêu cầu Context Runtime xây dựng Prompt."""
        logger.debug("Handling session loaded, triggering context build", session_id=event.session_id)
        
        await self.event_bus.publish(BaseEvent(
            event_name="context.command.build",
            session_id=event.session_id,
            turn_id=event.turn_id,
            payload=event.payload
        ))

    def _canonical_asset_dispatch_ready(
        self,
        event: BaseEvent,
        mode: str,
    ) -> bool:
        """Release canonical assets only onto explicitly activated surfaces."""

        if mode not in {"DIRECT", "AGENT"}:
            return False

        runtime_attr = (
            "direct_chat_runtime" if mode == "DIRECT" else "agent_runtime"
        )
        if getattr(self.container, runtime_attr, None) is None:
            return False

        provider_runtime = getattr(self.container, "provider_runtime", None)
        if provider_runtime is None or not bool(
            getattr(provider_runtime, "asset_projection_ready", False)
        ):
            return False

        identity_data = event.payload.get("identity")
        try:
            identity = (
                identity_data
                if isinstance(identity_data, Identity)
                else Identity.model_validate(identity_data)
            )
        except Exception:
            return False
        return bool(str(identity.user_id or "").strip())

    async def _handle_context_built(self, event: BaseEvent):
        """Bước 2: Sau khi Context dựng xong -> Yêu cầu Provider Runtime gọi LLM."""
        logger.debug(
            "Handling context built, triggering provider execution",
            session_id=event.session_id,
        )

        body = dict(event.payload.get("request_body", {}))
        mode = body.pop("_chat_execution_mode", "DIRECT")
        event.payload["request_body"] = body

        if (
            contains_canonical_asset_content(body.get("messages", []))
            and not self._canonical_asset_dispatch_ready(event, mode)
        ):
            await self.event_bus.publish(
                BaseEvent(
                    event_name="provider.failed",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={
                        "error": (
                            "Canonical asset content requires provider hydration "
                            "before inference."
                        ),
                        "error_code": "ASSET_HYDRATION_REQUIRED",
                        "failure_domain": "MESSAGE_ASSET",
                        "retryable": False,
                        "status_code": 409,
                    },
                )
            )
            return

        if mode == "DIRECT" and getattr(
            self.container,
            "direct_chat_runtime",
            None,
        ):
            await self._execute_direct(event, body)
            return
        if mode == "AGENT" and getattr(
            self.container,
            "agent_runtime",
            None,
        ):
            await self._execute_agent(event, body)
            return
        await self.event_bus.publish(
            BaseEvent(
                event_name="provider.chat.execute",
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload=event.payload,
            )
        )

    @staticmethod
    def _response_payload(response, extra_metadata: dict | None = None) -> dict:
        return {
            "id": response.execution_id,
            "model": response.model,
            "object": "gateway_response",
            "choices": [{
                "index": 0,
                "message": jsonable(response.message),
                "finish_reason": response.finish_reason,
            }],
            "usage": response.usage.model_dump(mode="json"),
            "metadata": {"provider": response.provider, **(extra_metadata or {})},
        }

    async def _execute_direct(
        self,
        event: BaseEvent,
        body: dict,
        *,
        fallback_notice: dict | None = None,
    ) -> None:
        try:
            identity_data = event.payload.get("identity")
            identity = identity_data if isinstance(identity_data, Identity) else Identity.model_validate(identity_data)
            metadata = body.get("metadata", {})
            user_metadata = metadata.get("user", {}) if isinstance(metadata, dict) else {}
            response = await self.container.direct_chat_runtime.execute(
                messages=body.get("messages", []),
                identity=identity,
                session_id=event.session_id,
                connection_id=body.get("connection_id"),
                model=body.get("model", ""),
                timezone_name=user_metadata.get("timezone"),
                metadata=metadata,
            )
            payload = self._response_payload(
                response,
                {"agent_fallback": fallback_notice} if fallback_notice else None,
            )
            if body.get("config", {}).get("stream"):
                if fallback_notice:
                    await self.event_bus.publish(BaseEvent(
                        event_name="provider.stream.chunk_emitted",
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        payload={"chunk": fallback_notice},
                    ))
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.stream.chunk_emitted",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={"chunk": {
                        "id": payload["id"],
                        "model": payload["model"],
                        "choices": [{
                            "index": 0,
                            "delta": {"content": self._stream_text(response.message.content)},
                        }],
                    }},
                ))
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.stream.completed",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={},
                ))
            else:
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.chat.responded",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={"response": payload},
                ))
        except Exception as exc:
            await self.event_bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload={"error": str(exc), "status_code": 500},
            ))

    async def _execute_agent(self, event: BaseEvent, body: dict) -> None:
        try:
            routing = body.get("metadata", {}).get("routing", {})
            agent_id = body.get("agent_id") or routing.get("default_agent_id")
            if not agent_id:
                await self._execute_direct(
                    event,
                    body,
                    fallback_notice={
                        "status": "AGENT_FALLBACK",
                        "reason": "AGENT_NOT_SPECIFIED",
                        "message": "No agent_id was specified; chat_direct handled this request.",
                        "fallback": "DIRECT",
                    },
                )
                return
            capability_runtime = getattr(self.container, "capability_runtime", None)
            catalog = getattr(capability_runtime, "catalog", None)
            identity = None
            if catalog is not None and catalog.contains_definition(agent_id):
                identity_data = event.payload.get("identity")
                identity = identity_data if isinstance(identity_data, Identity) else Identity.model_validate(identity_data)
                definition = catalog.get_definition(agent_id)
                if not self.container.authorization_service.is_allowed(identity, definition):
                    raise PermissionError(f"Agent '{agent_id}' is not permitted for this identity.")
            agent = self.container.agent_registry.get(agent_id)
            if agent is None:
                await self._execute_direct(
                    event,
                    body,
                    fallback_notice={
                        "status": "AGENT_FALLBACK",
                        "reason": "AGENT_NOT_FOUND",
                        "requested_agent_id": agent_id,
                        "message": f"Agent '{agent_id}' is unavailable; chat_direct handled this request.",
                        "fallback": "DIRECT",
                    },
                )
                return
            if identity is None:
                identity_data = event.payload.get("identity")
                identity = identity_data if isinstance(identity_data, Identity) else Identity.model_validate(identity_data)
            messages = body.get("messages", [])
            prompt = next((item.get("content") for item in reversed(messages) if item.get("role") == "user"), "")
            context = AgentExecutionContext.create(
                execution_id=self._execution_id_factory.new_id(),
                agent_id=agent_id,
                session_id=event.session_id,
                correlation_id=event.turn_id or f"corr_{uuid.uuid4().hex}",
                identity=identity,
                limits=AgentExecutionLimits(),
                request_id=event.turn_id,
                connection_id=body.get("connection_id"),
                agent=agent,
                input={"prompt": prompt},
                metadata={
                    **body.get("metadata", {}),
                    "model": body.get("model", ""),
                    "timezone": body.get("metadata", {}).get("user", {}).get("timezone"),
                },
            )
            supervisor = getattr(
                self.container,
                "agent_execution_supervisor",
                None,
            )
            if supervisor is None:
                result = await self.container.agent_runtime.execute(context)
            else:
                result = await supervisor.run(
                    context,
                    lambda: self.container.agent_runtime.execute(context),
                )
            if result.error_code == "WAITING_FOR_CONNECTION":
                durable_store = getattr(
                    self.container,
                    "agent_durable_store",
                    None,
                )
                current_loader = getattr(
                    durable_store,
                    "load_current_checkpoint",
                    None,
                )
                pending_loader = getattr(
                    durable_store,
                    "load_checkpoint_pending_invocations",
                    None,
                )
                execution_loader = getattr(
                    durable_store,
                    "load_execution",
                    None,
                )
                if not (
                    callable(current_loader)
                    and callable(pending_loader)
                    and callable(execution_loader)
                ):
                    raise RuntimeError(
                        "NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE: "
                        "workflow WAITING projection requires the canonical R7 store."
                    )

                checkpoint = await current_loader(result.execution_id)
                if checkpoint is None:
                    raise RuntimeError(
                        "NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE: "
                        "WAITING execution has no normalized current checkpoint."
                    )
                pending = await pending_loader(checkpoint.checkpoint_id)
                execution = await execution_loader(result.execution_id)
                if execution is None:
                    raise RuntimeError(
                        "NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE: "
                        "WAITING execution disappeared before projection."
                    )

                pending_capability_ids = [
                    item.capability_id
                    for item in pending
                ]
                waiting = build_waiting_ticket_payload(
                    execution,
                    checkpoint,
                    pending,
                )
                waiting.update(
                    {
                        "status": "WAITING_FOR_CONNECTION",
                        "retry_policy": "AUTO"
                        if waiting["auto_resume_allowed"]
                        else "USER_CONFIRM",
                        "pending_capability_id": (
                            pending_capability_ids[0]
                            if pending_capability_ids
                            else None
                        ),
                    }
                )
                if body.get("config", {}).get("stream"):
                    await self.event_bus.publish(BaseEvent(
                        event_name="provider.stream.chunk_emitted",
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        payload={"chunk": waiting},
                    ))
                    await self.event_bus.publish(BaseEvent(
                        event_name="provider.stream.completed",
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        payload={},
                    ))
                else:
                    await self.event_bus.publish(BaseEvent(
                        event_name="provider.chat.responded",
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        payload={"response": waiting, "_http_status": 202},
                    ))
                return
            if result.final_message is None:
                if result.error_code:
                    await self.event_bus.publish(BaseEvent(
                        event_name="provider.failed",  # compatibility event name
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        payload={
                            "error": result.error_message or result.error_code,
                            "error_code": result.error_code,
                            "failure_domain": result.failure_domain or "AGENT",
                            "retryable": result.retryable,
                            "execution_id": result.execution_id,
                            "status_code": 500,
                        },
                    ))
                    return
                raise RuntimeError("Agent produced no final message.")
            response = {
                "id": result.execution_id,
                "model": getattr(agent, "model", None) or body.get("model", ""),
                "object": "gateway_response",
                "choices": [{"index": 0, "message": jsonable(result.final_message), "finish_reason": "stop"}],
                "usage": result.usage.model_dump(mode="json"),
                "metadata": {"provider": "agent", "agent_id": agent_id},
            }
            if body.get("config", {}).get("stream"):
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.stream.chunk_emitted",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={"chunk": {
                        "id": response["id"],
                        "model": response["model"],
                        "choices": [{
                            "index": 0,
                            "delta": {"content": self._stream_text(result.final_message.content)},
                        }],
                    }},
                ))
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.stream.completed",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={},
                ))
            else:
                await self.event_bus.publish(BaseEvent(
                    event_name="provider.chat.responded",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    payload={"response": response},
                ))
        except Exception as exc:
            error_code = (
                getattr(exc, "code", None)
                or getattr(exc, "error_code", None)
                or type(exc).__name__
            )
            await self.event_bus.publish(BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload={
                    "error": str(exc),
                    "error_code": error_code,
                    "failure_domain": getattr(exc, "failure_domain", "AGENT"),
                    "retryable": bool(getattr(exc, "retryable", False)),
                    "status_code": (
                        403 if isinstance(exc, PermissionError)
                        else 400 if isinstance(exc, (ValueError, LookupError))
                        else 500
                    ),
                },
            ))

    async def _handle_capability_executed(self, event: BaseEvent):
        """Bước 3: Sau khi Capability/Tool chạy xong -> Gửi kết quả về Context Runtime để build lại Prompt."""
        logger.debug("Handling capability executed, re-triggering context build", session_id=event.session_id)
        
        await self.event_bus.publish(BaseEvent(
            event_name="context.command.build",
            session_id=event.session_id,
            turn_id=event.turn_id,
            payload={"tool_result": event.payload.get("result")}
        ))