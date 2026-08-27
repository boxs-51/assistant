import asyncio
import structlog
import inspect
import traceback
import functools
from collections import defaultdict
from typing import Tuple, Awaitable, Dict, Any, Callable, Mapping, Type, List, TYPE_CHECKING
from enum import IntEnum

from ...domain.schemas.event import BaseEvent
from .registry import EventRegistry
from ..storage.interfaces.cache import CacheDriver

if TYPE_CHECKING:
    from ..storage.core.unit_of_work import SqlAlchemyUnitOfWork


class EventPriority(IntEnum):
    HIGH = 1
    NORMAL = 5
    LOW = 10

logger = structlog.get_logger(__name__)


class EventDispatcher:
    def __init__(

        self,
        registry: EventRegistry,
        queue: asyncio.PriorityQueue,
        dependency_container: Any,
        cache_driver: CacheDriver,
        uow_factory: Callable[[], "SqlAlchemyUnitOfWork"],
        max_retries: int = 3,
        idempotency_ttl_seconds: int = 3600,
    ):
        self._registry = registry
        self._queue = queue
        self._dependency_container = dependency_container
        self._cache_driver = cache_driver
        self._uow_factory = uow_factory
        self._max_retries = max_retries
        self._idempotency_ttl = idempotency_ttl_seconds

        logger.info("EventDispatcher initialized", max_retries=max_retries)

    def _resolve_dependencies(self, handler: Callable) -> Tuple[Dict[str, Any], List[Tuple[str, Type]]]:
        resolved_deps = {}
        repo_types_needed = []
        sig = inspect.signature(handler)

        for name, param in sig.parameters.items():
            if param.annotation is not inspect.Parameter.empty:
                dep_instance = self._dependency_container.get_dependency(param.annotation)
                if dep_instance:
                    if isinstance(dep_instance, type):
                        repo_types_needed.append((name, dep_instance))
                    else:
                        resolved_deps[name] = dep_instance
                        
        return resolved_deps, repo_types_needed

    async def _execute_handler_with_uow(
        self, handler: Callable, event: BaseEvent, static_deps: Dict, repo_types: List[Tuple[str, Type]]
    ):
        async with self._uow_factory() as uow:
            repo_deps = {}
            for name, repo_type in repo_types:
                for attr_name, attr_value in uow.__dict__.items():
                    if isinstance(attr_value, repo_type):
                        repo_deps[name] = attr_value
                        break
            
            all_deps = {**static_deps, **repo_deps}
            await handler(event, **all_deps)
            await uow.commit()

    async def _execute_handler(self, handler: Callable, event: BaseEvent):
        static_dependencies, repo_types_needed = self._resolve_dependencies(handler)
        handler_name = getattr(handler, "__name__", str(handler))

        if repo_types_needed:
            execution_func = functools.partial(
                self._execute_handler_with_uow,
                handler=handler,
                event=event,
                static_deps=static_dependencies,
                repo_types=repo_types_needed
            )
        else:
            execution_func = functools.partial(handler, event, **static_dependencies)

        for attempt in range(self._max_retries):
            try:
                await execution_func()
                return
            except Exception as e:
                logger.error(
                    "Event handler execution failed",
                    event_name=event.event_name,
                    handler_name=handler_name,
                    attempt=attempt + 1,
                    error=str(e),
                    exc_info=True
                )
                if attempt + 1 == self._max_retries:
                    logger.critical(
                        "Handler failed maximum retries. Publishing to DLQ.",
                        event_name=event.event_name,
                        handler_name=handler_name
                    )
                    dlq_event = BaseEvent(
                        session_id=getattr(event, "session_id", "system"),
                        event_name="system.event.failed",
                        payload={
                            "failed_event": event.model_dump(),
                            "failed_handler": handler_name,
                            "error_message": str(e),
                            "stack_trace": traceback.format_exc(),
                        }
                    )
                    asyncio.create_task(self._dependency_container.event_bus.publish(dlq_event))
                    raise

    async def _is_event_processed(self, event_id: str) -> bool:
        if not self._cache_driver:
            return False

        try:
            return await self._cache_driver.exists(
                f"processed_event:{event_id}"
            )
        except Exception as exc:
            logger.warning(
                "Idempotency store unavailable; "
                "continuing event processing without idempotency check",
                event_id=event_id,
                error=str(exc),
                exc_info=True,
            )
            return False

    async def _mark_event_as_processed(self, event_id: str):
        if not self._cache_driver:
            return

        try:
            await self._cache_driver.set(
                f"processed_event:{event_id}",
                "processed",
                expire=self._idempotency_ttl,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist event idempotency marker",
                event_id=event_id,
                error=str(exc),
                exc_info=True,
            )

    async def start(self):
        logger.info("EventDispatcher loop starting...")
        while True:
            try:
                _, _, event, future = await self._queue.get()
                asyncio.create_task(self._dispatch_event_task(event, future))
                self._queue.task_done()
            except Exception as e:
                logger.critical("Fatal error in EventDispatcher loop", error=str(e), exc_info=True)

    async def _dispatch_event_task(self, event: BaseEvent, future: asyncio.Future):
        """Worker task riêng biệt cho từng event để tránh ngắt đoạn Queue Loop."""
        event_identifier = getattr(event, "event_id", None) or getattr(event, "id", None)

        try:
            if event_identifier and await self._is_event_processed(event_identifier):
                logger.warning("Duplicate event skipped by Idempotency check", event_id=event_identifier)
                if future and not future.done():
                    future.set_result(True)
                return

            handlers = self._registry.get_handlers(event.event_name)
            logger.debug("Dispatching event to handlers", event_name=event.event_name, count=len(handlers))

            # Thực thi song song tất cả các handler liên kết với Event
            tasks = [self._execute_handler(handler, event) for handler in handlers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            first_error = next((res for res in results if isinstance(res, Exception)), None)

            if first_error:
                if future and not future.done():
                    future.set_exception(first_error)
            else:
                if event_identifier:
                    await self._mark_event_as_processed(event_identifier)
                if future and not future.done():
                    future.set_result(True)

        except Exception as e:
            logger.error("Unhandled error during event dispatching", event_name=event.event_name, exc_info=True)
            if future and not future.done():
                future.set_exception(e)

class EventBus:
    """
    Lớp trung gian tiếp nhận Event. Tất cả đăng ký được ủy quyền (delegate) qua EventRegistry
    để nhất quán với Dispatcher.
    """
    def __init__(self, registry: EventRegistry, priority_map: Mapping[str, EventPriority]):
        self._queue: asyncio.PriorityQueue[Tuple[int, int, BaseEvent, asyncio.Future]] = asyncio.PriorityQueue()
        self._registry = registry
        self._priority_map = priority_map
        self._sequence = 0
        logger.info("EventBus initialized with PriorityQueue & Shared Registry.")

    @property
    def queue(self) -> asyncio.PriorityQueue:
        return self._queue

    def subscribe(self, event_name: str, handler: Callable[[BaseEvent], Awaitable[None]]):
        """Đăng ký handler trực tiếp vào EventRegistry chung."""
        self._registry.register(event_name, handler)

    def unsubscribe(self, event_name: str, handler: Callable[[BaseEvent], Awaitable[None]]):
        self._registry.unsubscribe(event_name, handler)

    def publish(self, event: BaseEvent) -> asyncio.Future:
        """
        Phát sự kiện vào Queue. 
        Trả về Future cho phép caller await nếu cần (Fire-and-Forget thì không await).
        """
        future = asyncio.get_event_loop().create_future()
        priority = self._priority_map.get(event.event_name, EventPriority.NORMAL)
        self._sequence += 1
        
        event_id = getattr(event, 'event_id', None) or getattr(event, 'id', 'unknown')
        self._queue.put_nowait((priority.value, self._sequence, event, future))
        
        logger.info("Event enqueued for publishing", event_name=event.event_name, event_id=event_id, priority=priority.name)
        return future