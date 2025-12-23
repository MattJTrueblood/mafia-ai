"""Configuration management for the Mafia AI game."""
import os
from pathlib import Path


def load_api_key():
    """Load OpenRouter API key from file."""
    key_file = Path("openrouter key.txt")
    if key_file.exists():
        with open(key_file, "r") as f:
            return f.read().strip()
    # Fallback to environment variable
    return os.getenv("OPENROUTER_API_KEY", "")


# OpenRouter API configuration
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = load_api_key()

# Default game settings
DEFAULT_DISCUSSION_TIME_LIMIT = 300  # 5 minutes in seconds
DEFAULT_VOTING_TIME_LIMIT = 120  # 2 minutes in seconds
DEFAULT_NIGHT_TIME_LIMIT = 180  # 3 minutes in seconds

# Available roles
ROLES = {
    "Mafia": "mafia",
    "Town": "town",
    "Sheriff": "sheriff",
    "Doctor": "doctor",
    "Vigilante": "vigilante"
}

# Game phases
PHASE_DAY = "day"
PHASE_NIGHT = "night"
PHASE_DISCUSSION = "discussion"
PHASE_VOTING = "voting"
PHASE_GAME_OVER = "game_over"

# Default models (can be overridden per player)
DEFAULT_MODELS = [
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemini-flash-1.5",
    "mistralai/mistral-7b-instruct:free",
    "openai/gpt-3.5-turbo",
    "anthropic/claude-3-haiku",
]

