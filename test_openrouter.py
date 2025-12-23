import requests
import json

# Read API key from file
with open("openrouter key.txt", "r") as f:
    api_key = f.read().strip()

# OpenRouter API endpoint
url = "https://openrouter.ai/api/v1/chat/completions"

# Headers
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

# Request body - simple hello world prompt
# Try a free model (models with :free suffix or specific free models)
# Common free models: meta-llama/llama-3.2-3b-instruct:free, google/gemini-flash-1.5, etc.
payload = {
    "model": "meta-llama/llama-3.2-3b-instruct:free",  # Using a free model for testing
    "messages": [
        {
            "role": "user",
            "content": "Hello! Please respond with a simple greeting and tell me you're ready to play Mafia."
        }
    ]
}

print("Sending request to OpenRouter...")
print(f"Model: {payload['model']}")
print(f"Message: {payload['messages'][0]['content']}")
print()

try:
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    
    result = response.json()
    
    # Extract the assistant's message
    assistant_message = result["choices"][0]["message"]["content"]
    
    print("Response received:")
    print("-" * 50)
    print(assistant_message)
    print("-" * 50)
    
    # Print some metadata
    if "usage" in result:
        print(f"\nTokens used: {result['usage']}")
    
except requests.exceptions.RequestException as e:
    print(f"Error: {e}")
    if hasattr(e, 'response') and e.response is not None:
        print(f"Response: {e.response.text}")

