from src.memory.short_term import ShortTermMemory
from src.memory.long_term import LongTermMemory

class ContextEngine:
    def __init__(self, config):
        self.short_mem = ShortTermMemory(max_history=config["memory_settings"]["short_term"]["max_history"])
        self.long_mem = LongTermMemory(storage_path=config["memory_settings"]["long_term"]["storage_path"])

    def build_rich_context(self, session_id: str, user_request: str) -> dict:
        """Tự động thu thập mọi ký ức và tối ưu hóa để tạo ra Context hoàn chỉnh"""
        chat_history = self.short_mem.get_context(session_id)
        past_facts = self.long_mem.search_relevant_facts(user_request)
        
        # Ở đây có thể viết thêm logic đếm Token, nếu dài quá thì tự drop bớt chat_history cũ
        
        return {
            "chat_history": chat_history,
            "past_facts": past_facts,
            "full_prompt_context": f"[KÝ ỨC QUÁ KHỨ]:\n{past_facts}\n\n[LỊCH SỬ CHAT]:\n{chat_history}"
        }

    def update_context(self, session_id: str, user_request: str, ai_response: str):
        """Cập nhật lại ký ức sau lượt chạy"""
        self.short_mem.add_message(session_id, "user", user_request)
        self.short_mem.add_message(session_id, "assistant", ai_response)