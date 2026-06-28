import os
import chromadb
from sentence_transformers import SentenceTransformer

class LongTermMemory:
    def __init__(self, storage_path="memory/chroma_db"):
        self.storage_path = storage_path
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        
        # 1. Khởi tạo mô hình embedding (chạy local)
        # 'all-MiniLM-L6-v2' là một mô hình nhẹ và hiệu quả cho nhiều tác vụ.py 
        print("🧠 [LongTermMemory] Đang tải mô hình embedding...")
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        print("✅ [LongTermMemory] Tải mô hình embedding thành công.")

        # 2. Khởi tạo ChromaDB client và collection
        # PersistentClient sẽ lưu dữ liệu vào ổ đĩa
        self.client = chromadb.PersistentClient(path=self.storage_path)
        self.collection = self.client.get_or_create_collection(
            name="past_facts",
            metadata={"hnsw:space": "cosine"} # Sử dụng khoảng cách cosine để đo độ tương đồng
        )

    def search_relevant_facts(self, query: str) -> str:
        """
        Nâng cấp: Tìm kiếm thông tin bằng Vector Search (tìm kiếm ngữ nghĩa).
        """
        if not query:
            return "Không có ký ức cũ liên quan."

        # 1. Tạo vector embedding cho câu truy vấn
        query_embedding = self.embedding_model.encode(query).tolist()

        # 2. Truy vấn ChromaDB để tìm 3 ký ức gần nhất
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=3
        )

        relevant_info = []
        # `results['documents'][0]` chứa danh sách các văn bản tìm được
        for doc in results['documents'][0]:
            relevant_info.append(f"- {doc}")

        return "\n".join(relevant_info) if relevant_info else "Không có ký ức cũ liên quan."

    def learn_new_fact(self, key: str, value: str):
        """
        Hàm giúp Agent tự ghi nhớ kiến thức mới bằng cách chuyển nó thành vector.
        """
        # 1. Tạo một văn bản duy nhất để embedding
        document = f"{key}: {value}"
        
        # 2. Tạo embedding cho văn bản
        embedding = self.embedding_model.encode(document).tolist()
        
        # 3. Lưu vào ChromaDB. ID là duy nhất, ta có thể dùng chính `key` làm ID.
        # Nếu ID đã tồn tại, ChromaDB sẽ tự động cập nhật (upsert).
        self.collection.upsert(
            ids=[key],
            embeddings=[embedding],
            documents=[document]
        )
        print(f"📝 [LongTermMemory] Đã học và ghi nhớ sự thật mới: '{key}'")