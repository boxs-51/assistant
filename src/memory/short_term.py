import tiktoken

class ShortTermMemory:
    def __init__(self, max_tokens=3000, model_encoding="cl100k_base"):
        # Lưu trữ theo session_id: { session_id: [{"role": "user", "content": "..."}, ...] }
        self.sessions = {}
        self.max_tokens = max_tokens
        
        # Khởi tạo bộ đếm token (cl100k_base là chuẩn cho GPT-3.5/GPT-4/Ollama chung)
        try:
            self.encoding = tiktoken.get_encoding(model_encoding)
        except Exception:
            # Fallback nếu tên encoding không hợp lệ
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Đếm số lượng token của một chuỗi văn bản"""
        if not text:
            return 0
        return len(self.encoding.encode(text))

    def get_context(self, session_id: str) -> list:
        """Lấy lịch sử chat của session hiện tại"""
        return self.sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str):
        """Thêm tin nhắn mới vào lịch sử và cắt tỉa theo Token"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        
        # Thêm tin nhắn mới vào cuối
        self.sessions[session_id].append({"role": role, "content": content})
        
        # Gọi hàm cắt tỉa
        self._prune_by_tokens(session_id)

    def _prune_by_tokens(self, session_id: str):
        """
        Cắt bỏ các tin nhắn cũ nhất nếu tổng số token vượt quá max_tokens.
        Ưu tiên giữ lại tin nhắn mới nhất để LLM không bị mất bối cảnh gần.
        """
        history = self.sessions[session_id]
        
        # Tính tổng số token của toàn bộ lịch sử hiện tại
        total_tokens = sum(self.count_tokens(msg["content"]) for msg in history)
        
        # Vòng lặp cắt tỉa: Nếu vượt ngưỡng và lịch sử có nhiều hơn 1 tin nhắn
        while total_tokens > self.max_tokens and len(history) > 1:
            # Xóa tin nhắn cũ nhất (index 0)
            removed_msg = history.pop(0)
            
            # Trừ đi số token của tin nhắn vừa xóa để khỏi phải tính lại từ đầu
            total_tokens -= self.count_tokens(removed_msg["content"])
            
        self.sessions[session_id] = history