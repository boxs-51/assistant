import os
from typing import Dict, Any, Optional

from .config_loader import ConfigLoader
from .local_tools import LocalToolManager
from .skills_loader import SkillManager
from ..mcp_client.mcp_adapter import MCPManager


class DynamicRegistry:
    INTERNAL_REQUIRED_TOOLS = {"read_file", "get_sys_info", "list_directory"}

    def __init__(self, tools_dir="tools", skills_dir="skills", config_dir="config"):
        base_dir = os.getcwd()
        self.config_loader = ConfigLoader(os.path.abspath(os.path.join(base_dir, config_dir)))
        self.local_tool_mgr = LocalToolManager(
            os.path.abspath(os.path.join(base_dir, tools_dir)), 
            self.INTERNAL_REQUIRED_TOOLS
        )
        self.skill_mgr = SkillManager(os.path.abspath(os.path.join(base_dir, skills_dir)))
        self.mcp_mgr = MCPManager()

        self.tools: Dict[str, Dict[str, Any]] = {}
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, Any] = {}
        self.constitution: str = ""

    def load_all(self):
        """Khởi chạy điều phối nạp dữ liệu từ các sub-modules"""
        # 1. Config & Constitution
        self.settings = self.config_loader.load_settings()
        self.constitution = self.config_loader.load_constitution()

        # 2. Local Python Tools
        tools_cfg = self.settings.get("tools_config", {})
        self.tools = self.local_tool_mgr.load_tools(tools_cfg)

        # 3. External MCP Servers
        mcp_cfg = self.settings.get("mcp_servers", {})
        mcp_tools = self.mcp_mgr.load_mcp_servers(mcp_cfg)
        self.tools.update(mcp_tools)

        # 4. Skills
        self.skills = self.skill_mgr.load_skills()

    def execute_slash_command(self, cmd: str) -> str:
        cmd = cmd.strip()
        if cmd == "/init":
            self.load_all()
            return "🔄 **System Reloaded**: Đã tải lại toàn bộ Modules."
        
        elif cmd == "/tools":
            res = "### 🛠️ Loaded Tools (Local & MCP)\n"
            for k, v in self.tools.items():
                meta = v["metadata"]
                risk = meta.get("base_risk", "LOW")
                desc = meta.get("description", "")
                tag = " `[CORE]`" if v.get("is_internal") else (" `[MCP]`" if v.get("is_mcp") else "")
                res += f"- **`{k}`**{tag} (Risk: `{risk}`): {desc}\n"
            return res
        
        elif cmd == "/skills":
            res = "### 📚 Discovered Skill Workflows\n"
            for k, v in self.skills.items():
                state = "loaded" if v.get("loaded") else "available"
                res += f"- **`{k}`** (Risk: `{v['base_risk']}`, State: `{state}`)\n"
            return res
            
        elif cmd == "/context":
            return f"### 🧠 Active Constitution Snapshot\n```markdown\n{self.constitution[:400]}...\n```"
            
        return "❌ Command không hợp lệ."

    def get_tool(self, tool_name: str) -> Optional[Dict[str, Any]]:
        if tool_name in self.tools:
            return self.tools[tool_name]
        
        # Tra cứu khớp đuôi MCP Tool (ví dụ 'list_directory' -> 'mcp__filesystem__list_directory')
        for registered_name, tool_data in self.tools.items():
            if registered_name.endswith(f"__{tool_name}"):
                return tool_data
                
        return None

    def get_skill(self, skill_name: str, *, load: bool = False) -> Optional[Dict[str, Any]]:
        skill = self.skill_mgr.get_skill(skill_name, load=load)
        if skill is not None:
            self.skills[skill_name] = skill
        return skill

    def activate_skill(self, skill_name: str) -> Dict[str, Any]:
        skill = self.skill_mgr.load_skill(skill_name)
        self.skills[skill_name] = skill
        return skill

    def deactivate_skill(self, skill_name: str) -> bool:
        unloaded = self.skill_mgr.unload_skill(skill_name)
        if unloaded:
            skill = self.skill_mgr.get_skill(skill_name)
            if skill is not None:
                self.skills[skill_name] = skill
        return unloaded
