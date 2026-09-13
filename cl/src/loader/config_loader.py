import os
import json
from typing import Dict, Any

class ConfigLoader:
    """Chuyên trách tải file cấu hình (setting.json) và Hiến pháp (AGENT.md)"""
    def __init__(self, config_dir: str):
        self.config_dir = config_dir

    def load_settings(self) -> Dict[str, Any]:
        possible_paths = [
            os.path.join(self.config_dir, "setting.json"),
            os.path.join(self.config_dir, "settings.json")
        ]
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        print(f"✅ [Config Loaded]: {path}")
                        return json.load(f)
                except Exception as e:
                    print(f"⚠️ Lỗi đọc file config {path}: {e}")
                    return {}
        print(f"⚠️ Không tìm thấy file setting.json trong {self.config_dir}")
        return {}

    def load_constitution(self) -> str:
        path = os.path.join(self.config_dir, "AGENT.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                print("✅ [Constitution Loaded]: AGENT.md")
                return f.read()
        print(f"⚠️ Không tìm thấy AGENT.md trong {self.config_dir}")
        return ""