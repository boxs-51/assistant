from ..core import ApiType
GEMINI_MODEL_MAP = {
    # Gemini 2.5 Native
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",

    # OpenAI Aliases
    "gpt-4o": "gemini-2.5-pro",
    "gpt-4o-mini": "gemini-2.5-flash",

    # Legacy Gemini
    "gemini-pro": "gemini-2.5-pro",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",

    # --- Ollama Models: Small/Fast (Map sang Gemini 2.5 Flash) ---
    "llama3": "gemini-2.5-flash",
    "llama3:latest": "gemini-2.5-flash",
    "llama3:8b": "gemini-2.5-flash",
    "llama3.1": "gemini-2.5-flash",
    "llama3.1:8b": "gemini-2.5-flash",
    "llama3.2": "gemini-2.5-flash",
    "llama3.2:1b": "gemini-2.5-flash",
    "llama3.2:3b": "gemini-2.5-flash",

    "gemma": "gemini-2.5-flash",
    "gemma:2b": "gemini-2.5-flash",
    "gemma:7b": "gemini-2.5-flash",
    "gemma2": "gemini-2.5-flash",
    "gemma2:2b": "gemini-2.5-flash",
    "gemma2:9b": "gemini-2.5-flash",
    "gemma3": "gemini-2.5-flash",
    "gemma3:1b": "gemini-2.5-flash",
    "gemma3:4b": "gemini-2.5-flash",
    "gemma3:12b": "gemini-2.5-flash",

    "qwen2.5": "gemini-2.5-flash",
    "qwen2.5:7b": "gemini-2.5-flash",
    "qwen2.5:14b": "gemini-2.5-flash",

    "mistral": "gemini-2.5-flash",
    "mistral:7b": "gemini-2.5-flash",
    "phi3": "gemini-2.5-flash",
    "phi4": "gemini-2.5-flash",

    "deepseek-r1": "gemini-2.5-flash",
    "deepseek-r1:7b": "gemini-2.5-flash",
    "deepseek-r1:8b": "gemini-2.5-flash",
    "deepseek-r1:14b": "gemini-2.5-flash",

    # --- Ollama Models: Large/Reasoning (Map sang Gemini 2.5 Pro) ---
    "llama3.1:70b": "gemini-2.5-pro",
    "llama3.1:405b": "gemini-2.5-pro",
    "llama3.3": "gemini-2.5-pro",
    "llama3.3:70b": "gemini-2.5-pro",
    "llama3.3:latest": "gemini-2.5-pro",

    "gemma2:27b": "gemini-2.5-pro",
    "gemma3:27b": "gemini-2.5-pro",

    "qwen2.5:32b": "gemini-2.5-pro",
    "qwen2.5:72b": "gemini-2.5-pro",
    "qwen2.5-coder": "gemini-2.5-pro",
    "qwen2.5-coder:32b": "gemini-2.5-pro",

    "mixtral": "gemini-2.5-pro",
    "mixtral:8x7b": "gemini-2.5-pro",
    "mixtral:8x22b": "gemini-2.5-pro",

    "deepseek-r1:32b": "gemini-2.5-pro",
    "deepseek-r1:70b": "gemini-2.5-pro",
    "deepseek-v3": "gemini-2.5-pro",

    "default": "gemini-2.5-flash"
}

# Ánh xạ ApiType sang endpoint template của Gemini
GEMINI_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "v1beta/models/{model}:{action}",
    ApiType.MODELS: "v1beta/models",
    ApiType.MODEL: "v1beta/models/{model}",
    ApiType.EMBEDDINGS: "v1beta/models/{model}:{action}",
    ApiType.IMAGE_GENERATION: "v1/images:generate", # Giả định endpoint cho Imagen 2
    ApiType.TEXT_TO_SPEECH: "v1/text:synthesize", # Giả định endpoint cho Text-to-Speech
    ApiType.FILES : "v1beta/files", # Endpoint cho File API
}