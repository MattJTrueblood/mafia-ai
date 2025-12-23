"""OpenRouter API client for LLM interactions."""
import requests
import json
from typing import List, Dict, Optional, Any
from config import OPENROUTER_API_URL, OPENROUTER_API_KEY


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the OpenRouter client."""
        self.api_key = api_key or OPENROUTER_API_KEY
        self.base_url = OPENROUTER_API_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a chat completion request to OpenRouter.
        
        Args:
            model: Model identifier (e.g., "meta-llama/llama-3.2-3b-instruct:free")
            messages: List of message dicts with "role" and "content"
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens in response
            response_format: Format specification (e.g., {"type": "json_object"})
            tools: List of function/tool definitions for function calling
            tool_choice: Tool choice mode ("auto", "none", or specific tool)
        
        Returns:
            Response dictionary with choices, usage, etc.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        
        if response_format is not None:
            payload["response_format"] = response_format
        
        if tools is not None:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        
        try:
            response = requests.post(self.base_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_msg = f"OpenRouter API error: {e}"
            if hasattr(e, 'response') and e.response is not None:
                error_msg += f"\nResponse: {e.response.text}"
            raise Exception(error_msg)
    
    def get_text_response(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Get a simple text response from the model.
        
        Returns:
            The text content of the assistant's message.
        """
        response = self.chat_completion(model, messages, temperature, max_tokens)
        return response["choices"][0]["message"]["content"]
    
    def get_json_response(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get a JSON-formatted response from the model.
        
        Returns:
            Parsed JSON dictionary.
        """
        response_format = {"type": "json_object"}
        response = self.chat_completion(
            model, messages, temperature, max_tokens, response_format=response_format
        )
        content = response["choices"][0]["message"]["content"]
        return json.loads(content)
    
    def get_tool_call_response(
        self,
        model: str,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get a tool/function call response from the model.
        
        Args:
            tools: List of tool definitions (OpenAI function format)
            tool_choice: "auto", "none", or {"type": "function", "function": {"name": "..."}}
        
        Returns:
            Tool call dictionary with name and arguments, or None if no tool was called.
        """
        response = self.chat_completion(
            model, messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice
        )
        
        message = response["choices"][0]["message"]
        if "tool_calls" in message and message["tool_calls"]:
            tool_call = message["tool_calls"][0]
            return {
                "name": tool_call["function"]["name"],
                "arguments": json.loads(tool_call["function"]["arguments"])
            }
        return None
    
    def get_usage_stats(self, response: Dict[str, Any]) -> Dict[str, int]:
        """Extract usage statistics from a response."""
        if "usage" in response:
            return {
                "prompt_tokens": response["usage"].get("prompt_tokens", 0),
                "completion_tokens": response["usage"].get("completion_tokens", 0),
                "total_tokens": response["usage"].get("total_tokens", 0)
            }
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

