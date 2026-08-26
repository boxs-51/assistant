from __future__ import annotations

import httpx
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from ..infrastructure.event_bus.bus import EventBus

if TYPE_CHECKING:
    from ..application.container import ApplicationContainer
    from .kernel import RuntimeKernel

logger = structlog.get_logger(__name__)


class LifecycleState(Enum):
    CREATED = "Created"
    INITIALIZED = "Initialized"
    STARTED = "Started"
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPING = "Stopping"
    STOPPED = "Stopped"
    DISPOSED = "Disposed"
    FAILED = "Failed"


class HealthStatus(Enum):
    HEALTHY = "Healthy"
    WARNING = "Warning"
    DEGRADED = "Degraded"
    FAILED = "Failed"


@dataclass(slots=True)
class RuntimeContext:
    """Single dependency boundary shared by every runtime instance."""

    kernel: "RuntimeKernel"
    container: "ApplicationContainer"
    config: Any
    logger: structlog.BoundLogger
    event_bus: EventBus
    storage: Any
    uow_factory: Callable[[], Any]
    http_client: httpx.AsyncClient
    metrics: Any = None
    tracer: Any = None
    clock: Any = None


@dataclass
class RuntimeManifest:
    id: str
    name: str
    version: str = "1.0.0"
    dependencies: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseRuntime(ABC):
    """Mọi Runtime trong hệ thống bắt buộc kế thừa từ class này."""

    def __init__(self, manifest: RuntimeManifest):
        self.manifest = manifest
        self.context: Optional[RuntimeContext] = None
        self.state: LifecycleState = LifecycleState.CREATED

    def initialize(self, context: RuntimeContext) -> None:
        self.context = context
        self.state = LifecycleState.INITIALIZED

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    async def check_health(self) -> HealthStatus:
        """Được gọi bởi Health Monitor để kiểm tra sức khỏe Runtime."""
        return HealthStatus.HEALTHY

    async def dispose(self) -> None:
        self.state = LifecycleState.DISPOSED