"""OpenRouter API client for LLM interactions."""

import json
import time
import requests
from typing import Dict, List, Optional, Any
from config import load_openrouter_key


class LLMCancelledException(Exception):
    """Raised when an LLM call is cancelled due to game pause."""
    pass


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
        max_tokens: Optional[int] = None,
        cancel_event: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Call OpenRouter API with a model.

        Args:
            model: Model identifier (e.g., "openai/gpt-4o")
            messages: List of message dicts with "role" and "content"
            response_format: Optional structured output schema
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            cancel_event: Optional gevent.event.Event to check for cancellation

        Returns:
            Dict with "content" and optionally "structured_output"

        Raises:
            LLMCancelledException: If cancel_event is set during the call
        """
        # Check if already cancelled before starting
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledException("Call cancelled before starting")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mafia-ai",  # Optional
            "X-Title": "Mafia AI Game",  # Optional
        }

        # Detect reasoning models that need special handling
        model_lower = model.lower()
        is_reasoning_model = any(x in model_lower for x in ["gpt-4.5", "gpt-5", "o1", "o3"])

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # For reasoning models, set minimal effort to maximize output tokens
        # and request reasoning to be included in the response
        # Note: OpenAI o-series models do NOT return reasoning tokens regardless of exclude setting
        if is_reasoning_model:
            payload["reasoning"] = {
                "effort": "minimal",  # 10% reasoning, 90% output - maximum output focus
                "exclude": False,     # Include reasoning in response (if model supports it)
            }

        if response_format:
            payload["response_format"] = response_format

        if max_tokens:
            # Newer OpenAI models (gpt-4.5, o1, o3, etc.) use max_completion_tokens
            # instead of max_tokens.
            if is_reasoning_model:
                payload["max_completion_tokens"] = max_tokens
            else:
                payload["max_tokens"] = max_tokens

        # Retry logic
        for attempt in range(self.max_retries):
            # Check cancellation before each attempt
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledException("Call cancelled before attempt")

            try:
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=30  # Shorter timeout for better responsiveness
                )
                response.raise_for_status()

                # Check cancellation after receiving response
                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled after response")

                data = response.json()

                # Debug: Check for API-level errors or empty responses
                if "error" in data:
                    print(f"[OpenRouter API Error] {data['error']}")
                    raise Exception(f"OpenRouter API error: {data['error']}")

                if not data.get("choices"):
                    print(f"[OpenRouter] No choices in response: {data}")
                    raise Exception("OpenRouter returned no choices")

                # Extract message object
                message = data["choices"][0]["message"]

                # Debug: Log if content is empty with structured output request
                if response_format and not message.get("content"):
                    print(f"[OpenRouter] Empty content with response_format. Model: {model}")
                    print(f"[OpenRouter] Message keys: {list(message.keys())}")
                    print(f"[OpenRouter] Full message: {message}")

                # Extract content
                content = message.get("content") or ""

                result = {
                    "content": content,
                    "raw_message": message,  # Include raw message for debugging
                }

                # Capture reasoning tokens if present (for reasoning models)
                # OpenRouter returns reasoning in 'reasoning_details' array
                # Note: OpenAI o-series models do NOT return their reasoning tokens
                if "reasoning_details" in message and message["reasoning_details"]:
                    reasoning_parts = []
                    for detail in message["reasoning_details"]:
                        if detail.get("type") == "reasoning.summary" and detail.get("summary"):
                            reasoning_parts.append(f"[Summary] {detail['summary']}")
                        elif detail.get("type") == "reasoning.text" and detail.get("text"):
                            reasoning_parts.append(detail["text"])
                        elif detail.get("text"):
                            reasoning_parts.append(detail["text"])
                        elif detail.get("summary"):
                            reasoning_parts.append(detail["summary"])
                    if reasoning_parts:
                        result["reasoning"] = "\n\n".join(reasoning_parts)
                # Also check legacy field names just in case
                elif "reasoning" in message and message["reasoning"]:
                    result["reasoning"] = message["reasoning"]

                # Extract structured output if present
                if "structured_outputs" in message:
                    result["structured_output"] = message["structured_outputs"]
                elif "structured_output" in message:
                    result["structured_output"] = message["structured_output"]
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

            except LLMCancelledException:
                raise  # Re-raise cancellation exceptions immediately
            except requests.exceptions.Timeout:
                # On timeout, check if we were cancelled
                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled during timeout")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise Exception(f"OpenRouter API timeout after {self.max_retries} attempts")
            except requests.exceptions.RequestException as e:
                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled during error")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                else:
                    raise Exception(f"OpenRouter API error after {self.max_retries} attempts: {str(e)}")

        raise Exception("Failed to call OpenRouter API")