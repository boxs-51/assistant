import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from .message import GatewayMessage

class MemoryQueryResult(BaseModel):
    """Kết quả truy vấn từ các hệ thống Memory nâng cao (VectorDB, Graph, RAG)"""
    memory_id: str
    source_type: str  # "episodic", "semantic", "user_profile", "file_index"
    content: str
    relevance_score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentContextSession(BaseModel):
    """Session quản lý ngữ cảnh toàn diện"""
    
    # 📌 Bổ sung default_factory và default tại đây
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = "guest_user"
    
    # 1. Trí nhớ ngắn hạn (Lịch sử hội thoại hiện tại)
    working_messages: List[GatewayMessage] = Field(default_factory=list)
    
    # 2. Trí nhớ dài hạn được RAG/VectorDB inject vào ngữ cảnh
    retrieved_memories: List[MemoryQueryResult] = Field(default_factory=list)
    
    # 3. Biến môi trường & Trạng thái động (State Manager)
    scratchpad: Dict[str, Any] = Field(default_factory=dict)
    
    # 4. Giới hạn Token & Quản lý Window
    max_tokens: int = 8192
    current_token_count: int = 0

    def add_message(self, message: GatewayMessage):
        self.working_messages.append(message)