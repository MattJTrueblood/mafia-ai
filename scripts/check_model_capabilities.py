"""Check which OpenRouter models support tools, reasoning, etc."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json
from config import load_openrouter_key, DEFAULT_MODELS


def check_model_capabilities():
    """Query OpenRouter models API and show supported parameters."""
    api_key = load_openrouter_key()

    print("Fetching model capabilities from OpenRouter API...\n")

    response = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    response.raise_for_status()

    models_data = response.json()

    # Add diagnostic models to check
    target_models = DEFAULT_MODELS + [
        "openai/gpt-4o",
        "openai/gpt-5.2",
    ]

    # Remove duplicates
    target_models = list(set(target_models))
    target_models.sort()

    print(f"Checking {len(target_models)} models...\n")
    print("=" * 80)

    # Track which models support what
    tool_models = []
    reasoning_models = []

    for model in models_data["data"]:
        if model["id"] in target_models:
            supported = model.get("supported_parameters", [])

            print(f"\n{model['id']}:")
            print(f"  Tools: {'tools' in supported}")
            print(f"  Reasoning: {'reasoning' in supported}")
            print(f"  Response format: {'response_format' in supported}")
            print(f"  Structured outputs: {'structured_outputs' in supported}")

            if supported:
                print(f"  All supported params: {', '.join(sorted(supported))}")

            # Track for summary
            if 'tools' in supported:
                tool_models.append(model["id"])
            if 'reasoning' in supported:
                reasoning_models.append(model["id"])

    # Print summary
    print("\n" + "=" * 80)
    print("\nSUMMARY:")
    print("\nModels that support TOOLS (use Responses API):")
    if tool_models:
        for m in sorted(tool_models):
            print(f"  - {m}")
    else:
        print("  None found")

    print("\nModels that support REASONING:")
    if reasoning_models:
        for m in sorted(reasoning_models):
            print(f"  - {m}")
    else:
        print("  None found")

    print("\n" + "=" * 80)
    print("\nNext steps:")
    print("1. Update config.py TOOL_MODELS list with models that support tools")
    print("2. Update config.py REASONING_MODELS list with models that support reasoning")
    print("3. Add pricing for new models (gpt-4o, gpt-5.2) to MODEL_PRICING")


if __name__ == "__main__":
    check_model_capabilities()
