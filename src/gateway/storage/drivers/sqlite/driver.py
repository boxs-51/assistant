import structlog
from typing import AsyncGenerator, Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from contextlib import asynccontextmanager
from ...interfaces.database import DatabaseDriver

logger = structlog.get_logger(__name__)

class SQLiteDriver(DatabaseDriver):
    """Implementation của DatabaseDriver cho SQLite."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        db_path = config.get("path", "gateway_storage.db")
        # Thêm `check_same_thread=False` cho SQLite khi dùng với asyncio
        self.db_url = f"sqlite+aiosqlite:///{db_path}"
        self._engine = create_async_engine(
            self.db_url,
            connect_args={"check_same_thread": False}
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession
        )

    async def connect(self):
        """Kiểm tra kết nối bằng cách thực hiện một truy vấn đơn giản."""
        logger.info("Connecting to SQLite database...", url=self.db_url)
        try:
            async with self._engine.connect() as conn:
                await conn.run_sync(lambda sync_conn: sync_conn.scalar(text("SELECT 1")))
            logger.info("SQLite connection successful.")
        except Exception as e:
            logger.error("Failed to connect to SQLite", error=str(e))
            raise

    async def disconnect(self):
        """Đóng engine của SQLAlchemy."""
        await self._engine.dispose()
        logger.info("SQLite connection pool disposed.")
    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Cung cấp một session bất đồng bộ."""
        async with self._session_factory() as session:
            yield session
