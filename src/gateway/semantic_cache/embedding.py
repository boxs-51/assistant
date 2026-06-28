import asyncio
from typing import List, Union
from functools import lru_cache

from sentence_transformers import SentenceTransformer
import structlog
from opentelemetry import trace

from ..config import settings

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class EmbeddingService:
    """
    Dịch vụ Singleton để tạo vector embedding.
    - Đảm bảo model chỉ được load một lần.
    - Chạy tác vụ encode trên một luồng riêng để không block event loop.
    """
    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            logger.info("Initializing EmbeddingService singleton...")
            cls._instance = super(EmbeddingService, cls).__new__(cls)
            try:
                with tracer.start_as_current_span("load_embedding_model"):
                    logger.info("Loading embedding model...", model_name=settings.EMBEDDING_MODEL)
                    # .eval() để chuyển sang chế độ inference
                    # Thêm cache_folder để chỉ định nơi lưu model, giúp quản lý dễ dàng hơn
                    cls._model = SentenceTransformer(
                        settings.EMBEDDING_MODEL,
                        device=settings.EMBEDDING_DEVICE,
                        cache_folder=settings.EMBEDDING_CACHE_FOLDER,
                    ).eval() # Chuyển sang chế độ inference
                    logger.info("Embedding model loaded successfully.")
            except Exception as e:
                logger.critical("Failed to load embedding model. Semantic cache will be disabled.", error=str(e))
                cls._model = None # Vô hiệu hóa service nếu model lỗi
        return cls._instance

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