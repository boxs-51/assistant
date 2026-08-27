from dataclasses import dataclass
from typing import Dict, Optional, Any

import structlog

logger = structlog.get_logger(__name__)


class BaseDriver:
    """
    Base lifecycle interface cho các storage driver.

    Các driver cụ thể có thể implement thêm interface riêng,
    ví dụ CacheDriver, VectorDriver, DatabaseDriver...
    """

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError


class BaseRepository:
    """Base class cho các Repository."""

    def __init__(self, **kwargs):
        pass


@dataclass
class DriverEntry:
    """
    Metadata của một driver đã đăng ký.
    """

    driver: Any
    required: bool = True
    connected: bool = False
    available: bool = False
    error: Optional[Exception] = None


class DriverRegistry:
    """
    Quản lý driver và lifecycle của Storage Framework.

    Registry phân biệt:

    - required driver:
        connect fail -> startup fail

    - optional driver:
        connect fail -> driver unavailable,
        nhưng storage engine vẫn tiếp tục startup.
    """

    def __init__(self):
        self._drivers: Dict[str, DriverEntry] = {}

    def register(
        self,
        name: str,
        driver_instance: Any,
        *,
        required: bool = True,
    ) -> None:
        """
        Đăng ký một driver instance.

        Args:
            name:
                Tên driver.

            driver_instance:
                Instance driver.

            required:
                Nếu True, driver failure sẽ làm startup fail.
                Nếu False, driver failure chỉ làm driver unavailable.
        """

        if name in self._drivers:
            logger.warning(
                "Driver already registered, overwriting",
                driver_name=name,
            )

        self._drivers[name] = DriverEntry(
            driver=driver_instance,
            required=required,
        )

        logger.info(
            "Driver registered successfully",
            driver_name=name,
            required=required,
        )

    def get(self, name: str) -> Optional[Any]:
        """
        Lấy driver đã đăng ký.

        Lưu ý:
        get() chỉ kiểm tra driver có được đăng ký hay không.
        Nó không đảm bảo driver đang connected.

        Dùng is_available() nếu cần kiểm tra runtime availability.
        """

        entry = self._drivers.get(name)

        if entry is None:
            return None

        return entry.driver

    def get_entry(self, name: str) -> Optional[DriverEntry]:
        """
        Trả về metadata của driver.
        """

        return self._drivers.get(name)

    def is_available(self, name: str) -> bool:
        """
        Kiểm tra driver hiện đang available.
        """

        entry = self._drivers.get(name)

        if entry is None:
            return False

        return entry.available

    def is_required(self, name: str) -> bool:
        """
        Kiểm tra driver có phải required driver hay không.
        """

        entry = self._drivers.get(name)

        if entry is None:
            return False

        return entry.required

    def status(self, name: str) -> Optional[dict]:
        """
        Trả về runtime status của một driver.
        """

        entry = self._drivers.get(name)

        if entry is None:
            return None

        return {
            "registered": True,
            "required": entry.required,
            "connected": entry.connected,
            "available": entry.available,
            "error": str(entry.error) if entry.error else None,
        }

    def statuses(self) -> Dict[str, dict]:
        """
        Trả về status của toàn bộ driver.
        """

        return {
            name: {
                "required": entry.required,
                "connected": entry.connected,
                "available": entry.available,
                "error": (
                    str(entry.error)
                    if entry.error
                    else None
                ),
            }
            for name, entry in self._drivers.items()
        }

    async def connect_all(self) -> None:
        """
        Connect toàn bộ driver.

        Behavior:

        required driver fail:
            -> rollback các driver đã connect
            -> raise

        optional driver fail:
            -> đánh dấu unavailable
            -> tiếp tục

        Nếu tất cả required drivers thành công,
        method hoàn thành bình thường.
        """

        logger.info("Connecting all registered drivers...")

        connected = []

        for name, entry in self._drivers.items():
            driver = entry.driver

            try:
                logger.debug(
                    "Connecting driver",
                    driver_name=name,
                    required=entry.required,
                )

                await driver.connect()

                entry.connected = True
                entry.available = True
                entry.error = None

                connected.append((name, entry))

                logger.info(
                    "Driver connected",
                    driver_name=name,
                )

            except Exception as exc:
                entry.connected = False
                entry.available = False
                entry.error = exc

                if entry.required:
                    logger.error(
                        "Required driver failed to connect",
                        driver_name=name,
                        error=str(exc),
                        exc_info=True,
                    )

                    await self._rollback_connected(connected)

                    raise

                logger.warning(
                    "Optional driver failed to connect; "
                    "continuing startup",
                    driver_name=name,
                    error=str(exc),
                    exc_info=True,
                )

                # Một driver có thể đã allocate resource
                # trước khi connect() ném exception.
                #
                # Best-effort cleanup.
                try:
                    await driver.disconnect()
                except Exception:
                    logger.warning(
                        "Failed to cleanup optional driver "
                        "after connection failure",
                        driver_name=name,
                        exc_info=True,
                    )

        logger.info(
            "Driver connection phase completed",
            statuses=self.statuses(),
        )

    async def _rollback_connected(self, connected) -> None:
        """
        Rollback các driver đã connect thành công.

        Disconnect theo reverse registration order.
        """

        logger.error(
            "Rolling back connected drivers"
        )

        for name, entry in reversed(connected):
            driver = entry.driver

            try:
                await driver.disconnect()

                entry.connected = False
                entry.available = False

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

                entry.connected = False
                entry.available = False

    async def disconnect_all(self) -> None:
        """
        Ngắt toàn bộ driver.

        Một driver lỗi không được ngăn các driver còn lại shutdown.
        """

        logger.info(
            "Disconnecting all registered drivers..."
        )

        for name, entry in reversed(
            list(self._drivers.items())
        ):
            driver = entry.driver

            # Không cần disconnect driver chưa từng
            # connect thành công.
            if not entry.connected:
                continue

            try:
                await driver.disconnect()

                entry.connected = False
                entry.available = False

                logger.debug(
                    "Driver disconnected",
                    driver_name=name,
                )

            except Exception:
                logger.error(
                    "Failed to disconnect driver",
                    driver_name=name,
                    exc_info=True,
                )

                entry.connected = False
                entry.available = False

        logger.info(
            "All drivers disconnected"
        )


class RepositoryRegistry:
    """
    Quản lý việc đăng ký và truy cập các Repository.
    """

    def __init__(self):
        self._repositories: Dict[str, BaseRepository] = {}

    def register(
        self,
        name: str,
        repository_instance: BaseRepository,
    ) -> None:
        if name in self._repositories:
            logger.warning(
                "Repository already registered, overwriting",
                repo_name=name,
            )

        self._repositories[name] = repository_instance

        logger.info(
            "Repository registered successfully",
            repo_name=name,
        )

    def get(
        self,
        name: str,
    ) -> Optional[BaseRepository]:

        return self._repositories.get(name)