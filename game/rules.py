"""
Centralized game rules configuration.

All game rules and configurable parameters live here.
No more ad-hoc conditionals scattered throughout the codebase.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class GameRules:
    """
    All configurable game rules in one place.

    Defaults match the current game behavior.
    """

    # Night phase configuration
    night_role_order: List[str] = field(
        default_factory=lambda: ["Escort", "Doctor", "Sheriff", "Tracker", "Vigilante", "Medium", "Amnesiac"]
    )

    # Doctor rules
    doctor_can_protect_same_twice: bool = False  # Cannot protect same player two nights in a row
    doctor_can_self_protect: bool = True         # Can protect themselves

    # Vigilante rules
    vigilante_can_abstain: bool = True   # Can choose not to shoot
    vigilante_bullets: int = 1           # Number of kills allowed per game

    # Day 1 rules
    day1_round_robin_only: bool = True   # Day 1 discussion is round-robin (no polling)
    day1_no_lynch: bool = True          # Day 1 has no lynch vote

    # Discussion rules
    max_discussion_messages: int = 10    # Max messages before forcing vote

    # Voting rules
    require_majority_to_lynch: bool = True  # Need >50% to lynch
    allow_no_lynch: bool = True             # If no majority, nobody dies

    # Godfather rules
    godfather_requires_other_mafia: bool = False  # Only immune when other mafia alive
    godfather_single_use_immunity: bool = False   # Loses immunity after first investigation

    # Miller rules
    miller_single_use_false_positive: bool = False  # Second investigation reveals true alignment

    # Executioner rules
    executioner_becomes_on_target_death: str = "Jester"  # Role to become if target dies (not by lynch)

    # Amnesiac rules
    amnesiac_announce_remember: bool = False  # Publicly announce when amnesiac remembers a role

    # Mafia rules
    mafia_select_killer: bool = True  # Mafia explicitly selects who performs the kill (affects tracking/blocking)


# =============================================================================
# RULE HELPER FUNCTIONS
# =============================================================================

def can_doctor_protect(rules: GameRules, doctor_role, target_name: str) -> tuple[bool, str]:
    """
    Check if a doctor can protect a given target.

    Returns:
        (can_protect, reason) - reason is empty string if allowed
    """
    if not rules.doctor_can_protect_same_twice:
        if doctor_role.last_protected == target_name:
            return False, f"Cannot protect {target_name} again (protected last night)"
    return True, ""


def can_vigilante_shoot(rules: GameRules, vigilante_role) -> tuple[bool, str]:
    """
    Check if a vigilante can still shoot.

    Returns:
        (can_shoot, reason) - reason is empty string if allowed
    """
    if vigilante_role.bullet_used:
        return False, "Already used bullet"
    return True, ""


def get_majority_threshold(alive_count: int) -> int:
    """Calculate votes needed for majority (more than half)."""
    return (alive_count // 2) + 1


def is_round_robin_day(rules: GameRules, day_number: int) -> bool:
    """Check if this day uses round-robin discussion (no polling)."""
    return rules.day1_round_robin_only and day_number == 1


def is_no_lynch_day(rules: GameRules, day_number: int) -> bool:
    """Check if this day has no lynch vote."""
    return rules.day1_no_lynch and day_number == 1


def get_night_steps_for_role(role_name: str) -> List[str]:
    """
    Get the step sequence for a role's night action.

    This is used by phases.py to build the night step sequence.
    Each role follows the discuss -> act pattern.
    """
    role_lower = role_name.lower()
    return [f"{role_lower}_discuss", f"{role_lower}_act"]


def get_investigation_result(rules: GameRules, target_player, game_state) -> tuple[str, bool]:
    """
    Determine sheriff investigation result for a target.

    Handles special cases for Godfather (appears innocent) and Miller (appears guilty).

    Args:
        rules: GameRules instance
        target_player: The player being investigated
        game_state: Current game state (for checking other mafia alive)

    Returns:
        (result, immunity_consumed) where:
        - result is "mafia" or "not mafia"
        - immunity_consumed is True if a Godfather/Miller special ability was triggered
    """
    role_name = target_player.role.name if target_player.role else None
    true_result = "mafia" if target_player.team == "mafia" else "not mafia"

    # Handle Godfather - appears innocent
    if role_name == "Godfather":
        immunity_available = True

        # Rule: requires other mafia alive
        if rules.godfather_requires_other_mafia:
            other_mafia = [p for p in game_state.get_alive_players()
                          if p.team == "mafia" and p.name != target_player.name]
            if not other_mafia:
                immunity_available = False

        # Rule: single-use immunity
        if rules.godfather_single_use_immunity and target_player.role.investigation_immunity_used:
            immunity_available = False

        if immunity_available:
            return "not mafia", True  # Appears innocent, immunity was used
        else:
            return "mafia", False  # Reveals true alignment

    # Handle Miller - appears guilty
    if role_name == "Miller":
        false_positive_available = True

        # Rule: single-use false positive
        if rules.miller_single_use_false_positive and target_player.role.false_positive_used:
            false_positive_available = False

        if false_positive_available:
            return "mafia", True  # Appears guilty, false positive was triggered
        else:
            return "not mafia", False  # Reveals true alignment

    # Normal investigation - check team
    return true_result, False


# =============================================================================
# DEFAULT RULES INSTANCE
# =============================================================================

DEFAULT_RULES = GameRules()
