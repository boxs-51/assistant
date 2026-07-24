from typing import Optional, Any

TOOL_METADATA = {
    "name": "update_topic_context",
    "description": "Cập nhật hoặc điều chỉnh chủ đề, mục tiêu chính (Topic Context) của phiên làm việc hiện tại.",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Chủ đề hoặc tiêu đề ngắn gọn của tác vụ hiện tại (e.g., 'Refactor module Gateway Client')."
            },
            "goal": {
                "type": "string",
                "description": "Mục tiêu cụ thể cần đạt được trong session này."
            }
        },
        "required": ["topic"]
    }
}

def run(topic: str, goal: Optional[str] = None, context_session: Optional[Any] = None) -> str:
    if context_session and hasattr(context_session, "scratchpad"):
        # Cập nhật thông tin Topic vào Scratchpad
        context_session.scratchpad["current_topic"] = topic
        if goal:
            context_session.scratchpad["current_goal"] = goal
        return f"🎯 **Topic Context updated**: {topic}" + (f" | Goal: {goal}" if goal else "")
    
    return "⚠️ Không tìm thấy context session để cập nhật Topic."