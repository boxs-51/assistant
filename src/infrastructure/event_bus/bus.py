import asyncio
import structlog
import inspect
import traceback
import functools
from collections import defaultdict
from typing import Tuple, Awaitable, Dict, Any, Callable, Mapping, Type, List, TYPE_CHECKING
from enum import IntEnum

from ...schemas.event import BaseEvent
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
    """
    Lớp điều phối sự kiện, chịu trách nhiệm lấy sự kiện từ hàng đợi,
    tìm và thực thi các handler tương ứng.
    """
    def __init__(
        self,
        registry: EventRegistry,
        queue: asyncio.Queue,
        dependency_container: Any, # Thường là EventingManager
        cache_driver: CacheDriver,
        uow_factory: Callable[[], "SqlAlchemyUnitOfWork"],
        max_retries: int = 3,
        idempotency_ttl_seconds: int = 3600, # 1 giờ
    ):
        self._registry = registry
        self._queue = queue
        self._dependency_container = dependency_container
        self._cache_driver = cache_driver
        self._uow_factory = uow_factory
        self._max_retries = max_retries
        self._idempotency_ttl = idempotency_ttl_seconds
        self._session_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        logger.info("EventDispatcher initialized", max_retries=max_retries, idempotency_ttl=idempotency_ttl_seconds)

    def _resolve_dependencies(self, handler: Callable) -> Dict[str, Any]:
        """
        Phân tích chữ ký của handler và "tiêm" các dependency cần thiết.
        """
        resolved_deps = {}
        repo_types_needed = []
        sig = inspect.signature(handler)

        for name, param in sig.parameters.items():
            if param.annotation is not inspect.Parameter.empty:
                dep_instance = self._dependency_container.get_dependency(param.annotation)
                if dep_instance:
                    # Nếu get_dependency trả về một kiểu dữ liệu (là Repository), lưu lại để xử lý sau
                    if isinstance(dep_instance, type):
                        repo_types_needed.append((name, dep_instance))
                    else: # Nếu là instance (Manager, Engine), inject trực tiếp
                        resolved_deps[name] = dep_instance
                        
        return resolved_deps, repo_types_needed

    async def _execute_handler_with_uow(self, handler: Awaitable, event: BaseEvent, static_deps: Dict, repo_types: List[Tuple[str, Type]]):
        """Thực thi handler bên trong một UnitOfWork."""
        async with self._uow_factory() as uow:
            # Lấy các instance repository từ UnitOfWork
            repo_deps = {}
            for name, repo_type in repo_types:
                # Tìm repo trong uow bằng cách so sánh kiểu dữ liệu
                for attr_name, attr_value in uow.__dict__.items():
                    if isinstance(attr_value, repo_type):
                        repo_deps[name] = attr_value
                        break
            
            # Kết hợp dependency tĩnh và dependency từ UoW
            all_deps = {**static_deps, **repo_deps}
            
            await handler(event, **all_deps)
            await uow.commit()

    async def _execute_handler(self, handler: Awaitable, event: BaseEvent):
        """Thực thi một handler với cơ chế retry."""
        static_dependencies, repo_types_needed = self._resolve_dependencies(handler)
        handler_name = getattr(handler, '__name__', 'unknown')

        # Chọn phương thức thực thi phù hợp
        if repo_types_needed:
            # Nếu cần Repo, dùng hàm có UoW
            execution_func = functools.partial(
                self._execute_handler_with_uow,
                handler=handler,
                event=event,
                static_deps=static_dependencies,
                repo_types=repo_types_needed
            )
        else:
            # Nếu không, dùng hàm thường
            execution_func = functools.partial(handler, event, **static_dependencies)

        for attempt in range(self._max_retries):
            try:
                await execution_func()
                return
            except Exception as e:
                logger.error(
                    "Event handler failed",
                    event_name=event.event_name,
                    handler_name=handler_name,
                    attempt=attempt + 1,
                    exc_info=True
                )
                if attempt + 1 == self._max_retries:
                    logger.critical(
                        "Event handler failed after max retries, publishing to DLQ",
                        event_name=event.event_name,
                        handler_name=handler_name,
                    )
                    # --- Dead Letter Queue (DLQ) Implementation ---
                    dlq_event = BaseEvent(
                        event_name="system.event.failed",
                        payload={
                            "failed_event": event.model_dump(),
                            "failed_handler": handler_name,
                            "error_message": str(e),
                            "stack_trace": traceback.format_exc(),
                        }
                    )
                    # Fire-and-forget a task to publish the DLQ event
                    asyncio.create_task(self._dependency_container.bus.publish(dlq_event))
                    raise

    async def _is_event_processed(self, event_id: str) -> bool:
        """Kiểm tra xem event đã được xử lý trước đó chưa."""
        cache_key = f"processed_event:{event_id}"
        return await self._cache_driver.exists(cache_key)

    async def _mark_event_as_processed(self, event_id: str):
        """Đánh dấu event đã được xử lý thành công."""
        cache_key = f"processed_event:{event_id}"
        await self._cache_driver.set(cache_key, "processed", ttl=self._idempotency_ttl)

    async def start(self):
        """Bắt đầu vòng lặp xử lý sự kiện từ hàng đợi."""
        logger.info("EventDispatcher started and listening for events...")
        while True:
            # PriorityQueue trả về tuple (priority, event, future)
            priority, event, future = await self._queue.get()
            logger.debug("Dequeued event", event_name=event.event_name, priority=priority)

            session_id = getattr(event, 'session_id', None)
            lock = self._session_locks[session_id] if session_id else None

            try:
                # --- Idempotency Check ---
                if await self._is_event_processed(event.id):
                    logger.warning("Idempotency check: Event already processed, skipping.", event_id=event.id, event_name=event.event_name)
                    future.set_result(True) # Báo thành công vì đã xử lý rồi
                    continue

                # --- Session Lock ---
                if lock:
                    logger.debug("Acquiring lock for session", session_id=session_id)
                    await lock.acquire()

                handlers = self._registry.get_handlers(event.event_name)
                logger.info("Dispatching event", event_name=event.event_name, handlers_count=len(handlers))

                tasks = [self._execute_handler(handler, event) for handler in handlers]

                # Chạy tất cả các handler cho sự kiện này
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Kiểm tra xem có lỗi nào không thể retry được không
                final_exception = next((res for res in results if isinstance(res, Exception)), None)

                if final_exception: # Nếu có lỗi, báo lỗi cho future
                    future.set_exception(final_exception)
                else:
                    # Nếu tất cả handler thành công, đánh dấu event đã xử lý
                    await self._mark_event_as_processed(event.id)
                    future.set_result(True) # Báo hiệu hoàn thành

            except Exception as e:
                logger.error("Critical error in EventDispatcher loop", exc_info=True)
                if not future.done():
                    future.set_exception(e)
            finally:
                # --- Release Session Lock ---
                if lock and lock.locked():
                    lock.release()
                    logger.debug("Released lock for session", session_id=session_id)
                self._queue.task_done()


