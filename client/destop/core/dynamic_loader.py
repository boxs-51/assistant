import os
import glob
import json
import asyncio
import importlib.util
import re
from typing import Dict, Any, List

# Cài đặt MCP Python SDK: pip install mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClientAdapter:
    """Quản lý kết nối và ánh xạ Tool từ MCP Server"""
    def __init__(self, server_name: str, server_config: dict):
        self.server_name = server_name
        self.config = server_config
        self.session: ClientSession = None

    async def connect(self):
        env = os.environ.copy()
        if "env" in self.config:
            env.update(self.config["env"])

        server_params = StdioServerParameters(
            command=self.config["command"],
            args=self.config.get("args", []),
            env=env
        )

        read_stream, write_stream = await stdio_client(server_params)
        self.session = ClientSession(read_stream, write_stream)
        await self.session.initialize()

    async def get_mapped_tools(self) -> Dict[str, Dict[str, Any]]:
        mcp_tools = await self.session.list_tools()
        mapped_tools = {}

        for tool in mcp_tools.tools:
            namespaced_name = f"mcp__{self.server_name}__{tool.name}"
            mapped_tools[namespaced_name] = {
                "metadata": {
                    "name": namespaced_name,
                    "description": f"[{self.server_name.upper()} MCP] {tool.description}",
                    "base_risk": self.config.get("base_risk", "MEDIUM"),
                    "parameters": tool.inputSchema
                },
                "func": self._create_execution_handler(tool.name),
                "is_mcp": True
            }
        return mapped_tools

    def _create_execution_handler(self, original_tool_name: str):
        def handler(**kwargs) -> str:
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(
                self.session.call_tool(original_tool_name, arguments=kwargs)
            )
            output_texts = [c.text for c in result.content if c.type == "text"]
            return "\n".join(output_texts)
        return handler


class DynamicRegistry:
    # 📌 CÁC TOOL NỘI BỘ CỐT LÕI: Luôn tự động bật, không bị chặn bởi settings.json
    INTERNAL_REQUIRED_TOOLS = {"read_file", "get_sys_info", "list_directory"}

    def __init__(self, tools_dir="tools", skills_dir="skills", config_dir="config"):
        self.tools_dir = tools_dir
        self.skills_dir = skills_dir
        self.config_dir = config_dir
        
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, Any] = {}
        self.constitution: str = ""
        self.mcp_adapters: List[MCPClientAdapter] = []

    def load_all(self):
        """Nạp toàn bộ cấu hình, tools, MCP và skills"""
        self._load_settings()
        self._load_constitution()
        self._load_local_tools()
        self._load_mcp_servers()
        self._load_skills()

    def _load_settings(self):
        """1. Đọc file config duy nhất settings.json"""
        path = os.path.join(self.config_dir, "settings.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.settings = json.load(f)
            except Exception as e:
                print(f"⚠️ Lỗi đọc settings.json: {e}")
                self.settings = {}
        else:
            self.settings = {}

    def _load_constitution(self):
        """2. Nạp file Hiến pháp AGENT.md"""
        path = os.path.join(self.config_dir, "AGENT.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.constitution = f.read()

    def _load_local_tools(self):
        """3. Nạp Tool local Python kèm bộ lọc từ settings.json"""
        self.tools.clear()
        tools_cfg = self.settings.get("tools_config", {})
        allowed = tools_cfg.get("allowed_local_tools", ["*"])
        blocked = tools_cfg.get("blocked_local_tools", [])

        for fpath in glob.glob(os.path.join(self.tools_dir, "*.py")):
            if fpath.endswith("__init__.py"): 
                continue

            mname = os.path.basename(fpath)[:-3]
            spec = importlib.util.spec_from_file_location(mname, fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            if hasattr(mod, "TOOL_METADATA") and hasattr(mod, "run"):
                meta = mod.TOOL_METADATA
                t_name = meta["name"]

                # ⭐ NGUYÊN TẮC LỌC TOOL:
                # 1. Nếu là Tool nội bộ (INTERNAL) -> Luôn nạp.
                # 2. Nếu nằm trong danh sách blocked -> Bỏ qua.
                # 3. Nếu allowed không chứa "*" và t_name không có trong allowed -> Bỏ qua.
                is_internal = t_name in self.INTERNAL_REQUIRED_TOOLS
                
                if not is_internal:
                    if t_name in blocked:
                        print(f"🚫 [Tool Blocked]: {t_name}")
                        continue
                    if "*" not in allowed and t_name not in allowed:
                        continue

                self.tools[t_name] = {
                    "metadata": meta,
                    "func": mod.run,
                    "is_internal": is_internal
                }

    def _load_mcp_servers(self):
        """4. Nạp các MCP Server được khai báo trong settings.json"""
        mcp_servers = self.settings.get("mcp_servers", {})
        
        for s_name, s_config in mcp_servers.items():
            # Kiểm tra xem server này có được bật (enabled) hay không
            if not s_config.get("enabled", True):
                print(f"⏸️ [MCP Disabled]: {s_name}")
                continue

            try:
                adapter = MCPClientAdapter(s_name, s_config)
                
                # Khởi tạo kết nối Async trong môi trường Sync
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                loop.run_until_complete(adapter.connect())
                mcp_tools = loop.run_until_complete(adapter.get_mapped_tools())
                
                # Cập nhật các MCP tools vào bộ nhớ Registry chung
                self.tools.update(mcp_tools)
                self.mcp_adapters.append(adapter)
                print(f"🔌 [MCP Connected]: {s_name} ({len(mcp_tools)} tools)")

            except Exception as e:
                print(f"❌ Lỗi kết nối MCP Server [{s_name}]: {e}")

    def _load_skills(self):
        """5. Nạp Skill Workflows (.md)"""
        self.skills.clear()
        for fpath in glob.glob(os.path.join(self.skills_dir, "*.md")):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            name_m = re.search(r"name:\s*(.+)", content)
            risk_m = re.search(r"base_risk:\s*(.+)", content)
            if name_m:
                s_name = name_m.group(1).strip()
                self.skills[s_name] = {
                    "name": s_name,
                    "base_risk": risk_m.group(1).strip() if risk_m else "MEDIUM",
                    "content": content
                }

    def execute_slash_command(self, cmd: str) -> str:
        """Xử lý lệnh Slash từ UI/User"""
        cmd = cmd.strip()
        if cmd == "/init":
            self.load_all()
            return "🔄 **System Reloaded**: Đã tải lại Settings, Hiến pháp, Tools local, MCP và Skills."
        
        elif cmd == "/tools":
            res = "### 🛠️ Loaded Tools (Local & MCP)\n"
            for k, v in self.tools.items():
                meta = v["metadata"]
                risk = meta.get("base_risk", "LOW")
                desc = meta.get("description", "")
                
                tag = ""
                if v.get("is_internal"):
                    tag = " `[CORE]`"
                elif v.get("is_mcp"):
                    tag = " `[MCP]`"

                res += f"- **`{k}`**{tag} (Risk: `{risk}`): {desc}\n"
            return res
        
        elif cmd == "/skills":
            res = "### 📚 Loaded Skill Workflows\n"
            for k, v in self.skills.items():
                res += f"- **`{k}`** (Risk: `{v['base_risk']}`)\n"
            return res
            
        elif cmd == "/context":
            return f"### 🧠 Active Constitution Snapshot\n```markdown\n{self.constitution[:400]}...\n```"
            
        return "❌ Command không hợp lệ."