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
