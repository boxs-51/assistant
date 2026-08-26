import structlog
from typing import Dict, Any, Type, Optional

# Các lớp này là placeholder và sẽ được định nghĩa đầy đủ trong các file interfaces
class BaseDriver:
    """Lớp cha trừu tượng cho tất cả các Driver."""
    async def connect(self):
        pass
    async def disconnect(self):
        pass

class BaseRepository:
    """Lớp cha trừu tượng cho tất cả các Repository."""
    def __init__(self, **kwargs):
        pass

logger = structlog.get_logger(__name__)

class DriverRegistry:
    """
    Quản lý vòng đời của các driver lưu trữ (database, cache, vector db...).
    """
    def __init__(self):
        self._drivers: Dict[str, BaseDriver] = {}

    def register(self, name: str, driver_instance: BaseDriver):
        """Đăng ký một instance của driver đã được khởi tạo."""
        if name in self._drivers:
            logger.warning("Driver already registered, overwriting.", driver_name=name)
        self._drivers[name] = driver_instance
        logger.info("Driver registered successfully", driver_name=name)

    def get(self, name: str) -> Optional[BaseDriver]:
        """Lấy một driver đã đăng ký."""
        driver = self._drivers.get(name)
        if not driver:
            logger.error("Attempted to access an unregistered driver", driver_name=name)
        return driver

    async def connect_all(self):
        """Kết nối tất cả các driver đã đăng ký."""
        logger.info("Connecting all registered drivers...")

        connected = []

        try:
            for name, driver in self._drivers.items():
                await driver.connect()
                connected.append((name, driver))
                logger.debug("Driver connected", driver_name=name)
        except Exception:
            logger.error(
                "Failed to connect storage drivers. "
                "Rolling back already connected drivers.",
                exc_info=True,
            )

            # Roll back theo reverse order để tránh để lại resource mở.
            for name, driver in reversed(connected):
                try:
                    await driver.disconnect()
                    logger.debug(
                        "Rolled back driver connection",
                        driver_name=name,
                    )
                except Exception:
                    logger.error(
                        "Failed to rollback driver connection",
                        driver_name=name,
                        exc_info=True,
                    )

            raise

        logger.info("All drivers connected.")

    async def disconnect_all(self):
        """Ngắt kết nối tất cả các driver."""
        logger.info("Disconnecting all registered drivers...")

        # Disconnect reverse registration order để phù hợp với lifecycle dependency.
        for name, driver in reversed(list(self._drivers.items())):
            try:
                await driver.disconnect()
                logger.debug("Driver disconnected", driver_name=name)
            except Exception:
                # Một driver lỗi không được ngăn driver còn lại shutdown.
                logger.error(
                    "Failed to disconnect driver",
                    driver_name=name,
                    exc_info=True,
                )

        logger.info("All drivers disconnected.")

class RepositoryRegistry:
    """
    Quản lý việc đăng ký và truy cập các Repository.
    """
    def __init__(self):
        self._repositories: Dict[str, BaseRepository] = {}

    def register(self, name: str, repository_instance: BaseRepository):
        """Đăng ký một instance của repository đã được khởi tạo."""
        if name in self._repositories:
            logger.warning("Repository already registered, overwriting.", repo_name=name)
        self._repositories[name] = repository_instance
        logger.info("Repository registered successfully", repo_name=name)

    def get(self, name: str) -> Optional[BaseRepository]:
        """Lấy một repository đã đăng ký."""
        repo = self._repositories.get(name)
        if not repo:
            logger.error("Attempted to access an unregistered repository", repo_name=name)
        return repo