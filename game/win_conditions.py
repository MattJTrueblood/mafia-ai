"""Win condition checking logic."""

from typing import Optional
from .game_state import GameState


def check_win_conditions(game_state: GameState) -> Optional[str]:
    """
    Check if the game has ended and who won.

    Returns:
        "mafia" if mafia wins, "town" if town wins, None if game continues
    """
    alive_players = game_state.get_alive_players()

    if not alive_players:
        return None  # Shouldn't happen, but handle edge case

    # Count teams - third_party (Jester) counts as non-mafia
    mafia_count = len([p for p in alive_players if p.team == "mafia"])
    non_mafia_count = len([p for p in alive_players if p.team != "mafia"])

    # Mafia wins if they outnumber or equal all non-mafia (including Jesters)
    if mafia_count >= non_mafia_count and mafia_count > 0:
        return "mafia"

    # Town wins if all mafia are eliminated
    if mafia_count == 0:
        return "town"

    # Game continues
    return None
