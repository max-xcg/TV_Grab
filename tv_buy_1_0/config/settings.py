import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", 30))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", 800))
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", 0.6))
ENABLE_LLM = os.getenv("ENABLE_LLM", "0") == "1"

# ========= LLM Provider =========

# 可选：deepseek / openai

# ========= DeepSeek =========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com"
)
DEEPSEEK_MODEL = os.getenv(
    "DEEPSEEK_MODEL",
    "deepseek-chat"
)
