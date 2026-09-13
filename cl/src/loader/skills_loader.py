import os
import glob
import re
from typing import Dict, Any

class SkillManager:
    """Chuyên trách quét và nạp danh sách Skills từ thư mục skills/"""
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir

    def load_skills(self) -> Dict[str, Dict[str, Any]]:
        skills = {}
        pattern_subfolder = os.path.join(self.skills_dir, "*", "*.md")
        pattern_rootfolder = os.path.join(self.skills_dir, "*.md")
        skill_files = glob.glob(pattern_subfolder) + glob.glob(pattern_rootfolder)

        for fpath in skill_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()

                name_m = re.search(r"name:\s*(.+)", content)
                risk_m = re.search(r"base_risk:\s*(.+)", content)

                s_name = name_m.group(1).strip() if name_m else os.path.basename(os.path.dirname(fpath))
                if not s_name or s_name == os.path.basename(self.skills_dir):
                    s_name = os.path.splitext(os.path.basename(fpath))[0]

                skills[s_name] = {
                    "name": s_name,
                    "base_risk": risk_m.group(1).strip() if risk_m else "MEDIUM",
                    "content": content
                }
            except Exception as e:
                print(f"⚠️ Lỗi đọc skill tại {fpath}: {e}")

        print(f"✅ [Skills Loaded]: {len(skills)} skills từ {self.skills_dir}")
        return skills