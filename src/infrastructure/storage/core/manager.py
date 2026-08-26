import structlog
from typing import Any

from .registry import DriverRegistry, RepositoryRegistry
from ..interfaces.cache import CacheDriver
# --- Tạm thời import trực tiếp, sau sẽ thay bằng cơ chế import động ---
from ..drivers.redis.driver import RedisDriver
from ..drivers.sqlite.driver import SQLiteDriver
from ..repositories.sessions import SessionRepository
from ..drivers.chroma.driver import ChromaVectorDriver
from ..services.embedding_service import EmbeddingService


from ...config import settings
#from ...event_bus.manager import EventBus

logger = structlog.get_logger(__name__)

class StorageEngine:
    """
    Điểm truy cập chính, điều phối toàn bộ Storage Framework.
    """
    def __init__(self):
        self.config = settings.storage
        self.drivers = DriverRegistry()
        self.repositories = RepositoryRegistry()
        self.services = {} # Để lưu các service phức tạp hơn (e.g., SemanticCacheService)

    async def connect(self):
        """
        Khởi tạo và kết nối tất cả các driver, sau đó khởi tạo các repository.
        """
        logger.info("Storage Engine is starting...")
        self._initialize_drivers()
        self._initialize_services() # Khởi tạo các service trước khi kết nối driver
        await self.drivers.connect_all()
        self._initialize_repositories()
        logger.info("Storage Engine started successfully.")

    def get_cache_driver(self) -> CacheDriver:
        """
        Trả về cache driver đã được cấu hình.

        Application layer chỉ làm việc với CacheDriver abstraction,
        không truy cập raw Redis client.
        """
        driver = self.drivers.get("redis")
        if driver is None:
            raise RuntimeError("Redis cache driver is not configured")

        if not isinstance(driver, CacheDriver):
           raise TypeError("Configured Redis driver is not a CacheDriver")

        return driver


    async def disconnect(self):
        """Ngắt kết nối tất cả các driver một cách an toàn."""
        logger.info("Storage Engine is shutting down...")
        await self.drivers.disconnect_all()
        logger.info("Storage Engine shut down successfully.")

    def _initialize_drivers(self):
        """Đọc config và đăng ký các driver cần thiết."""
        logger.info("Initializing drivers based on configuration...")
        
        # Mapping từ tên driver trong config sang class
        driver_map = {
            "redis": RedisDriver,
            "sqlite": SQLiteDriver,
            "chroma": ChromaVectorDriver, # Đăng ký ChromaDB driver
            # "postgres": PostgresDriver, # Sẽ thêm trong tương lai
        }

        if not self.config.drivers:
            logger.warning("No 'drivers' section found in storage configuration. Skipping driver initialization.")
            return

        for driver_name, driver_config in self.config.drivers.items():
            if driver_config.enabled:
                driver_class = driver_map.get(driver_name)
                if driver_class:
                    logger.debug("Initializing driver", driver_name=driver_name)
                    instance = driver_class(driver_config.model_dump())
                    self.drivers.register(driver_name, instance)
                else:
                    logger.warning("Unknown driver specified in config", driver_name=driver_name)

    def _initialize_repositories(self):
        """Khởi tạo các repository và inject các driver cần thiết vào chúng."""
        logger.info("Initializing non-transactional repositories...")
        
        # Lấy các driver đã được đăng ký
        sqlite_driver = self.drivers.get("sqlite")
        redis_driver = self.drivers.get("redis")
        
        # Với kiến trúc Unit of Work, các repository liên quan đến SQL (User, Org, etc.)
        # sẽ được khởi tạo bên trong chính UnitOfWork context.
        # StorageEngine chỉ cần đăng ký các repository không thuộc về transaction của DB,
        # ví dụ như các repository tương tác với Redis, S3, etc.
        if not sqlite_driver:
            logger.warning(
                "SQLite driver not found. SQL-dependent operations must be performed "
                "within a Unit of Work context."
            )

        # Khởi tạo và đăng ký các repository phụ thuộc vào Redis
        if redis_driver:
            self.repositories.register("sessions", SessionRepository(cache_driver=redis_driver))
        else:
            logger.warning(
                "Redis driver not found, dependent repositories (Session) will not be available."
            )

    def _initialize_services(self):
        """Khởi tạo các service phức tạp hơn, có thể phụ thuộc vào các driver."""
        logger.info("Initializing storage services...")
        
        # Semantic Cache Service
        chroma_driver = self.drivers.get("chroma")
        if chroma_driver:
            embedding_service = EmbeddingService(settings.semantic_cache.model_dump())
            self.services["embedding_service"] = embedding_service
            
            from ..services.semantic_cache_service import SemanticCacheService
            semantic_cache_service = SemanticCacheService(vector_driver=chroma_driver, embedding_service=embedding_service)
            self.services["semantic_cache"] = semantic_cache_service
            logger.info("Semantic Cache service initialized.")
            
            