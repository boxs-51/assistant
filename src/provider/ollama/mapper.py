from ..core import ApiType
# Ánh xạ từ OpenAI/Gemini sang các model Ollama tương ứng
OLLAMA_MODEL_MAP = {
    # --- Flagship / Pro Tier -> Ollama Large Models (70B / 32B) ---
    "gpt-4o": "llama3.3:70b",
    "gpt-4": "llama3.3:70b",
    "gpt-4-turbo": "llama3.3:70b",
    "gemini-2.5-pro": "llama3.3:70b",
    "gemini-1.5-pro": "llama3.3:70b",
    "gemini-pro": "llama3.3:70b",

    # --- Reasoning Tier -> Ollama Reasoning Models ---
    "o1": "deepseek-r1:32b",
    "o3-mini": "deepseek-r1:14b",

    # --- Fast / Light Tier -> Ollama Lightweight Models (8B / 7B / 3B) ---
    "gpt-4o-mini": "llama3.1:8b",
    "gpt-3.5-turbo": "llama3.1:8b",
    "gemini-2.5-flash": "gemma2:9b",
    "gemini-1.5-flash": "gemma2:9b",

    # --- Code Specialized Tier ---
    "claude-3-5-sonnet": "qwen2.5-coder:32b",
    "gpt-4-coder": "qwen2.5-coder:32b",

    # --- Default Fallback ---
    "default": "llama3.1:8b"
}

# Ánh xạ ApiType sang endpoint tương ứng của Ollama API
OLLAMA_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "api/chat",
    ApiType.IMAGE_GENERATION: "api/generate",
    ApiType.EMBEDDINGS: "api/embed",
    ApiType.MODELS: "api/tags",
    ApiType.MODEL: "api/show",
}