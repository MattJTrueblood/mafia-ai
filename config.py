"""Configuration module for loading API keys and game settings."""

import os


def load_openrouter_key():
    """Load OpenRouter API key from openrouter_key.txt file."""
    key_path = os.path.join(os.path.dirname(__file__), "openrouter_key.txt")
    try:
        with open(key_path, "r") as f:
            key = f.read().strip()
        if not key:
            raise ValueError("OpenRouter API key file is empty")
        return key
    except FileNotFoundError:
        raise FileNotFoundError(f"OpenRouter API key file not found at {key_path}")


# Game configuration
DEFAULT_PLAYER_COUNT = 7

# Default models with pricing (per 1M tokens: input / output)
# Prices verified from openrouter.ai model pages
DEFAULT_MODELS = [
    "qwen/qwen-turbo",                              # $0.05 / $0.20
    "mistralai/mistral-small-3.2-24b-instruct",     # $0.06 / $0.18
    "meta-llama/llama-3.3-70b-instruct",            # $0.10 / $0.32
    "meta-llama/llama-4-maverick",                  # $0.15 / $0.60
    "x-ai/grok-4-fast",                             # $0.20 / $0.50 (starting at)
    "deepseek/deepseek-v3.2",                       # $0.224 / $0.32
    "openai/gpt-5-mini",                            # $0.25 / $2.00
    "openai/gpt-4.1-mini",                          # $0.40 / $1.60 (starting at)
    "mistralai/mistral-large-2512",                 # $0.50 / $1.50
]

# Model pricing dictionary (per 1M tokens: input / output)
# Used for displaying pricing in the UI
MODEL_PRICING = {
    "qwen/qwen-turbo": {"input": 0.05, "output": 0.20},
    "mistralai/mistral-small-3.2-24b-instruct": {"input": 0.06, "output": 0.18},
    "meta-llama/llama-3.3-70b-instruct": {"input": 0.10, "output": 0.32},
    "meta-llama/llama-4-maverick": {"input": 0.15, "output": 0.60},
    "x-ai/grok-4-fast": {"input": 0.20, "output": 0.50},          # starting at
    "deepseek/deepseek-v3.2": {"input": 0.224, "output": 0.32},
    "openai/gpt-5-mini": {"input": 0.25, "output": 2.00},
    "openai/gpt-4.1-mini": {"input": 0.40, "output": 1.60},       # starting at
    "mistralai/mistral-large-2512": {"input": 0.50, "output": 1.50},
    "openai/gpt-oss-20b": {"input": 0.03, "output": 0.14},
}
