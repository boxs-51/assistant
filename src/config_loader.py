import yaml
import os

class ConfigLoader:
    def __init__(self, config_path="config/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def get_system_prompt(self) -> str:
        soul = self.config["agent_soul"]
        rules = "\n".join([f"- {r}" for r in self.config["agent_rules"]])
        
        system_prompt = f"""
Role: {soul['role']}
Tone: {soul['tone']}
Language: {soul['language']}

Rules:
{rules}
"""
        return system_prompt.strip()

    def get_provider_config(self, provider_type: str) -> dict:
        return self.config["llm_providers"].get(provider_type, {})