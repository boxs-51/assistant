import asyncio
import os
from typing import List
import time
import chromadb
import structlog
import os
from opentelemetry import trace

from ...interfaces.vector import VectorStorageDriver
from ...models.chroma.base import CacheEntry, CacheMetadata, CacheGetResult# Vẫn giữ models ở đây
from ....config.schemas import DriverConfig

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class ChromaVectorDriver(VectorStorageDriver):
    """
    Triển khai Vector Storage Driver sử dụng ChromaDB.
    Đây là một driver cụ thể của VectorStorageDriver interface.
    """
    def __init__(self, config: DriverConfig):
        self.config = config
        self.threshold = config.options.get("threshold", 0.95)
        self.expire = config.options.get("expire", 3600)

        chroma_path = config.options.get("path", "./chroma_db")
        logger.info("Initializing ChromaDB driver...", path=chroma_path)
        os.makedirs(os.path.dirname(chroma_path), exist_ok=True)
        self.client = chromadb.PersistentClient(path=chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=config.options.get("collection", "default_collection"),
            metadata={"hnsw:space": "cosine"}
        )
        logger.info("ChromaDB backend is ready.")

    async def get(self, embedding: list[float]) -> CacheGetResult:
        """Tìm kiếm trong ChromaDB một cách bất đồng bộ."""
        with tracer.start_as_current_span("chroma_query") as span:
            try:
                # Chạy query đồng bộ trên một luồng khác
                results = await asyncio.to_thread(
                    self.collection.query,
                    query_embeddings=[embedding],
                    n_results=1,
                    include=["metadatas", "documents", "distances"]
                )
                span.set_attribute("num_results", len(results['ids'][0]))

                if results and results['ids'][0]:
                    distance = results['distances'][0][0]
                    span.set_attribute("best_distance", distance)

                    # Chỉ xử lý nếu kết quả trả về nằm trong ngưỡng cho phép
                    if distance > self.threshold:
                        return None, distance, "below_threshold"

                    # Khôi phục CacheEntry từ metadata
                    metadata_dict = results['metadatas'][0][0]
                    
                    # Kiểm tra TTL
                    expires_at = metadata_dict.get("expires_at")
                    if expires_at and time.time() > expires_at:
                        logger.info("Cache hit but expired", entry_id=results['ids'][0][0])
                        # TODO: Xóa entry này trong một background task
                        return None, distance, "expired"

                    # Lấy embedding đã lưu, không dùng embedding của query
                    retrieved_embedding_result = await asyncio.to_thread(
                        self.collection.get, ids=[results['ids'][0][0]], include=["embeddings"]
                    )
                    if not retrieved_embedding_result or not retrieved_embedding_result.get('embeddings'):
                            logger.warning("Could not retrieve embedding for cached entry.", entry_id=results['ids'][0][0])
                            return None, distance, "not_found" # Coi như không tìm thấy
                    retrieved_embedding = retrieved_embedding_result['embeddings'][0]

                    entry = CacheEntry(
                        id=results['ids'][0][0],
                        response=results['documents'][0][0],
                        embedding=retrieved_embedding,
                        metadata=CacheMetadata(**metadata_dict)
                    )
                    return entry, distance, None # Hit
                else:
                    # Không tìm thấy kết quả nào trong DB
                    return None, None, "not_found"

            except Exception as e:
                logger.error("ChromaDB query failed", error=str(e), exc_info=True)
                span.record_exception(e)
                # Fail Open: Trả về None nếu có lỗi
        return None, None, "backend_error"

    async def set(self, entry: CacheEntry):
        """Lưu entry vào ChromaDB một cách bất đồng bộ."""
        with tracer.start_as_current_span("chroma_upsert"):
            try:
                # Cập nhật thời gian hết hạn cho metadata trước khi lưu
                if self.expire > 0:
                    entry.metadata.expires_at = time.time() + self.expire

                await asyncio.to_thread(
                    self.collection.upsert,
                    ids=[entry.id],
                    embeddings=[entry.embedding],
                    documents=[entry.response],
                    metadatas=[entry.to_storage_dict()]
                )
            except Exception as e:
                logger.error("ChromaDB upsert failed", error=str(e), exc_info=True)
                trace.get_current_span().record_exception(e)
                # Fail Open: Chỉ ghi log, không làm sập request

    async def batch_set(self, entries: List[CacheEntry]):
        """Lưu một loạt các entry vào ChromaDB một cách bất đồng bộ."""
        if not entries:
            return
        with tracer.start_as_current_span("chroma_batch_upsert"):
            try:
                for entry in entries:
                    if self.expire > 0:
                        entry.metadata.expires_at = time.time() + self.expire
                
                await asyncio.to_thread(
                    self.collection.upsert,
                    ids=[e.id for e in entries],
                    embeddings=[e.embedding for e in entries],
                    documents=[e.response for e in entries],
                    metadatas=[e.to_storage_dict() for e in entries]
                )
            except Exception as e:
                logger.error("ChromaDB batch upsert failed", error=str(e), exc_info=True)
                trace.get_current_span().record_exception(e)

    async def delete(self, entry_id: str):
        """Xóa một entry khỏi cache dựa trên ID."""
        with tracer.start_as_current_span("chroma_delete"):
            try:
                await asyncio.to_thread(self.collection.delete, ids=[entry_id])
            except Exception as e:
                logger.error("ChromaDB delete failed", error=str(e), exc_info=True)
                trace.get_current_span().record_exception(e)

    async def cleanup(self):
        """Xóa các entry đã hết hạn TTL khỏi collection."""
        with tracer.start_as_current_span("chroma_cleanup") as span:
            try:
                current_time = time.time()
                # Lấy các entry có trường expires_at nhỏ hơn thời gian hiện tại
                expired_entries = await asyncio.to_thread(
                    self.collection.get, where={"expires_at": {"$lt": current_time}}
                )
                if expired_entries and expired_entries['ids']:
                    logger.info(f"Cleaning up {len(expired_entries['ids'])} expired cache entries.")
                    await asyncio.to_thread(self.collection.delete, ids=expired_entries['ids'])
            except Exception as e:
                logger.error("ChromaDB cleanup failed", error=str(e), exc_info=True)
                span.record_exception(e)

    async def health(self) -> bool:
        """Kiểm tra tình trạng của ChromaDB bằng cách gọi heartbeat."""
        try:
            # heartbeat() ném ra exception nếu có lỗi
            await asyncio.to_thread(self.client.heartbeat)
            return True
        except Exception as e:
            logger.error("ChromaDB health check failed", error=str(e))
            return False

    async def close(self):
        """
        ChromaDB PersistentClient không yêu cầu đóng kết nối một cách tường minh.
        Phương thức này được để trống để tuân thủ interface.
        """
        pass

    async def connect(self):
        """ChromaDB PersistentClient không yêu cầu kết nối tường minh."""
        logger.info("ChromaVectorDriver connected (persistent client).")

    async def disconnect(self):
        """ChromaDB PersistentClient không yêu cầu ngắt kết nối tường minh."""
        logger.info("ChromaVectorDriver disconnected (persistent client).")