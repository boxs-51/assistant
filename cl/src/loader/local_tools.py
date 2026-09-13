import sys
import logging
import types
from pathlib import Path
import importlib.util
from typing import Dict, Any, Set, Optional, Union

logger = logging.getLogger(__name__)

class LocalToolManager:
    """Chuyên trách phát hiện, nạp và lọc các Local Python Tools trong thư mục tools/"""
    
    def __init__(self, tools_dir: Union[str, Path], internal_required_tools: Set[str]):
        self.tools_dir = Path(tools_dir).resolve()
        self.internal_required_tools = internal_required_tools

    def _setup_import_environment(self) -> None:
        """Thêm tools_dir vào sys.path và khởi tạo mock package 'local_tools'."""
        tools_str = str(self.tools_dir)
        
        # 1. Cho phép 'import helper_module' trực tiếp
        if tools_str not in sys.path:
            sys.path.insert(0, tools_str)

        # 2. Tạo virtual package 'local_tools' để hỗ trợ relative import (from . import helper)
        if "local_tools" not in sys.modules:
            pkg_mod = types.ModuleType("local_tools")
            pkg_mod.__path__ = [tools_str]
            pkg_mod.__file__ = str(self.tools_dir / "__init__.py")
            sys.modules["local_tools"] = pkg_mod

    def load_tools(self, tools_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        loaded_tools = {}
        allowed = tools_config.get("allowed_local_tools", ["*"])
        blocked = set(tools_config.get("blocked_local_tools", []))

        if not self.tools_dir.exists() or not self.tools_dir.is_dir():
            logger.error(f"Thư mục tools không tồn tại: {self.tools_dir}")
            return loaded_tools

        # Thiết lập môi trường import trước khi nạp bất kỳ file nào
        self._setup_import_environment()

        # Lấy danh sách file .py (bỏ qua __init__.py)
        tool_files = [f for f in self.tools_dir.glob("*.py") if f.name != "__init__.py"]

        for fpath in tool_files:
            mname = f"local_tools.{fpath.stem}"

            # Nếu module đã được nạp tự động trước đó (do một tool khác import nó làm helper)
            if mname in sys.modules and hasattr(sys.modules[mname], "TOOL_METADATA"):
                mod = sys.modules[mname]
            else:
                try:
                    spec = importlib.util.spec_from_file_location(
                        mname, 
                        fpath,
                        submodule_search_locations=[str(self.tools_dir)]
                    )
                    if not spec or not spec.loader:
                        logger.warning(f"Không thể tạo module spec cho: {fpath}")
                        continue

                    mod = importlib.util.module_from_spec(spec)
                    mod.__package__ = "local_tools"
                    
                    # Đăng ký sẵn vào sys.modules trước khi exec để hỗ trợ circular/sub-import
                    sys.modules[mname] = mod
                    if fpath.stem not in sys.modules:
                        sys.modules[fpath.stem] = mod

                    spec.loader.exec_module(mod)

                except Exception as e:
                    # Dọn dẹp nếu nạp thất bại
                    sys.modules.pop(mname, None)
                    sys.modules.pop(fpath.stem, None)
                    logger.error(f"❌ Lỗi khi nạp tool/module từ '{fpath.name}': {str(e)}", exc_info=True)
                    continue

            # Kiểm tra hợp lệ cấu trúc Tool
            if hasattr(mod, "TOOL_METADATA") and hasattr(mod, "run"):
                meta = getattr(mod, "TOOL_METADATA")
                
                if not isinstance(meta, dict) or "name" not in meta:
                    logger.warning(f"⚠️ [Tool Skipped]: {fpath.name} thiếu key 'name' trong TOOL_METADATA.")
                    continue

                t_name = meta["name"]
                is_internal = t_name in self.internal_required_tools

                # Xử lý Whitelist / Blacklist
                if not is_internal:
                    if t_name in blocked:
                        logger.info(f"🚫 [Tool Blocked]: {t_name}")
                        continue
                    if "*" not in allowed and t_name not in allowed:
                        continue

                loaded_tools[t_name] = {
                    "metadata": meta,
                    "func": getattr(mod, "run"),
                    "is_internal": is_internal,
                    "file_path": str(fpath)
                }
            else:
                # File .py tiện ích/helper hợp lệ nhưng không phải Tool -> Giữ trong sys.modules để các tool khác sử dụng
                logger.debug(f"ℹ️ [Helper Module Loaded]: {fpath.name}")

        logger.info(f"✅ [Local Tools Loaded]: {len(loaded_tools)} tools từ {self.tools_dir}")
        return loaded_tools