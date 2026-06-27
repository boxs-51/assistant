import chromadb
from sentence_transformers import SentenceTransformer
import hashlib
from typing import Optional

class SemanticCache:
    """
    Lớp quản lý Semantic Cache, sử dụng SentenceTransformer để tạo embedding
    và ChromaDB để lưu trữ và truy vấn vector.
    """
    def __init__(self,
                 collection_name: str = "semantic_cache",
                 model_name: str = 'all-MiniLM-L6-v2',
                 threshold: float = 0.05): # Ngưỡng khoảng cách Cosine, càng nhỏ càng tương đồng
        print("🧠 [SemanticCache] Đang khởi tạo Semantic Cache...")
        
        # 1. Tải mô hình embedding. Lần đầu chạy sẽ mất chút thời gian để tải về.
        self.model = SentenceTransformer(model_name)
        
        # 2. Khởi tạo ChromaDB client. Ở đây dùng client tạm thời trong bộ nhớ.
        # Để lưu trữ bền vững, hãy dùng: chromadb.PersistentClient(path="/path/to/db")
        self.client = chromadb.Client()
        
        # 3. Lấy hoặc tạo một "collection" (tương đương một bảng)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"} # Chỉ định thuật toán đo lường là cosine
        )
        
        # 4. Thiết lập ngưỡng tương đồng.
        # Với cosine distance, giá trị từ 0 đến 2. 0 là giống hệt.
        # Ngưỡng 0.05 tương đương với độ tương đồng rất cao.
        self.threshold = threshold
        print("✅ [SemanticCache] Semantic Cache đã sẵn sàng.")

    def _get_prompt_id(self, prompt: str) -> str:
        """Tạo một ID duy nhất và ổn định cho một prompt bằng thuật toán băm."""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    async def get(self, prompt: str) -> Optional[str]:
        """
        Tìm kiếm một prompt tương đồng ngữ nghĩa trong cache.
        Trả về câu trả lời đã cache nếu tìm thấy và độ tương đồng đủ cao.
        """
        if not prompt:
            return None

        # 1. Tạo vector embedding cho câu truy vấn
        query_embedding = self.model.encode(prompt).tolist()

        # 2. Truy vấn ChromaDB để tìm 1 kết quả gần nhất
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=1
        )

        # 3. Kiểm tra kết quả và ngưỡng tương đồng
        if results and results['ids'][0]:
            distance = results['distances'][0][0]
            if distance <= self.threshold:
                print(f"✅ [SemanticCache] Cache Hit! (Distance: {distance:.4f})")
                # `documents` chứa câu trả lời đã được cache
                return results['documents'][0][0]

        print("⚪ [SemanticCache] Cache Miss.")
        return None

    async def set(self, prompt: str, response: str):
        """Lưu một cặp prompt-response mới vào cache."""
        prompt_id = self._get_prompt_id(prompt)
        prompt_embedding = self.model.encode(prompt).tolist()

        # Dùng upsert để thêm mới hoặc cập nhật nếu ID đã tồn tại
        self.collection.upsert(
            ids=[prompt_id],
            embeddings=[prompt_embedding],
            documents=[response],
            metadatas=[{"prompt": prompt}] # Lưu lại prompt gốc trong metadata để debug
        )
        print(f"📝 [SemanticCache] Đã cập nhật cache cho prompt ID: {prompt_id[:10]}...")