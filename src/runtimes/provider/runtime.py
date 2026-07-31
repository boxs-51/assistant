import time
import httpx
import structlog
from typing import Dict, Any, Optional

from ...kernel.base import BaseRuntime
from ...kernel.event import Event, EventBus
from ...provider.registry import ProviderRegistry
from ...provider.discovery import ProviderDiscovery
from ...provider.policies.routing_policy import RoutingPolicy
from ...provider.executor import ProviderExecutor
from ...provider.exceptions import NoAvailableProviderError
from ...gateway.circuit_breaker import CircuitBreakerManager

# Import các Handlers mới tách
from ...provider.handlers.chat_handler import ChatExecutionHandler
from ...provider.handlers.embedding_handler import EmbeddingExecutionHandler
from ...provider.handlers.model_handler import ModelOperationHandler
from ...provider.handlers.file_handler import FileOperationHandler

logger = structlog.get_logger(__name__)

class ProviderRuntime(BaseRuntime):
    """
    PROVIDER RUNTIME
    Sở hữu: Provider Registry, Discovery, Circuit Breakers, Fallback Chains.
    Ủy quyền xử lý chi tiết cho các Handlers chuyên biệt.
    """

    def __init__(self, event_bus: EventBus, circuit_breaker_manager: CircuitBreakerManager):
        super().__init__(name="ProviderRuntime")
        self.event_bus = event_bus
        self.circuit_breaker_manager = circuit_breaker_manager
        
        self.provider_registry: Optional[ProviderRegistry] = None
        self.executor: Optional[ProviderExecutor] = None
        self.routing_policy: Optional[RoutingPolicy] = None
        self.providers: Dict[str, Any] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

        # Handlers
        self.chat_handler: Optional[ChatExecutionHandler] = None
        self.embedding_handler: Optional[EmbeddingExecutionHandler] = None
        self.model_handler: Optional[ModelOperationHandler] = None
        self.file_handler: Optional[FileOperationHandler] = None

    async def initialize(self, context: Dict[str, Any]) -> None:
        """Khởi tạo Discovery, Registry & khởi tạo Handlers."""
        self._http_client = context.get("http_client") or httpx.AsyncClient()
        
        self.provider_registry = ProviderRegistry()
        discovery = ProviderDiscovery(registry=self.provider_registry)
        discovery.run()

        self.providers = self.provider_registry.list_all_providers()
        if not self.providers:
            raise RuntimeError("Configuration Error: No LLM providers are enabled.")

        self.routing_policy = RoutingPolicy(providers=self.providers)
        self.executor = ProviderExecutor(self.circuit_breaker_manager)

        # Khởi tạo các Sub-handlers
        handler_kwargs = {
            "providers": self.providers,
            "routing_policy": self.routing_policy,
            "executor": self.executor,
            "circuit_breaker_manager": self.circuit_breaker_manager,
        }
        self.chat_handler = ChatExecutionHandler(**handler_kwargs)
        self.embedding_handler = EmbeddingExecutionHandler(**handler_kwargs)
        self.model_handler = ModelOperationHandler(**handler_kwargs)
        self.file_handler = FileOperationHandler(**handler_kwargs)

        # Đăng ký Event Handlers
        self.event_bus.subscribe("ExecuteProvider", self._handle_execute_provider)
        self.event_bus.subscribe("ExecuteEmbeddings", self._handle_execute_embeddings)
        self.event_bus.subscribe("ExecuteModelOperation", self._handle_model_operation)
        self.event_bus.subscribe("ExecuteFileOperation", self._handle_file_operation)
            
        self._is_initialized = True
        logger.info("ProviderRuntime initialized", providers=list(self.providers.keys()))

    async def start(self) -> None:
        self._is_running = True
        logger.info("ProviderRuntime started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._http_client:
            await self._http_client.aclose()
        logger.info("ProviderRuntime stopped.")

    # ------------------------------------------------------------------
    # EVENT HANDLERS (Chỉ đóng vai trò Router điều hướng đến Handlers)
    # ------------------------------------------------------------------

    async def _handle_execute_provider(self, event: Event) -> None:
        body = event.payload.get("request_body", {})
        is_stream = event.payload.get("is_stream", False)
        session_id = event.session_id
        start_time = time.time()

        try:
            if not is_stream:
                response = await self.chat_handler.execute_with_fallback(self._http_client, body)
                latency = time.time() - start_time
                
                await self.event_bus.publish(Event(
                    event_name="ProviderResponded",
                    session_id=session_id,
                    payload={
                        "response": response.model_dump(),
                        "provider": response.provider,
                        "model": response.model,
                        "latency": latency
                    }
                ))
            else:
                async for chunk in self.chat_handler.stream_with_fallback(self._http_client, body):
                    await self.event_bus.publish(Event(
                        event_name="ProviderStreamChunk",
                        session_id=session_id,
                        payload={"chunk": chunk.model_dump(), "sse": chunk.to_sse()}
                    ))
                
                await self.event_bus.publish(Event(
                    event_name="ProviderStreamCompleted",
                    session_id=session_id,
                    payload={"latency": time.time() - start_time}
                ))

        except NoAvailableProviderError as e:
            logger.critical("Provider execution failed: No provider available", error=str(e))
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 503}
            ))
        except Exception as e:
            logger.error("Unhandled error in ProviderRuntime", error=str(e))
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 500}
            ))

    async def _handle_execute_embeddings(self, event: Event) -> None:
        body = event.payload.get("request_body", {})
        session_id = event.session_id
        try:
            response = await self.embedding_handler.execute(self._http_client, body)
            await self.event_bus.publish(Event(
                event_name="EmbeddingsResponded",
                session_id=session_id,
                payload={"response": response}
            ))
        except Exception as e:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 503 if isinstance(e, NoAvailableProviderError) else 500}
            ))

    async def _handle_model_operation(self, event: Event) -> None:
        provider_name = event.payload.get("provider_name")
        model_id = event.payload.get("model_id")
        session_id = event.session_id

        try:
            result = await self.model_handler.execute(
                provider_name=provider_name,
                model_id=model_id,
                http_client=self._http_client
            )
            await self.event_bus.publish(Event(
                event_name="ModelOperationResponded",
                session_id=session_id,
                payload={"result": result}
            ))
        except KeyError as e:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 404}
            ))
        except NotImplementedError:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": f"Functionality not implemented for '{provider_name}'.", "status_code": 501}
            ))
        except Exception as e:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 500}
            ))

    async def _handle_file_operation(self, event: Event) -> None:
        session_id = event.session_id
        try:
            res = await self.file_handler.execute(
                payload=event.payload,
                http_client=self._http_client
            )
            await self.event_bus.publish(Event(
                event_name="FileOperationResponded",
                session_id=session_id,
                payload={"result": res}
            ))
        except KeyError as e:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 404}
            ))
        except Exception as e:
            await self.event_bus.publish(Event(
                event_name="ProviderFailed",
                session_id=session_id,
                payload={"error": str(e), "status_code": 500}
            ))