class EventBus:
    """
    Lớp trung gian để nhận và đưa sự kiện vào hàng đợi.
    Nó cung cấp một phương thức `publish` có thể được `await`.
    """
    def __init__(self, priority_map: Mapping[str, EventPriority]):
        self._queue: asyncio.PriorityQueue[Tuple[int, BaseEvent, asyncio.Future]] = asyncio.PriorityQueue()
        self._priority_map = priority_map
        logger.info("EventBus initialized with PriorityQueue.")

    @property
    def queue(self) -> asyncio.PriorityQueue:
        return self._queue

    def publish(self, event: BaseEvent) -> asyncio.Future:
        """
        Phát một sự kiện bằng cách đưa nó vào hàng đợi.
        Trả về một Future để bên gọi có thể chờ (await) nếu cần.
        Sự kiện sẽ được ưu tiên dựa trên `event_name`.
        """
        future = asyncio.Future()
        # Lấy độ ưu tiên từ map, mặc định là NORMAL nếu không được định nghĩa
        priority = self._priority_map.get(event.event_name, EventPriority.NORMAL)
        
        # PriorityQueue sắp xếp theo giá trị nhỏ nhất trước
        self._queue.put_nowait((priority.value, event, future))
        
        logger.info("Event enqueued for publishing", event_name=event.event_name, event_id=event.event_id, priority=priority.name)
        return future