from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from schemas.message import GatewayMessage

class MemoryQueryResult(BaseModel):
    """Kết quả truy vấn từ các hệ thống Memory nâng cao (VectorDB, Graph, RAG)"""
    memory_id: str
    source_type: str  # "episodic", "semantic", "user_profile", "file_index"
    content: str
    relevance_score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentContextSession(BaseModel):
    """Session quản lý ngữ cảnh toàn diện"""
    session_id: str
    user_id: str
    
    # 1. Trí nhớ ngắn hạn (Lịch sử hội thoại hiện tại)
    working_messages: List[GatewayMessage] = Field(default_factory=list)
    
    # 2. Trí nhớ dài hạn được RAG/VectorDB inject vào ngữ cảnh
    retrieved_memories: List[MemoryQueryResult] = Field(default_factory=list)
    
    # 3. Biến môi trường & Trạng thái động (State Manager)
    # Giúp Tools/Skills có thể đọc/ghi biến dùng chung (Ví dụ: current_working_dir, active_project)
    scratchpad: Dict[str, Any] = Field(default_factory=dict)
    
    # 4. Giới hạn Token & Quản lý Window
    max_tokens: int = 8192
    current_token_count: int = 0

    def add_message(self, message: GatewayMessage):
        self.working_messages.append(message)
        # Nơi xử lý tự động Compress / Truncate Context nếu vượt max_tokens