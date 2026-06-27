import os
import importlib
import inspect
from src.skills.base_skill import BaseSkill

class SkillRegistry:
    def __init__(self):
        self._skills = {}

    def discover_and_register(self, directory="src/skills"):
        """Tự động quét, tải và đăng ký tất cả các Skill trong một thư mục."""
        for filename in os.listdir(directory):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]
                module_path = f"{directory.replace('/', '.')}.{module_name}"
                
                try:
                    module = importlib.import_module(module_path)
                    for name, cls in inspect.getmembers(module, inspect.isclass):
                        if issubclass(cls, BaseSkill) and cls is not BaseSkill and cls.__module__ == module.__name__:
                            skill_instance = cls()
                            self.register(skill_instance)
                except Exception as e:
                    print(f"⚠️ [SkillRegistry] Lỗi khi tải skill từ file {filename}: {e}")

    def register(self, skill: BaseSkill):
        self._skills[skill.name] = skill

    def get_skills_manifest(self) -> str:
        prompt_parts = [f"- Tên: {skill.name}\n  Mô tả: {skill.description}" for skill in self._skills.values()]
        return "\n".join(prompt_parts)