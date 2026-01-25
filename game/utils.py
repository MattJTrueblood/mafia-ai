"""
Shared utility functions for step handlers.

Consolidates common patterns used across day, night, and postgame handlers.
"""

import random
import logging
import gevent
from gevent import Greenlet
from datetime import datetime
from typing import List, Optional, Callable, Any

from llm.prompts import build_scratchpad_prompt
from .llm_caller import call_llm, parse_text


def execute_parallel(players: List, func: Callable, ctx: Any) -> List:
    """
    Execute a function for multiple players in parallel using gevent.

    Args:
        players: List of Player objects to process
        func: Function that takes a player and returns a result
        ctx: StepContext for cancellation checking

    Returns:
        List of non-None results from all players
    """
    results = []
    greenlets = []

    for player in players:
        def worker(p=player):
            if ctx.is_cancelled():
                return None
            result = func(p)
            return result

        g = Greenlet(worker)
        greenlets.append(g)

    for g in greenlets:
        g.start()

    gevent.joinall(greenlets, raise_error=True)

    for g in greenlets:
        if g.value is not None:
            results.append(g.value)

    return results


def execute_scratchpad_writing(ctx: Any, player: Any, timing: str) -> Optional[str]:
    """
    Execute scratchpad writing for a single player.

    Args:
        ctx: StepContext with game state and LLM client
        player: Player object to write scratchpad for
        timing: Timing identifier (e.g., "day_start", "pre_vote", "night_start")

    Returns:
        The note content if successful, None otherwise
    """
    prompt = build_scratchpad_prompt(ctx.game_state, player, timing)
    messages = [{"role": "user", "content": prompt}]

    response = call_llm(
        player, ctx.llm_client, messages, f"scratchpad_{timing}", ctx.game_state,
        temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
    )

    note = parse_text(response, player.name)

    if note:
        player.scratchpad.append({
            "day": ctx.day_number,
            "phase": ctx.phase,
            "timing": timing,
            "note": note,
            "timestamp": datetime.now().isoformat()
        })

    return note


def select_speaker_by_recency(candidates: List[str], game_state: Any) -> Optional[str]:
    """
    Select the candidate whose last message was least recent.

    Used for fair turn distribution in discussion phases.

    Args:
        candidates: List of player names to choose from
        game_state: GameState object with phase_data containing message indices

    Returns:
        Selected player name, or None if candidates is empty
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    last_indices = game_state.phase_data.get("player_last_message_index", {})

    def recency_key(name):
        return last_indices.get(name, -1)

    min_index = min(recency_key(c) for c in candidates)
    tied = [c for c in candidates if recency_key(c) == min_index]
    return random.choice(tied)


def wait_for_human_input(ctx: Any, input_type: str, context: dict = None) -> Optional[dict]:
    """
    Wait for human player input with proper state management.

    Handles the common pattern of:
    1. Set waiting state
    2. Emit game state update
    3. Yield for socket
    4. Wait for input
    5. Clear waiting state

    Args:
        ctx: StepContext with game state and callbacks
        input_type: Type of input expected ("discussion", "vote", "role_action", "mvp_vote")
        context: Optional context dict with options/metadata for the input

    Returns:
        Human input dict if received, None otherwise
    """
    ctx.game_state.set_waiting_for_human(input_type, context or {})

    if ctx.emit_game_state:
        ctx.emit_game_state()

    gevent.sleep(0.05)  # Yield to allow socket to send before blocking

    human_input = ctx.wait_for_human() if ctx.wait_for_human else None

    ctx.game_state.clear_waiting_for_human()

    return human_input


def generate_vote_summary(game_state: Any, day_number: int) -> str:
    """
    Generate a structured vote summary for a day (no LLM needed).

    Parses vote events and formats them as a concise summary showing
    who voted whom and the outcome.

    Args:
        game_state: GameState object with events
        day_number: The day number to summarize votes for

    Returns:
        Formatted vote summary string
    """
    # Find vote events for this day
    vote_events = []
    death_event = None
    role_reveal = None

    for event in game_state.events:
        if event.get("day") != day_number or event.get("phase") != "day":
            continue

        event_type = event.get("type")
        if event_type == "vote":
            metadata = event.get("metadata", {})
            voter = event.get("player")
            target = metadata.get("target", "abstain")
            if voter:
                vote_events.append({"voter": voter, "target": target})

        elif event_type == "death":
            metadata = event.get("metadata", {})
            death_event = metadata.get("player")

        elif event_type == "system":
            message = event.get("message", "")
            # Check for role reveal (e.g., "Alice was MAFIA.")
            if " was MAFIA" in message or " was TOWN" in message:
                role_reveal = message

    if not vote_events:
        return "No votes cast."

    # Group votes by target
    votes_by_target = {}
    for v in vote_events:
        target = v["target"]
        if target not in votes_by_target:
            votes_by_target[target] = []
        votes_by_target[target].append(v["voter"])

    # Build summary lines
    lines = []
    for target, voters in sorted(votes_by_target.items(), key=lambda x: -len(x[1])):
        if target == "abstain":
            lines.append(f"{', '.join(voters)} abstained")
        else:
            lines.append(f"{', '.join(voters)} voted {target}")

    # Add outcome
    if death_event:
        lines.append(f"{death_event} was lynched")
        if role_reveal:
            # Extract just the role part
            lines.append(role_reveal)
    else:
        lines.append("No one was lynched")

    return "\n".join(lines)


def generate_night_summary(game_state: Any, day_number: int, player: Any) -> str:
    """
    Generate a summary of night events visible to a specific player.

    Args:
        game_state: GameState object with events
        day_number: The day number (night N-1 events belong to day N-1's summary)
        player: The player to generate the summary for

    Returns:
        Formatted night summary string, or empty string if no visible events
    """
    from llm.prompts import get_visible_events, format_event_for_prompt

    visible_events = get_visible_events(game_state, player)

    # Filter to night events for this day
    night_events = []
    for event in visible_events:
        if event.get("day") == day_number and event.get("phase") == "night":
            formatted = format_event_for_prompt(event)
            night_events.append(formatted)

    if not night_events:
        return ""

    return "Night events:\n" + "\n".join(f"- {e}" for e in night_events)


def execute_group_discussion(
    ctx: Any,
    player: Any,
    group_name: str,
    previous_messages: List,
    prompt_builder: Callable,
    action_type: str,
    temperature: float = 0.8
) -> str:
    """
    Execute a private group discussion message for a player.

    Used for both Mafia and Mason night discussions.

    Args:
        ctx: StepContext with game state and LLM client
        player: Player object speaking
        group_name: Name of the group (for logging)
        previous_messages: List of previous discussion messages
        prompt_builder: Function that builds the prompt (takes game_state, player, previous_messages)
        action_type: Action type for LLM logging
        temperature: LLM temperature setting

    Returns:
        The discussion message content
    """
    from .llm_caller import call_llm, parse_text

    prompt = prompt_builder(ctx.game_state, player, previous_messages)
    messages = [{"role": "user", "content": prompt}]

    response = call_llm(
        player, ctx.llm_client, messages, action_type, ctx.game_state,
        temperature=temperature, cancel_event=ctx.cancel_event,
        emit_player_status=ctx.emit_player_status
    )

    content = parse_text(response, player.name, max_length=1000)
    return content if content else "No comment."
