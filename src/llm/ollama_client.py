# src/llm/ollama_client.py
import requests
from src.llm.base import BaseLLM

class OllamaClient(BaseLLM):
    def __init__(self, config: dict):
        self.endpoint = f"{config['endpoint']}/api/generate"
        self.model = config['model']

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\nUser: {user_prompt}",
            "stream": False
        }
        try:
            response = requests.post(self.endpoint, json=payload, timeout=30)
            return response.json().get("response", "")
        except Exception as e:
            return f"Error connecting to Local LLM: {str(e)}"