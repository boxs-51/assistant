import time
import chromadb
import structlog
import os
from opentelemetry import trace

from ...interfaces.vector import VectorStorageDriver, CacheGetResult
from ...caching.models import CacheEntry, CacheMetadata # Vẫn giữ models ở đây
from ..config import settings

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class ChromaVectorDriver(VectorStorageDriver):
    """
    Triển khai Vector Storage Driver sử dụng ChromaDB.
    Đây là một driver cụ thể của VectorStorageDriver interface.
    """
    def __init__(self, config: dict):
        self.config = config
        chroma_path = self.config.get("path", "./chroma_db")
        logger.info("Initializing ChromaDB driver...", path=chroma_path)
        os.makedirs(os.path.dirname(settings.semantic_cache.path), exist_ok=True)
        self.client = chromadb.PersistentClient(path=chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=self.config.get("collection", "default_collection"),
            metadata={"hnsw:space": "cosine"}
        )
        logger.info("ChromaDB backend is ready.")
        """
        Phương thức này được để trống để tuân thủ interface.
        """
        pass

    async def connect(self):
        """ChromaDB PersistentClient không yêu cầu kết nối tường minh."""
        logger.info("ChromaVectorDriver connected (persistent client).")

    async def disconnect(self):
        """ChromaDB PersistentClient không yêu cầu ngắt kết nối tường minh."""
        logger.info("ChromaVectorDriver disconnected (persistent client).")
