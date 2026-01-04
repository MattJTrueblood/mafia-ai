"""OpenRouter API client for LLM interactions."""

import json
import logging
import time
import requests
from typing import Dict, List, Optional, Any, Callable
from config import load_openrouter_key, TOOL_MODELS


class LLMCancelledException(Exception):
    """Raised when an LLM call is cancelled due to game pause."""
    pass


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""

    CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
    RESPONSES_URL = "https://openrouter.ai/api/v1/responses"
    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 1

    def __init__(self):
        self.api_key = load_openrouter_key()

    def call_model(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
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
            cancel_event: Optional gevent.event.Event to check for cancellation

        Returns:
            Dict with "content" and optionally "structured_output"

        Raises:
            LLMCancelledException: If cancel_event is set during the call
        """
        self._check_cancellation(cancel_event, "before starting")

        use_responses_api = self._supports_tools(model) and response_format is not None

        if use_responses_api:
            return self._call_responses_api(
                model, messages, response_format, temperature, cancel_event
            )
        else:
            return self._call_chat_api(
                model, messages, temperature, cancel_event
            )

    def _supports_tools(self, model: str) -> bool:
        """Check if model uses Responses API with tools."""
        return model in TOOL_MODELS

    def _call_chat_api(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Call Chat API (traditional completions endpoint for freeform text)."""
        payload = self._build_chat_payload(model, messages, temperature)
        response_data = self._execute_chat_request(payload, model, cancel_event)
        return self._parse_chat_response(response_data, model)

    def _build_chat_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float
    ) -> Dict[str, Any]:
        """Build Chat API request payload."""
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

    def _execute_chat_request(
        self,
        payload: Dict[str, Any],
        model: str,
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Execute Chat API HTTP request with retry logic."""
        logging.info(f"Calling Chat API: {model}")

        def api_call():
            return requests.post(
                self.CHAT_URL,
                headers=self._build_headers(),
                json=payload,
                timeout=self.DEFAULT_TIMEOUT
            )

        response = self._retry_with_cancellation(api_call, cancel_event, "Chat API")
        return response.json()

    def _parse_chat_response(self, data: Dict[str, Any], model: str) -> Dict[str, Any]:
        """Parse and validate Chat API response."""
        if data.get("error"):
            logging.error(f"OpenRouter API error: {data['error']}")
            raise Exception(f"OpenRouter API error: {data['error']}")

        if not data.get("choices"):
            logging.error("OpenRouter returned no choices")
            raise Exception("OpenRouter returned no choices")

        message = data["choices"][0]["message"]
        content = message.get("content") or ""

        if not content:
            logging.warning(f"Empty response from model {model}")

        result = {"content": content}
        return result

    def _call_responses_api(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Dict[str, Any],
        temperature: float,
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Call Responses API (tool calling endpoint for structured outputs)."""
        payload = self._build_responses_payload(model, messages, response_format, temperature)
        response_data = self._execute_responses_request(payload, model, cancel_event)
        return self._parse_responses_output(response_data, model)

    def _build_responses_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Dict[str, Any],
        temperature: float
    ) -> Dict[str, Any]:
        """Build Responses API request payload."""
        input_messages = self._messages_to_input(messages)

        payload = {
            "model": model,
            "input": input_messages,
            "temperature": temperature,
            "tools": [self._schema_to_tool(response_format)],
            "tool_choice": {"type": "function", "name": "structured_response"}
        }

        return payload

    def _execute_responses_request(
        self,
        payload: Dict[str, Any],
        model: str,
        cancel_event: Optional[Any]
    ) -> Dict[str, Any]:
        """Execute Responses API HTTP request with retry logic."""
        logging.info(f"Calling Responses API: {model}")

        def api_call():
            return requests.post(
                self.RESPONSES_URL,
                headers=self._build_headers(),
                json=payload,
                timeout=self.DEFAULT_TIMEOUT
            )

        response = self._retry_with_cancellation(api_call, cancel_event, "Responses API")
        return response.json()

    def _parse_responses_output(self, data: Dict[str, Any], model: str) -> Dict[str, Any]:
        """Parse and extract structured output from Responses API."""
        if data.get("error"):
            logging.error(f"OpenRouter API error: {data['error']}")
            raise Exception(f"OpenRouter API error: {data['error']}")

        if not data.get("output"):
            logging.error("OpenRouter Responses API returned no output")
            raise Exception("OpenRouter Responses API returned no output")

        output = data["output"]
        content = data.get("output_text") or ""

        result = {"content": content}

        for item in output:
            if item.get("type") == "function_call":
                arguments_str = item.get("arguments", "{}")
                try:
                    result["structured_output"] = json.loads(arguments_str)
                except json.JSONDecodeError as e:
                    logging.error(f"Failed to parse structured output JSON for {model}: {e}")
                    logging.error(f"Raw arguments: {arguments_str}")
                break

        return result

    def _build_headers(self) -> Dict[str, str]:
        """Build common HTTP headers for OpenRouter API."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/mafia-ai",
            "X-Title": "Mafia AI Game",
        }

    def _check_cancellation(self, cancel_event: Optional[Any], context: str) -> None:
        """Check if operation has been cancelled."""
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledException(f"Call cancelled {context}")

    def _retry_with_cancellation(
        self,
        api_call: Callable[[], requests.Response],
        cancel_event: Optional[Any],
        api_name: str
    ) -> requests.Response:
        """Execute API call with retry logic and cancellation support."""
        for attempt in range(self.MAX_RETRIES):
            self._check_cancellation(cancel_event, "before attempt")

            try:
                response = api_call()

                if response.status_code != 200:
                    self._log_api_error(response, api_name)

                response.raise_for_status()
                self._check_cancellation(cancel_event, "after response")
                return response

            except LLMCancelledException:
                raise
            except requests.exceptions.Timeout:
                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled during timeout")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.BASE_RETRY_DELAY * (attempt + 1))
                    continue
                raise Exception(f"{api_name} timeout after {self.MAX_RETRIES} attempts")
            except requests.exceptions.RequestException as e:
                if cancel_event and cancel_event.is_set():
                    raise LLMCancelledException("Call cancelled during error")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.BASE_RETRY_DELAY * (attempt + 1))
                    continue
                raise Exception(f"{api_name} error after {self.MAX_RETRIES} attempts: {str(e)}")

        raise Exception(f"Failed to call {api_name}")

    def _log_api_error(self, response: requests.Response, api_name: str) -> None:
        """Log API error details."""
        try:
            error_data = response.json()
            logging.error(f"{api_name} error {response.status_code}: {error_data}")
        except Exception:
            logging.error(f"{api_name} error {response.status_code}: {response.text}")

    def _schema_to_tool(self, schema: dict, name: str = "structured_response") -> dict:
        """Convert a JSON schema to an OpenRouter Responses API tool definition."""
        if "json_schema" in schema and "schema" in schema["json_schema"]:
            actual_schema = schema["json_schema"]["schema"]
        else:
            actual_schema = schema

        return {
            "type": "function",
            "name": name,
            "description": f"Provide structured response as {name}",
            "parameters": actual_schema
        }

    def _messages_to_input(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Convert Chat API messages format to Responses API input format."""
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
