import json
import os

class LongTermMemory:
    def __init__(self, storage_path="config/long_term_memory.json"):
        self.storage_path = storage_path
        self.memory_data = self._load_memory()

    def _load_memory(self) -> dict:
        if os.path.exists(self.storage_path):
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"user_profile": {}, "learned_rules": [], "past_facts": {}}

    def save_memory(self):
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self.memory_data, f, ensure_ascii=False, indent=4)

    def search_relevant_facts(self, query: str) -> str:
        """
        Tìm kiếm thông tin liên quan từ quá khứ dựa trên từ khóa.
        (Sau này có thể nâng cấp thành Vector Search / RAG tại đây)
        """
        relevant_info = []
        query_lower = query.lower()
        
        # Thử quét qua các sự kiện/tri thức cũ đã lưu
        for key, value in self.memory_data.get("past_facts", {}).items():
            if key.lower() in query_lower:
                relevant_info.append(f"- {key}: {value}")
                
        return "\n".join(relevant_info) if relevant_info else "Không có ký ức cũ liên quan."

    def learn_new_fact(self, key: str, value: str):
        """Hàm giúp Agent tự ghi nhớ thêm kiến thức mới sau khi xử lý xong task"""
        self.memory_data["past_facts"][key] = value
        self.save_memory()