"""
Unified LLM calling module.

This is THE single place for:
- LLM calls with status emission
- Response parsing (structured output + JSON fallback)
- Context tracking for debugging
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable


def call_llm(
    player,
    llm_client,
    messages: List[Dict[str, str]],
    action_type: str,
    game_state,
    response_format: Optional[Dict] = None,
    temperature: float = 0.7,
    cancel_event=None,
    emit_player_status: Callable = None,
) -> Dict[str, Any]:
    """
    Make an LLM call with status emission and context tracking.

    This is the ONLY function that should call llm_client.call_model().

    Args:
        player: Player making the call
        llm_client: OpenRouterClient instance
        messages: Message list for the LLM
        action_type: Type of action (for logging/context)
        game_state: Current game state
        response_format: Optional structured output schema
        temperature: LLM temperature
        cancel_event: Optional cancellation event
        emit_player_status: Optional callback for UI status

    Returns:
        Raw response dict from LLM
    """
    # Store context before call
    player.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": action_type,
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        response = llm_client.call_model(
            player.model,
            messages,
            response_format=response_format,
            temperature=temperature,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response
        return response
    finally:
        if emit_player_status:
            emit_player_status(player.name, "complete")


# =============================================================================
# RESPONSE PARSERS
# =============================================================================

def parse_target(response: Dict, allow_abstain: bool = True) -> Optional[str]:
    """
    Parse a target from an LLM response.

    Handles both structured_output and JSON-in-content fallback.

    Args:
        response: Raw LLM response dict
        allow_abstain: If True, ABSTAIN returns None; if False, ABSTAIN is invalid

    Returns:
        Target name or None
    """
    target = None

    if "structured_output" in response:
        target = response["structured_output"].get("target")
        logging.debug(f"Parsed target from structured_output: {repr(target)}")
    else:
        target = _parse_json_field(response, "target")

    # Convert ABSTAIN to None
    if target == "ABSTAIN":
        target = None if allow_abstain else None

    return target


def parse_vote(response: Dict) -> tuple[str, str]:
    """
    Parse a vote response (vote + explanation).

    Returns:
        Tuple of (vote_target, explanation)
    """
    vote_target = "abstain"
    explanation = ""

    if "structured_output" in response:
        vote_target = response["structured_output"].get("vote", "abstain")
        explanation = response["structured_output"].get("explanation", "")
    else:
        parsed = _try_parse_json(response)
        if parsed:
            vote_target = parsed.get("vote", "abstain")
            explanation = parsed.get("explanation", "")

    return vote_target, explanation


def parse_mvp_vote(response: Dict) -> tuple[Optional[str], str]:
    """
    Parse an MVP vote response (target + reason).

    Returns:
        Tuple of (target, reason)
    """
    target = None
    reason = ""

    if "structured_output" in response:
        target = response["structured_output"].get("target")
        reason = response["structured_output"].get("reason", "")
    else:
        parsed = _try_parse_json(response)
        if parsed:
            target = parsed.get("target")
            reason = parsed.get("reason", "")

    return target, reason


def parse_turn_poll(response: Dict) -> tuple[bool, bool, bool]:
    """
    Parse a turn poll response.

    Returns:
        Tuple of (wants_interrupt, wants_respond, wants_pass)
    """
    wants_interrupt = False
    wants_respond = False
    wants_pass = True  # Default to pass

    if "structured_output" in response:
        wants_interrupt = response["structured_output"].get("wants_to_interrupt", False)
        wants_respond = response["structured_output"].get("wants_to_respond", False)
        wants_pass = response["structured_output"].get("wants_to_pass", False)
    else:
        parsed = _try_parse_json(response)
        if parsed:
            wants_interrupt = parsed.get("wants_to_interrupt", False)
            wants_respond = parsed.get("wants_to_respond", False)
            wants_pass = parsed.get("wants_to_pass", False)

    return wants_interrupt, wants_respond, wants_pass


def parse_text(response: Dict, player_name: str = None, max_length: int = 2000) -> str:
    """
    Parse a text response (discussion, scratchpad, etc.).

    Args:
        response: Raw LLM response
        player_name: Optional player name to strip from prefix
        max_length: Maximum length of returned text

    Returns:
        Cleaned text content
    """
    content = response.get("content", "").strip()
    content = _strip_quotes(content)
    if player_name:
        content = _strip_player_name_prefix(content, player_name)
    return content[:max_length] if content else ""


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _try_parse_json(response: Dict) -> Optional[Dict]:
    """Try to parse JSON from response content. Returns None if parsing fails."""
    try:
        content = response.get("content", "")
        idx = content.find("{")
        if idx >= 0:
            return json.loads(content[idx:content.rfind("}")+1])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logging.error(f"JSON parse failed: {e}")
    return None


def _parse_json_field(response: Dict, field: str) -> Optional[str]:
    """Parse a single field from JSON in response content."""
    parsed = _try_parse_json(response)
    if parsed:
        value = parsed.get(field)
        logging.debug(f"Parsed {field} from content JSON: {repr(value)}")
        return value
    else:
        content = response.get("content", "")[:200]
        logging.warning(f"No JSON found in response content: {content}")
    return None


def _strip_quotes(text: str) -> str:
    """Strip surrounding quotation marks from text if present."""
    if not text:
        return text
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def _strip_player_name_prefix(text: str, player_name: str) -> str:
    """Strip player name prefix from text if present (e.g., 'Frank: message')."""
    if not text or not player_name:
        return text
    prefix = f"{player_name}: "
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


# =============================================================================
# SCHEMAS
# =============================================================================

def build_target_schema(available_targets: List[str], allow_abstain: bool = True) -> dict:
    """
    Build a JSON schema with an enum of valid targets.

    Args:
        available_targets: List of valid target names
        allow_abstain: Whether to include ABSTAIN as an option

    Returns:
        JSON schema dict
    """
    enum_values = list(available_targets)
    if allow_abstain:
        enum_values.append("ABSTAIN")

    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": enum_values
            }
        },
        "required": ["target"],
        "additionalProperties": False
    }


VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "vote": {"type": "string"},
        "explanation": {"type": "string"}
    },
    "required": ["vote", "explanation"]
}

MVP_VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "reason": {"type": "string"}
    },
    "required": ["target", "reason"]
}

TURN_POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "wants_to_interrupt": {"type": "boolean"},
        "wants_to_respond": {"type": "boolean"},
        "wants_to_pass": {"type": "boolean"}
    },
    "required": ["wants_to_interrupt", "wants_to_respond", "wants_to_pass"]
}
