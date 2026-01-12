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
        default_factory=lambda: ["Doctor", "Sheriff", "Vigilante"]
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


# =============================================================================
# DEFAULT RULES INSTANCE
# =============================================================================

DEFAULT_RULES = GameRules()
