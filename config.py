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

# Default models - vetted for reliable tool calling performance
DEFAULT_MODELS = [
    "x-ai/grok-4.1-fast",              # Best BFCL v4 score, great value
    #"anthropic/claude-sonnet-4.5",     # Strong agentic capabilities
    "openai/gpt-5.2",                  # Premium flagship
    "deepseek/deepseek-v3.2",          # Excellent value with reasoning
    "openai/gpt-4o",                   # Proven reliable
    "google/gemini-2.5-flash",         # Fast and affordable
    "google/gemini-2.5-pro",           # More capable Gemini
    "moonshotai/kimi-k2-0905",         # Fast Kimi without reasoning overhead
    #"anthropic/claude-opus-4.5",       # Premium reasoning (expensive)
    "mistralai/mistral-large-2512",    # Enterprise option
]

# Models that support tool calling (use Responses API)
TOOL_MODELS = [
    "x-ai/grok-4.1-fast",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-opus-4.5",
    "openai/gpt-5.2",
    "openai/gpt-4o",
    "deepseek/deepseek-v3.2",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    "moonshotai/kimi-k2-0905",
    "mistralai/mistral-large-2512",
]

# Model pricing dictionary (per 1M tokens: input / output)
# Used for displaying pricing in the UI
# Sorted by cost (cheapest to most expensive)
# Verified via OpenRouter API 2025-12-31
MODEL_PRICING = {
    # Budget tier (< $1 per 1M input)
    "x-ai/grok-4.1-fast": {"input": 0.20, "output": 0.50},
    "deepseek/deepseek-v3.2": {"input": 0.25, "output": 0.38},
    "google/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "moonshotai/kimi-k2-0905": {"input": 0.39, "output": 1.90},
    "mistralai/mistral-large-2512": {"input": 0.50, "output": 1.50},
    # Mid tier ($1-3 per 1M input)
    "google/gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "openai/gpt-5.2": {"input": 1.75, "output": 14.00},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    # Premium tier (> $3 per 1M input)
    "anthropic/claude-opus-4.5": {"input": 5.00, "output": 25.00},
}
