"""
Declarative phase composition.

Defines the sequence of steps for each game phase.
Adding a new role's night actions is automatic if the role
declares its `night_steps` attribute.
"""

from typing import List, Tuple
from .roles import ROLE_CLASSES


# =============================================================================
# STEP SEQUENCES
# =============================================================================

def get_night_steps(game_state, rules) -> List[str]:
    """
    Build the complete sequence of night steps.

    Automatically includes steps for any role that declares `night_steps`.
    """
    steps = [
        "night_start",
        "scratchpad_night_start",
        "mafia_discussion",
        "mafia_vote",
    ]

    # Add mason discussion if there are multiple masons
    mason_players = game_state.get_players_by_role("Mason")
    if len(mason_players) >= 2:
        steps.append("mason_discussion")

    # Add steps for each night-active role in order
    for role_name in rules.night_role_order:
        if game_state.get_players_by_role(role_name):
            role_class = ROLE_CLASSES.get(role_name)
            if role_class and hasattr(role_class, 'night_steps'):
                steps.extend(role_class.night_steps)
            else:
                # Default pattern: role_discuss, role_act
                role_lower = role_name.lower()
                steps.extend([f"{role_lower}_discuss", f"{role_lower}_act"])

    steps.append("night_resolve")
    return steps


def get_day_steps(game_state, rules) -> List[str]:
    """
    Build the complete sequence of day steps.

    Controlled by two rules:
    - day1_round_robin_only: Day 1 uses round-robin intros instead of polling
    - day1_no_lynch: Day 1 has no lynch vote
    """
    from .rules import is_round_robin_day, is_no_lynch_day

    steps = ["day_start"]

    # Discussion phase: round-robin or polling
    if is_round_robin_day(rules, game_state.day_number):
        steps.append("introduction_message")
    else:
        steps.extend([
            "scratchpad_day_start",
            "discussion_poll",
            "discussion_message",
        ])

    # Voting phase (unless no-lynch day)
    if not is_no_lynch_day(rules, game_state.day_number):
        steps.extend([
            "scratchpad_pre_vote",
            "voting",
            "voting_resolve",
        ])

    return steps


def get_postgame_steps() -> List[str]:
    """Steps for the postgame phase."""
    return [
        "postgame_reveal",
        "postgame_discussion",
        "mvp_voting",
        "game_end",
    ]


# =============================================================================
# STEP ADVANCEMENT
# =============================================================================

def get_next_step(game_state, rules) -> Tuple[str, int]:
    """
    Determine the next step based on current position.

    Returns:
        (next_step_name, next_step_index)
    """
    current = game_state.current_step
    idx = game_state.step_index

    # Get the step sequence for current phase
    if game_state.phase == "night":
        steps = get_night_steps(game_state, rules)
    elif game_state.phase == "day":
        steps = get_day_steps(game_state, rules)
    elif game_state.phase == "postgame":
        steps = get_postgame_steps()
    else:
        raise ValueError(f"Unknown phase: {game_state.phase}")

    # Find current step in sequence
    try:
        current_idx = steps.index(current)
    except ValueError:
        # Current step not in sequence (shouldn't happen normally)
        return steps[0], 0

    # Move to next step
    next_idx = current_idx + 1
    if next_idx < len(steps):
        return steps[next_idx], 0

    # Reached end of phase - transition
    return get_phase_transition(game_state, rules)


def get_phase_transition(game_state, rules) -> Tuple[str, int]:
    """
    Handle phase transitions (night->day, day->night, etc).

    Returns:
        (first_step_of_next_phase, 0)
    """
    if game_state.phase == "night":
        # Night -> Day
        return "day_start", 0

    elif game_state.phase == "day":
        # Day -> Night
        return "night_start", 0

    elif game_state.phase == "postgame":
        # Postgame ends at game_end
        return "game_end", 0

    return "day_start", 0


# =============================================================================
# STEP TYPE HELPERS
# =============================================================================

def is_multi_player_step(step_name: str) -> bool:
    """
    Check if a step iterates over multiple players.

    Multi-player steps use step_index to track progress.
    """
    return step_name in {
        "mafia_vote",
        "voting",
        "introduction_message",
        "scratchpad_night_start",
        "scratchpad_day_start",
        "scratchpad_pre_vote",
        "mvp_voting",
    }


def get_step_players(step_name: str, game_state, rules) -> List[str]:
    """
    Get the list of players involved in a multi-player step.

    Returns list of player names in execution order.
    """
    if step_name == "mafia_vote":
        return [p.name for p in game_state.get_players_by_role("Mafia")]

    elif step_name == "voting":
        return [p.name for p in game_state.get_alive_players()]

    elif step_name == "introduction_message":
        return game_state.phase_data.get("speaker_order", [])

    elif step_name in {"scratchpad_night_start", "scratchpad_day_start", "scratchpad_pre_vote"}:
        # Only special roles write scratchpad at night
        if "night" in step_name:
            special_roles = {"Mafia", "Sheriff", "Doctor", "Vigilante"}
            return [p.name for p in game_state.get_alive_players()
                    if p.role and p.role.name in special_roles]
        else:
            return [p.name for p in game_state.get_alive_players()]

    elif step_name == "mvp_voting":
        return [p.name for p in game_state.players]  # All players, including dead

    return []
