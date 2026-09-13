import structlog

from .registry import DriverRegistry, RepositoryRegistry

from ..interfaces.cache import CacheDriver

from ..drivers.inmemory.driver import InMemoryDriver
from ..drivers.redis.driver import RedisDriver
from ..drivers.sqlite.driver import SQLiteDriver
from ..drivers.chroma.driver import ChromaVectorDriver

from ..repositories.sessions import SessionRepository
from ..services.embedding_service import EmbeddingService

from ...config.schemas import ConfigSchema


logger = structlog.get_logger(__name__)


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

            # Vẫn gọi disconnect để đảm bảo cleanup
            # trong trường hợp startup partially completed.
            await self.drivers.disconnect_all()
            return

        logger.info(
            "Storage Engine is shutting down..."
        )

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

    def is_driver_available(self, name: str) -> bool:
        """
        Runtime availability check.
        """

        return self.drivers.is_available(name)

    @property
    def is_started(self) -> bool:
        return self._started

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

        redis_available = (
            self.drivers.is_available("redis")
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

        if redis_available:
            redis_driver = self.get_cache_driver()

            self.repositories.register(
                "sessions",
                SessionRepository(
                    cache_driver=redis_driver
                ),
            )

            logger.info(
                "Session repository initialized"
            )

        else:
            logger.warning(
                "Redis driver unavailable. "
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