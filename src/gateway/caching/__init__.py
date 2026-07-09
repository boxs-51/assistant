import hashlib
import time
from typing import Optional, Tuple, List

import structlog
from opentelemetry import trace

from ..config import settings
from ..middleware import observability as gateway_metrics
from .base import BaseCacheBackend
from .embedding import EmbeddingService
from .models import CacheEntry, CacheMetadata

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class SemanticCache:
    """
    Lớp điều phối chính cho Semantic Cache.
    - Sử dụng EmbeddingService để tạo vector.
    - Sử dụng một CacheBackend (ví dụ: Chroma) để lưu trữ và truy vấn.
    - Chịu trách nhiệm về business logic: kiểm tra ngưỡng, TTL, cập nhật metadata.
    """

    def __init__(self, backend: BaseCacheBackend, embedding_service: EmbeddingService):
        self.backend = backend
        self.embedding_service = embedding_service
        logger.info("Semantic Cache coordinator is ready.", backend=backend.__class__.__name__)

    def _get_prompt_id(self, prompt: str) -> str:
        """Tạo một ID duy nhất và ổn định cho một prompt bằng thuật toán băm."""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    async def get(self, prompt: str) -> Optional[Tuple[str, list[float]]]:
        """
        Tìm kiếm một prompt tương đồng ngữ nghĩa trong cache.
        Trả về (câu trả lời, embedding) nếu tìm thấy, để tái sử dụng embedding.
        """
        start_time = time.time()
        try:
            with tracer.start_as_current_span("semantic_cache.get") as span: # type: ignore
                miss_reason_val = "unknown"
                try:
                    if not prompt:
                        return None
    
                    # 1. Tạo embedding cho prompt (chỉ một lần)
                    embedding = await self.embedding_service.encode(prompt)
                    span.set_attribute("prompt_hash", self._get_prompt_id(prompt))
    
                    # 2. Truy vấn backend
                    entry, distance, miss_reason = await self.backend.get(embedding)
                    miss_reason_val = miss_reason or "unknown"
    
                    if entry and distance is not None:
                        # Cache Hit
                        span.set_attribute("cache_hit", True)
                        span.set_attribute("cache_distance", distance)
                        logger.info("Cache hit", distance=round(distance, 4))
                        gateway_metrics.metrics.increment_cache_hits()
                        return entry.response, embedding
    
                except Exception as e:
                    logger.error("Semantic cache GET operation failed", error=str(e), exc_info=True)
                    span.record_exception(e)
                    miss_reason_val = "backend_error"
                    # Fail Open
    
                # Logic chỉ được thực thi khi cache miss hoặc có lỗi
                span.set_attribute("cache_hit", False)
                span.set_attribute("cache_miss_reason", miss_reason_val)
                return None
        finally:
            gateway_metrics.metrics.record_semantic_cache_latency("get", time.time() - start_time)

    async def set(self, prompt: str, response: str, embedding: list[float]):
        """Lưu một cặp prompt-response mới vào cache, sử dụng embedding đã có."""
        start_time = time.time()
        with tracer.start_as_current_span("semantic_cache.set"):
            entry_id = self._get_prompt_id(prompt)
            metadata = CacheMetadata(prompt=prompt)
            entry = CacheEntry(id=entry_id, response=response, embedding=embedding, metadata=metadata)

            await self.backend.set(entry)
            gateway_metrics.metrics.increment_cache_write()
            gateway_metrics.metrics.record_semantic_cache_latency("set", time.time() - start_time)
            logger.debug("Cache updated", prompt_id=entry_id)

    async def batch_set(self, items: List[Tuple[str, str, list[float]]]):
        """Lưu một loạt các mục vào cache."""
        entries = []
        for prompt, response, embedding in items:
            entry_id = self._get_prompt_id(prompt)
            metadata = CacheMetadata(prompt=prompt)
            entries.append(CacheEntry(id=entry_id, response=response, embedding=embedding, metadata=metadata))
        if entries:
            await self.backend.batch_set(entries)
            gateway_metrics.metrics.increment_cache_write(amount=len(entries))