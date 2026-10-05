from collections.abc import Callable

import structlog

from .registry import DriverRegistry, RepositoryRegistry

from ..interfaces.cache import CacheDriver
from ..interfaces.object import ObjectStorageDriver

from ..drivers.inmemory.driver import InMemoryDriver
from ..drivers.redis.driver import RedisDriver
from ..drivers.sqlite.driver import SQLiteDriver
from ..drivers.chroma.driver import ChromaVectorDriver
from ..drivers.object_local.driver import LocalObjectStorageDriver

from ..repositories.sessions import SessionRepository
from ..services.embedding_service import EmbeddingService
from ..services.memory_promotion_admission import (
    DurableMemoryPromotionAdmission,
)
from ..services.promotion_reservation_issuer import (
    DurablePromotionReservationIssuer,
)
from ..services.promotion_reservation_recovery import (
    DurablePromotionReservationRecovery,
)
from ..services.tool_response_payload_memory_promotion import (
    DurableToolResponsePayloadMemoryPromotion,
)
from ..services.tool_response_payload_promotion_orchestration import (
    DurableToolResponsePayloadPromotionOrchestration,
)
from ..services.tool_response_payload_source_authority import (
    DurableToolResponsePayloadSourceAuthority,
)

from ...config.schemas import ConfigSchema


logger = structlog.get_logger(__name__)


_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION = (
    "tool_response_payload_memory_promotion"
)


class StorageServiceGenerationRevokedError(RuntimeError):
    """A retained storage service belongs to a revoked engine generation."""


class _GenerationBoundToolResponsePayloadMemoryPromotion:
    """Fail closed before entering B5 when the owning engine generation is stale."""

    def __init__(
        self,
        delegate: DurableToolResponsePayloadMemoryPromotion,
        *,
        generation: int,
        is_generation_active: Callable[[int], bool],
    ) -> None:
        self._delegate = delegate
        self._generation = generation
        self._is_generation_active = is_generation_active

    async def promote(
        self,
        *,
        source_ref,
        owner_user_id: str,
    ):
        if not self._is_generation_active(self._generation):
            raise StorageServiceGenerationRevokedError(
                "TOOL_RESPONSE_PAYLOAD Memory promotion service generation "
                "is no longer active"
            )
        return await self._delegate.promote(
            source_ref=source_ref,
            owner_user_id=owner_user_id,
        )


