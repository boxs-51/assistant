from tools.registry import ToolRegistry
from skills.registry import SkillRegistry

class CapabilityManager:
    """Hệ thống lõi quản lý cả Tools và Skills"""
    def __init__(self):
        # Nâng cấp: Tự động phát hiện và đăng ký các năng lực
        print("🚀 [CapabilityManager] Đang tự động tải các năng lực...")
        self.tools = ToolRegistry()
        self.tools.discover_and_register(directory="src/tools")
        
        self.skills = SkillRegistry()
        self.skills.discover_and_register(directory="src/skills")
        print("✅ [CapabilityManager] Tải thành công các Tools và Skills.")

    def get_available_capabilities(self) -> str:
        """Cung cấp cho AI bức tranh toàn cảnh về những gì nó có thể làm"""
        return f"""
[CÔNG CỤ TƯƠNG TÁC THỰC TẾ (TOOLS)]:
{self.tools.get_local_tools_prompt()}

[KỸ NĂNG TƯ DUY NỘI BỘ (SKILLS)]:
{self.skills.get_skills_manifest()}
"""