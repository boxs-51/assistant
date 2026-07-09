import structlog
from typing import Dict, Any

from .registry import DriverRegistry, RepositoryRegistry

# --- Tạm thời import trực tiếp, sau sẽ thay bằng cơ chế import động ---
from ..drivers.redis.driver import RedisDriver
from ..drivers.sqlite.driver import SQLiteDriver
from ..repositories.users import UserRepository
from ..repositories.api_keys import APIKeyRepository
from ..repositories.sessions import SessionRepository
from ..repositories.organizations import OrganizationRepository
from ..repositories.applications import ApplicationRepository
from ..repositories.members import MemberRepository
from ..repositories.oauth_accounts import OAuthAccountRepository
from ..drivers.chroma.driver import ChromaVectorDriver
from ..services.embedding_service import EmbeddingService
from ..repositories.conversations import ConversationRepository


logger = structlog.get_logger(__name__)

class StorageEngine:
    """
    Điểm truy cập chính, điều phối toàn bộ Storage Framework.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
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
        logger.info("Initializing repositories...")
        
        # Lấy các driver đã được đăng ký
        sqlite_driver = self.drivers.get("sqlite")
        redis_driver = self.drivers.get("redis")
        
        # Khởi tạo và đăng ký các repository phụ thuộc vào SQLite
        if sqlite_driver:
            self.repositories.register("users", UserRepository(db_driver=sqlite_driver))
            self.repositories.register("organizations", OrganizationRepository(db_driver=sqlite_driver))
            self.repositories.register("applications", ApplicationRepository(db_driver=sqlite_driver))
            self.repositories.register("members", MemberRepository(db_driver=sqlite_driver))
            self.repositories.register("api_keys", APIKeyRepository(db_driver=sqlite_driver))
            self.repositories.register("oauth_accounts", OAuthAccountRepository(db_driver=sqlite_driver))
            self.repositories.register("conversations", ConversationRepository(db_driver=sqlite_driver))
        else:
            logger.warning(
                "SQLite driver not found, SQL-dependent repositories will not be available."
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
            embedding_service = EmbeddingService(self.config.semantic_cache.model_dump())
            self.services["embedding_service"] = embedding_service
            
            from ..services.semantic_cache_service import SemanticCacheService
            semantic_cache_service = SemanticCacheService(vector_driver=chroma_driver, embedding_service=embedding_service)
            self.services["semantic_cache"] = semantic_cache_service
            logger.info("Semantic Cache service initialized.")
            
            