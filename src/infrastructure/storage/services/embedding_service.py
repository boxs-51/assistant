import asyncio
from typing import List, Union
from functools import lru_cache
import os
from opentelemetry import trace
from sentence_transformers import SentenceTransformer
import structlog

tracer = trace.get_tracer(__name__)
logger = structlog.get_logger(__name__)
class EmbeddingService:
    """
    Dịch vụ Singleton để tạo vector embedding.
    - Model được load một lần khi khởi tạo.
    - Chạy tác vụ encode trên một luồng riêng để không block event loop.
    """
    def __init__(self, config: dict):
        self.config = config
        self._model = None
        try:
            with tracer.start_as_current_span("load_embedding_model"):
                model_name = self.config.get("embedding_model", "all-MiniLM-L6-v2")
                device = self.config.get("embedding_device", "cpu")
                cache_folder = self.config.get("embedding_cache_folder", "./embedding_models")
                os.makedirs(cache_folder, exist_ok=True)
                logger.info("Loading embedding model...", model_name=model_name, device=device, cache_folder=cache_folder)
                self._model = SentenceTransformer(
                    model_name,
                    device=device,
                    cache_folder=cache_folder,
                ).eval() # Chuyển sang chế độ inference
                logger.info("Embedding model loaded successfully.")
        except Exception as e:
            logger.critical("Failed to load embedding model. Semantic cache will be disabled.", error=str(e))
            self._model = None # Vô hiệu hóa service nếu model lỗi

    async def encode(self, texts: Union[str, List[str]]) -> Union[list[float], list[list[float]]]:
        """
        Tạo embedding cho một hoặc nhiều chuỗi văn bản một cách bất đồng bộ.
        Sử dụng L1 in-memory cache để tránh tính toán lại embedding cho các text giống nhau.
        """
        if not self._model:
            raise RuntimeError("Embedding model is not available.")

        with tracer.start_as_current_span("create_embedding") as span:
            is_batch = isinstance(texts, list)
            span.set_attribute("num_texts", len(texts) if is_batch else 1)

            # Bọc hàm encode đồng bộ bằng lru_cache để có L1 cache
            # maxsize có thể được đưa ra file config
            @lru_cache(maxsize=1024)
            def _cached_encode(text_to_encode):
                return self._model.encode(text_to_encode, normalize_embeddings=True)

            # Chạy hàm encode đồng bộ trong một luồng riêng
            embeddings = await asyncio.to_thread(
                _cached_encode, tuple(texts) if is_batch else texts
            )
            
            return embeddings.tolist() if hasattr(embeddings, 'tolist') else embeddings
