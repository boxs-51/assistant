class ShortTermMemory:
    def __init__(self, max_history=10):
        # Lưu trữ theo session_id. Định dạng: { session_id: [{"role": "user", "content": "..."}, ...] }
        self.sessions = {}
        self.max_history = max_history

    def get_context(self, session_id: str) -> list:
        """Lấy lịch sử chat của session hiện tại dưới dạng danh sách tin nhắn"""
        return self.sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str):
        """Thêm tin nhắn mới vào lịch sử và cắt tỉa nếu quá dài"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        
        self.sessions[session_id].append({"role": role, "content": content})
        
        # Cắt tỉa lịch sử để tránh tràn Context Window của LLM
        if len(self.sessions[session_id]) > self.max_history:
            self.sessions[session_id] = self.sessions[session_id][-self.max_history:]