class StorageEngine:
    """
    Điểm truy cập chính của Storage Framework.

    Responsibilities:

    - initialize drivers
    - manage driver lifecycle
    - initialize services sau khi drivers connected
    - initialize repositories sau khi services/drivers ready

    StorageEngine không chứa business logic của từng loại cache.
    """

    def __init__(self, config: ConfigSchema):
        self.config = config

        self.drivers = DriverRegistry()
        self.repositories = RepositoryRegistry()

        self.services = {}

        self._started = False
        self._service_generation = 0
        self._active_service_generation = None

    async def connect(self) -> None:
        """
        Khởi động Storage Engine.

        Lifecycle:

            initialize drivers
                    ↓
              connect drivers
                    ↓
            initialize services
                    ↓
          initialize repositories
                    ↓
                STARTED
        """

        if self._started:
            logger.warning(
                "Storage Engine is already started"
            )
            return

        logger.info(
            "Storage Engine is starting..."
        )

        try:
            # -------------------------------------------------
            # Phase 1:
            # Instantiate drivers
            # -------------------------------------------------
            self._initialize_drivers()

            # -------------------------------------------------
            # Phase 2:
            # Connect drivers
            #
            # Required failure -> exception
            # Optional failure -> unavailable
            # -------------------------------------------------
            await self.drivers.connect_all()

            # Every activation attempt that reaches the service phase receives
            # a fresh, non-reusable generation. Retained services from older
            # generations can therefore never become valid again after restart.
            self._begin_service_generation()

            # -------------------------------------------------
            # Phase 3:
            # Initialize services
            #
            # MUST happen after driver connection.
            # -------------------------------------------------
            self._initialize_services()

            # -------------------------------------------------
            # Phase 4:
            # Initialize repositories
            # -------------------------------------------------
            self._initialize_repositories()

            self._started = True

            logger.info(
                "Storage Engine started successfully",
                driver_status=self.drivers.statuses(),
            )

        except Exception:
            self._started = False
            self._revoke_service_generation()

            logger.error(
                "Storage Engine failed to start",
                exc_info=True,
            )

            # Defensive cleanup.
            #
            # DriverRegistry.connect_all() already performs
            # rollback for required failures, but this keeps
            # StorageEngine safe if a later phase fails.
            await self.drivers.disconnect_all()

            self.services.clear()

            raise

    async def disconnect(self) -> None:
        """
        Shutdown Storage Engine.
        """

        if not self._started:
            logger.debug(
                "Storage Engine is not running"
            )

            # Revoke before any defensive teardown so a retained service from
            # a partially completed start cannot acquire a fresh SQL session.
            self._revoke_service_generation()

            # Vẫn gọi disconnect để đảm bảo cleanup
            # trong trường hợp startup partially completed.
            await self.drivers.disconnect_all()
            self.services.clear()
            return

        logger.info(
            "Storage Engine is shutting down..."
        )

        # Lifetime authority is revoked before driver teardown. An invocation
        # that already passed the guard keeps lower-layer failure/cancellation
        # semantics; later invocations fail before delegate/session acquisition.
        self._revoke_service_generation()

        try:
            await self.drivers.disconnect_all()

        finally:
            self.services.clear()
            self._started = False

        logger.info(
            "Storage Engine shut down successfully."
        )

    # =========================================================
    # Driver access
    # =========================================================

    def get_driver(self, name: str):
        """
        Lấy raw driver abstraction.

        Không kiểm tra availability.
        """

        driver = self.drivers.get(name)

        if driver is None:
            raise RuntimeError(
                f"Driver '{name}' is not configured"
            )

        return driver

    def get_cache_driver(
        self,
        name: str = "in-memory",
    ) -> CacheDriver:
        """
        Lấy CacheDriver abstraction.

        Không expose redis.Redis ra application layer.
        """

        driver = self.drivers.get(name)

        if driver is None:
            raise RuntimeError(
                f"Cache driver '{name}' is not configured"
            )

        if not isinstance(driver, CacheDriver):
            raise TypeError(
                f"Driver '{name}' is not a CacheDriver"
            )

        if not self.drivers.is_available(name):
            raise RuntimeError(
                f"Cache driver '{name}' is unavailable"
            )

        return driver

    def get_object_storage_driver(
        self,
        name: str = "object-local",
    ) -> ObjectStorageDriver:
        driver = self.drivers.get(name)
        if driver is None:
            raise RuntimeError(
                f"Object storage driver '{name}' is not configured"
            )
        if not isinstance(driver, ObjectStorageDriver):
            raise TypeError(
                f"Driver '{name}' is not an ObjectStorageDriver"
            )
        if not self.drivers.is_available(name):
            raise RuntimeError(
                f"Object storage driver '{name}' is unavailable"
            )
        return driver

    def is_driver_available(self, name: str) -> bool:
        """
        Runtime availability check.
        """

        return self.drivers.is_available(name)

    @property
    def is_started(self) -> bool:
        return self._started

    def get_tool_response_payload_memory_promotion(
        self,
    ) -> _GenerationBoundToolResponsePayloadMemoryPromotion:
        """Resolve the current B6 promotion service for trusted internal callers."""

        generation = self._active_service_generation
        if not self._started or generation is None:
            raise StorageServiceGenerationRevokedError(
                "TOOL_RESPONSE_PAYLOAD Memory promotion service is not active"
            )

        if not self.drivers.is_available("sqlite"):
            raise RuntimeError(
                "SQLite driver is unavailable for TOOL_RESPONSE_PAYLOAD "
                "Memory promotion"
            )

        service = self.services.get(_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION)
        if not isinstance(
            service,
            _GenerationBoundToolResponsePayloadMemoryPromotion,
        ):
            raise RuntimeError(
                "TOOL_RESPONSE_PAYLOAD Memory promotion service is unavailable"
            )

        if not self._is_service_generation_active(service._generation):
            raise StorageServiceGenerationRevokedError(
                "TOOL_RESPONSE_PAYLOAD Memory promotion service generation "
                "is no longer active"
            )

        return service

    # =========================================================
    # Internal service-generation lifetime fence
    # =========================================================

    def _begin_service_generation(self) -> int:
        self._service_generation += 1
        self._active_service_generation = self._service_generation
        return self._service_generation

    def _revoke_service_generation(self) -> None:
        self._active_service_generation = None

    def _is_service_generation_active(self, generation: int) -> bool:
        return (
            self._started
            and self._active_service_generation == generation
        )

    # =========================================================
    # Initialization
    # =========================================================

    def _initialize_drivers(self) -> None:
        """
        Đọc configuration và instantiate drivers.

        Chưa connect driver ở bước này.
        """

        logger.info(
            "Initializing drivers based on configuration..."
        )

        driver_map = {
            "redis": RedisDriver,
            "sqlite": SQLiteDriver,
            "chroma": ChromaVectorDriver,
            "in-memory": InMemoryDriver,
            "object-local": LocalObjectStorageDriver,
        }

        if not self.config.storage.drivers:
            logger.warning(
                "No 'drivers' section found in storage "
                "configuration."
            )
            return

        for driver_name, driver_config in (
            self.config.storage.drivers.items()
        ):
            if not driver_config.enabled:
                logger.debug(
                    "Driver disabled",
                    driver_name=driver_name,
                )
                continue

            driver_class = driver_map.get(driver_name)

            if driver_class is None:
                required = getattr(
                    driver_config,
                    "required",
                    True,
                )

                if required:
                    raise RuntimeError(
                        f"Unknown required storage driver: "
                        f"{driver_name}"
                    )

                logger.warning(
                    "Unknown optional storage driver; "
                    "skipping",
                    driver_name=driver_name,
                )

                continue

            required = getattr(
                driver_config,
                "required",
                True,
            )

            logger.debug(
                "Initializing driver",
                driver_name=driver_name,
                required=required,
            )

            instance = driver_class(
                driver_config
            )

            self.drivers.register(
                driver_name,
                instance,
                required=required,
            )

    # =========================================================
    # Repository initialization
    # =========================================================

    def _initialize_repositories(self) -> None:
        """
        Khởi tạo các Repository dựa trên driver availability.
        """

        logger.info(
            "Initializing repositories..."
        )

        sqlite_available = (
            self.drivers.is_available("sqlite")
        )

        # -----------------------------------------------------
        # SQLite
        # -----------------------------------------------------

        if not sqlite_available:
            logger.warning(
                "SQLite driver unavailable. "
                "SQL-dependent operations must use "
                "UnitOfWork only when DB becomes available."
            )

        # -----------------------------------------------------
        # Session Repository
        # -----------------------------------------------------

        cache_driver = None
        if self.drivers.is_available("redis"):
            cache_driver = self.get_driver("redis")
        elif self.drivers.is_available("in-memory"):
            cache_driver = self.get_cache_driver("in-memory")

        if cache_driver is not None:
            self.repositories.register(
                "sessions",
                SessionRepository(
                    cache_driver=cache_driver
                ),
            )

            logger.info(
                "Session repository initialized"
            )
        else:
            logger.warning(
                "No cache driver available. "
                "Session repository will not be registered."
            )

    # =========================================================
    # Service initialization
    # =========================================================

    def _initialize_services(self) -> None:
        """
        Khởi tạo services dựa trên các driver AVAILABLE.

        Không initialize service với một backend
        đang unavailable.
        """

        logger.info(
            "Initializing storage services..."
        )

        # -----------------------------------------------------
        # Semantic Cache
        # -----------------------------------------------------

        if self.drivers.is_available("chroma"):
            chroma_driver = self.drivers.get("chroma")

            embedding_service = EmbeddingService(
                self.config.semantic_cache.model_dump()
            )

            self.services[
                "embedding_service"
            ] = embedding_service

            from ..services.semantic_cache_service import (
                SemanticCacheService,
            )

            semantic_cache_service = (
                SemanticCacheService(
                    vector_driver=chroma_driver,
                    embedding_service=embedding_service,
                )
            )

            self.services[
                "semantic_cache"
            ] = semantic_cache_service

            logger.info(
                "Semantic Cache service initialized"
            )

        else:
            logger.warning(
                "Chroma driver unavailable. "
                "Semantic Cache service will not be initialized."
            )

        # -----------------------------------------------------
        # TOOL_RESPONSE_PAYLOAD -> durable Memory promotion
        # -----------------------------------------------------

        if self.drivers.is_available("sqlite"):
            sqlite_driver = self.drivers.get("sqlite")
            if sqlite_driver is None:
                raise RuntimeError(
                    "SQLite is available but no configured driver exists"
                )

            generation = self._active_service_generation
            if generation is None:
                raise RuntimeError(
                    "Storage service generation is not active"
                )

            # This public driver method is the only SQL session-context source
            # for the complete B1 -> B4 -> H-B2 -> B5 construction chain.
            session_context_factory = sqlite_driver.get_session

            source_authority = DurableToolResponsePayloadSourceAuthority(
                session_context_factory
            )
            reservation_issuer = DurablePromotionReservationIssuer(
                session_context_factory
            )
            reservation_recovery = DurablePromotionReservationRecovery(
                session_context_factory
            )
            orchestration = DurableToolResponsePayloadPromotionOrchestration(
                source_authority,
                reservation_issuer,
                reservation_recovery,
            )
            admission = DurableMemoryPromotionAdmission(
                session_context_factory
            )
            delegate = DurableToolResponsePayloadMemoryPromotion(
                orchestration,
                admission,
            )

            self.services[_TOOL_RESPONSE_PAYLOAD_MEMORY_PROMOTION] = (
                _GenerationBoundToolResponsePayloadMemoryPromotion(
                    delegate,
                    generation=generation,
                    is_generation_active=self._is_service_generation_active,
                )
            )

            logger.info(
                "TOOL_RESPONSE_PAYLOAD Memory promotion service initialized",
                storage_generation=generation,
            )
        else:
            logger.info(
                "SQLite driver unavailable. "
                "TOOL_RESPONSE_PAYLOAD Memory promotion service "
                "will not be initialized."
            )