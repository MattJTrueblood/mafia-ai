"""OpenRouter API client for LLM interactions."""

import json
import logging
import time
import requests
from typing import Dict, List, Optional, Any
from config import load_openrouter_key, TOOL_MODELS, REASONING_MODELS


class LLMCancelledException(Exception):
    """Raised when an LLM call is cancelled due to game pause."""
    pass


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""

    def __init__(self):
        self.api_key = load_openrouter_key()
        self.chat_url = "https://openrouter.ai/api/v1/chat/completions"
        self.responses_url = "https://openrouter.ai/api/v1/responses"
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

        Routes to either Chat API or Responses API based on model capabilities.

        Args:
            model: Model identifier (e.g., "openai/gpt-4o")
            messages: List of message dicts with "role" and "content"
            response_format: Optional structured output schema
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            cancel_event: Optional gevent.event.Event to check for cancellation

        Returns:
            Dict with "content" and optionally "structured_output" and "reasoning"

        Raises:
            LLMCancelledException: If cancel_event is set during the call
        """
        # Check cancellation
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledException("Call cancelled before starting")

        # Route to appropriate API:
        # Use Responses API if model supports tools AND we're requesting structured output
        # Otherwise use Chat API (plain text OR model doesn't support tools)
        use_responses_api = self._supports_tools(model) and response_format is not None

        if use_responses_api:
            return self._call_responses_api(
                model, messages, response_format, temperature, max_tokens, cancel_event
            )
        else:
            return self._call_chat_api(
                model, messages, response_format, temperature, max_tokens, cancel_event
            )

    def _call_chat_api(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, Any]],
        temperature: float,
        max_tokens: Optional[int],
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Call Chat API (traditional completions endpoint)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mafia-ai",
            "X-Title": "Mafia AI Game",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # Add reasoning config for reasoning models
        if self._is_reasoning_model(model):
            # Different models use different reasoning parameter formats
            if "deepseek" in model.lower():
                # DeepSeek: Control via max_tokens (total output limit)
                payload["reasoning"] = {
                    "enabled": True,
                }
                # Set conservative total output limit if not already set
                if max_tokens is None:
                    payload["max_tokens"] = 4096  # Total: reasoning + answer
            elif "moonshotai" in model.lower() or "kimi" in model.lower():
                # Kimi: Use effort level like other models (max_tokens may not work)
                payload["reasoning"] = {
                    "effort": "low",
                    "exclude": False,
                    "summary": "auto",
                }
            else:
                # OpenAI (GPT-5, o1, o3) and Grok models use effort parameter
                payload["reasoning"] = {
                    "effort": "low",  # 20% allocation - "minimal" not supported by GPT-5.x
                    "exclude": False,
                    "summary": "auto",  # Request reasoning summaries in plaintext
                }

        if response_format:
            payload["response_format"] = response_format

        # Note: max_tokens intentionally not set to prevent output truncation
        # Token usage is controlled through prompting instead

        # Debug logging - log full payload for reasoning models
        if self._is_reasoning_model(model):
            logging.info(f"Chat API request (reasoning model): model={model}, payload_keys={list(payload.keys())}, reasoning_config={payload.get('reasoning')}")
        else:
            logging.info(f"Chat API request: model={model}, messages_count={len(messages)}, has_response_format={response_format is not None}")

        # Retry logic
        for attempt in range(self.max_retries):
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledException("Call cancelled before attempt")

            try:
                response = requests.post(
                    self.chat_url,
                    headers=headers,
                    json=payload,
                    timeout=30
                )

                # Log error details before raising
                if response.status_code != 200:
                    try:
                        error_data = response.json()
                        logging.error(f"Chat API error {response.status_code}: {error_data}")
                    except:
                        logging.error(f"Chat API error {response.status_code}: {response.text}")

                response.raise_for_status()

                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled after response")

                data = response.json()

                # Debug logging - log full message structure for diagnosis
                if data.get("choices"):
                    message = data["choices"][0]["message"]
                    content = message.get("content") or ""
                    has_reasoning_details = "reasoning_details" in message
                    has_reasoning = "reasoning" in message
                    logging.info(
                        f"Chat API response: model={model}, "
                        f"message_keys={list(message.keys())}, "
                        f"content_length={len(content)}, "
                        f"has_reasoning_details={has_reasoning_details}, "
                        f"has_reasoning={has_reasoning}"
                    )
                else:
                    logging.info(f"Chat API response: model={model}, has_choices=False")

                if data.get("error"):
                    logging.error(f"OpenRouter API error: {data['error']}")
                    raise Exception(f"OpenRouter API error: {data['error']}")

                if not data.get("choices"):
                    logging.error("OpenRouter returned no choices")
                    raise Exception("OpenRouter returned no choices")

                message = data["choices"][0]["message"]
                content = message.get("content") or ""

                # Extract reasoning
                reasoning = self._extract_reasoning(message)

                # Log what we extracted for diagnosis
                if reasoning:
                    logging.info(f"Extracted reasoning from {model} ({len(reasoning)} chars):\n{reasoning}")

                # Log token usage for cost monitoring
                if "usage" in data:
                    usage = data["usage"]
                    total_tokens = usage.get("total_tokens", 0)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    logging.info(f"Token usage for {model}: {total_tokens} total ({prompt_tokens} prompt + {completion_tokens} completion)")

                # Warn if content is empty
                if not content:
                    logging.warning(f"Empty response from model {model}")

                result = {"content": content}

                # Store reasoning separately if it exists
                if reasoning:
                    result["reasoning"] = reasoning

                # Extract structured output
                if "structured_outputs" in message:
                    result["structured_output"] = message["structured_outputs"]
                elif "structured_output" in message:
                    result["structured_output"] = message["structured_output"]

                return result

            except LLMCancelledException:
                raise
            except requests.exceptions.Timeout:
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
                raise Exception(f"OpenRouter API error after {self.max_retries} attempts: {str(e)}")

        raise Exception("Failed to call OpenRouter API")

    def _call_responses_api(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Dict[str, Any],
        temperature: float,
        max_tokens: Optional[int],
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Call Responses API (tool calling endpoint)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mafia-ai",
            "X-Title": "Mafia AI Game",
        }

        # Convert messages to Responses API input format
        input_messages = self._messages_to_input(messages)

        payload = {
            "model": model,
            "input": input_messages,
            "temperature": temperature,
            "tools": [self._schema_to_tool(response_format)],
            "tool_choice": {"type": "function", "name": "structured_response"}
        }

        # Debug logging - log the full payload
        logging.info(f"Responses API payload: {json.dumps(payload, indent=2)}")

        # Add reasoning config for reasoning models
        if self._is_reasoning_model(model):
            # Different models use different reasoning parameter formats
            if "deepseek" in model.lower():
                # DeepSeek: Control via max_tokens (total output limit)
                payload["reasoning"] = {
                    "enabled": True,
                }
                # Set conservative total output limit if not already set
                if max_tokens is None:
                    payload["max_tokens"] = 4096  # Total: reasoning + answer
            elif "moonshotai" in model.lower() or "kimi" in model.lower():
                # Kimi: Use effort level like other models (max_tokens may not work)
                payload["reasoning"] = {
                    "effort": "low",
                    "exclude": False,
                    "summary": "auto",
                }
            else:
                # OpenAI (GPT-5, o1, o3) and Grok models use effort parameter
                payload["reasoning"] = {
                    "effort": "low",  # 20% allocation - "minimal" not supported by GPT-5.x
                    "exclude": False,
                    "summary": "auto",  # Request reasoning summaries in plaintext
                }

        # Note: max_tokens intentionally not set to prevent output truncation
        # Token usage is controlled through prompting instead

        # Retry logic
        for attempt in range(self.max_retries):
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledException("Call cancelled before attempt")

            try:
                response = requests.post(
                    self.responses_url,
                    headers=headers,
                    json=payload,
                    timeout=30
                )

                # Log error details before raising
                if response.status_code != 200:
                    try:
                        error_data = response.json()
                        logging.error(f"Responses API error {response.status_code}: {error_data}")
                    except:
                        logging.error(f"Responses API error {response.status_code}: {response.text}")

                response.raise_for_status()

                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled after response")

                data = response.json()

                # Debug: log the full response structure
                logging.info(f"Responses API response: {json.dumps(data, indent=2)}")

                if data.get("error"):
                    logging.error(f"OpenRouter API error: {data['error']}")
                    raise Exception(f"OpenRouter API error: {data['error']}")

                if not data.get("output"):
                    logging.error("OpenRouter Responses API returned no output")
                    raise Exception("OpenRouter Responses API returned no output")

                # Extract content and structured output from output array
                output = data["output"]
                content = data.get("output_text") or ""

                result = {"content": content}

                # Extract function call from output array
                for item in output:
                    if item.get("type") == "function_call":
                        arguments_str = item.get("arguments", "{}")
                        result["structured_output"] = json.loads(arguments_str)
                        break

                # Fallback: Some models (like Kimi K2) put structured output inside reasoning text
                # Look for JSON in reasoning content if no function_call found
                if "structured_output" not in result:
                    for item in output:
                        if item.get("type") == "reasoning":
                            for content_item in item.get("content", []):
                                if content_item.get("type") == "reasoning_text":
                                    text = content_item.get("text", "")
                                    # Try to extract JSON from reasoning text
                                    # Look for patterns like: {"key": "value"}
                                    try:
                                        # Find first { and last } to extract JSON
                                        start = text.find("{")
                                        end = text.rfind("}") + 1
                                        if start >= 0 and end > start:
                                            json_str = text[start:end]
                                            # Try to parse it
                                            parsed = json.loads(json_str)
                                            result["structured_output"] = parsed
                                            break
                                    except (json.JSONDecodeError, ValueError):
                                        continue
                            if "structured_output" in result:
                                break

                # Extract reasoning from output array
                # Some models expose summary, others expose full reasoning content
                reasoning_parts = []
                for item in output:
                    if item.get("type") == "reasoning":
                        logging.debug(f"Found reasoning item in output for {model}")
                        # Check summary array first (summary tokens)
                        summary = item.get("summary", [])
                        for summary_item in summary:
                            if summary_item.get("type") == "summary_text":
                                reasoning_parts.append(summary_item.get("text", ""))

                        # Also check content array (full reasoning tokens)
                        content_array = item.get("content", [])
                        logging.debug(f"Reasoning content array has {len(content_array)} items")
                        for content_item in content_array:
                            if content_item.get("type") == "reasoning_text":
                                text = content_item.get("text", "")
                                logging.debug(f"Extracting reasoning text ({len(text)} chars)")
                                reasoning_parts.append(text)

                logging.debug(f"Total reasoning parts extracted: {len(reasoning_parts)}")
                if reasoning_parts:
                    result["reasoning"] = "\n\n".join(reasoning_parts)
                    logging.info(f"Extracted reasoning from {model} ({len(result['reasoning'])} chars) via Responses API")
                else:
                    logging.debug(f"No reasoning parts found for {model} in Responses API")

                # Log token usage for cost monitoring
                if "usage" in data:
                    usage = data["usage"]
                    total_tokens = usage.get("total_tokens", 0)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    logging.info(f"Token usage for {model}: {total_tokens} total ({prompt_tokens} prompt + {completion_tokens} completion)")

                return result

            except LLMCancelledException:
                raise
            except requests.exceptions.Timeout:
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
                raise Exception(f"OpenRouter API error after {self.max_retries} attempts: {str(e)}")

        raise Exception("Failed to call OpenRouter API")

    def _supports_tools(self, model: str) -> bool:
        """Check if model uses Responses API with tools."""
        return model in TOOL_MODELS

    def _is_reasoning_model(self, model: str) -> bool:
        """Check if model supports reasoning."""
        return model in REASONING_MODELS

    def _schema_to_tool(self, schema: dict, name: str = "structured_response") -> dict:
        """Convert a JSON schema to an OpenRouter Responses API tool definition.

        Args:
            schema: JSON schema from response_format (Chat API format with json_schema wrapper)
            name: Function name for the tool

        Returns:
            Tool definition dict
        """
        # Extract the actual schema from the Chat API response_format wrapper
        # response_format has format: {"type": "json_schema", "json_schema": {"name": "...", "schema": {...}}}
        # We need just the inner schema object
        if "json_schema" in schema and "schema" in schema["json_schema"]:
            actual_schema = schema["json_schema"]["schema"]
        else:
            # Fallback: assume it's already a raw schema
            actual_schema = schema

        return {
            "type": "function",
            "name": name,
            "description": f"Provide structured response as {name}",
            "parameters": actual_schema
        }

    def _extract_reasoning(self, message: dict) -> Optional[str]:
        """Extract reasoning from message if present."""
        # Modern format: reasoning_details array
        if "reasoning_details" in message and message["reasoning_details"]:
            parts = []
            for detail in message["reasoning_details"]:
                if detail.get("type") == "reasoning.summary":
                    summary_text = detail['summary']
                    parts.append(f"[Summary] {summary_text}")
                    logging.info(f"Reasoning summary: {summary_text}")
                elif detail.get("text"):
                    parts.append(detail["text"])
            if parts:
                return "\n\n".join(parts)

        # Legacy format: direct reasoning field
        if "reasoning" in message and message["reasoning"]:
            return message["reasoning"]

        return None

    def _messages_to_input(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Convert Chat API messages format to Responses API input format.

        Chat API format:
            [{"role": "user", "content": "text"}, ...]

        Responses API format:
            [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "text"}]}, ...]
        """
        input_messages = []
        for msg in messages:
            input_msg = {
                "type": "message",
                "role": msg["role"],
                "content": [
                    {
                        "type": "input_text",
                        "text": msg["content"]
                    }
                ]
            }
            input_messages.append(input_msg)
        return input_messages
