"""OpenRouter API client for LLM interactions."""

import json
import time
import requests
from typing import Dict, List, Optional, Any
from config import load_openrouter_key


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""
    
    def __init__(self):
        self.api_key = load_openrouter_key()
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.max_retries = 3
        self.retry_delay = 1
    
    def call_model(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Call OpenRouter API with a model.
        
        Args:
            model: Model identifier (e.g., "openai/gpt-4o")
            messages: List of message dicts with "role" and "content"
            response_format: Optional structured output schema
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
        
        Returns:
            Dict with "content" and optionally "structured_output"
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mafia-ai",  # Optional
            "X-Title": "Mafia AI Game",  # Optional
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        
        if response_format:
            payload["response_format"] = response_format
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        # Retry logic
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=60
                )
                response.raise_for_status()
                
                data = response.json()
                
                # Extract content
                content = data["choices"][0]["message"]["content"]
                
                result = {"content": content}
                
                # Extract structured output if present
                if "structured_outputs" in data.get("choices", [{}])[0].get("message", {}):
                    result["structured_output"] = data["choices"][0]["message"]["structured_outputs"]
                elif response_format:
                    # Try to parse JSON from content if structured output not available
                    try:
                        # Look for JSON in the content
                        json_start = content.find("{")
                        json_end = content.rfind("}") + 1
                        if json_start >= 0 and json_end > json_start:
                            json_str = content[json_start:json_end]
                            result["structured_output"] = json.loads(json_str)
                    except (json.JSONDecodeError, ValueError):
                        pass
                
                return result
                
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                else:
                    raise Exception(f"OpenRouter API error after {self.max_retries} attempts: {str(e)}")
        
        raise Exception("Failed to call OpenRouter API")

