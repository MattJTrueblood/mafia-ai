"""
Win condition checking logic.

Supports compositional win conditions where multiple players can win simultaneously.
For example:
- Survivor wins if they are alive at game end (can win alongside mafia OR town)
- Executioner wins if their target was lynched (exclusive)
- Jester wins if they are lynched (exclusive, ends game)
"""

from dataclasses import dataclass
from typing import Optional, List, Callable, Tuple, Any
from .game_state import GameState, Player


@dataclass
class WinCondition:
    """
    Defines a win condition for a role or team.

    Attributes:
        name: Identifier for this win condition (e.g., "mafia", "town", "survivor")
        check: Function that takes (GameState, Player) and returns True if win condition met
        exclusive: If True, game ends when this condition is met.
                   If False, can win alongside other winners (e.g., Survivor)
        ends_game: If True, immediately ends the game when triggered.
                   Only checked at specific moments (e.g., Jester on lynch)
        priority: Resolution order (lower = checked first). Used for ordering win messages.
        message_template: Template for win message. Use {player} for player name.
    """
    name: str
    check: Callable[[GameState, Player], bool]
    exclusive: bool = True
    ends_game: bool = False
    priority: int = 50
    message_template: str = "{player} wins!"


# =============================================================================
# WIN CONDITION CHECK FUNCTIONS
# =============================================================================

def check_mafia_team_win(game_state: GameState, player: Player) -> bool:
    """Mafia team wins if mafia outnumber or equal non-mafia."""
    if player.team != "mafia":
        return False

    alive_players = game_state.get_alive_players()
    mafia_count = len([p for p in alive_players if p.team == "mafia"])
    non_mafia_count = len([p for p in alive_players if p.team != "mafia"])

    return mafia_count >= non_mafia_count and mafia_count > 0


def check_town_team_win(game_state: GameState, player: Player) -> bool:
    """Town team wins if all mafia are eliminated."""
    if player.team != "town":
        return False

    alive_players = game_state.get_alive_players()
    mafia_count = len([p for p in alive_players if p.team == "mafia"])

    return mafia_count == 0


def check_jester_win(game_state: GameState, player: Player) -> bool:
    """Jester wins if they were lynched. Checked separately during voting."""
    # This is set during voting resolution in day.py
    if player.role and player.role.name == "Jester":
        return getattr(game_state, 'winning_jester', None) == player.name
    return False


def check_survivor_win(game_state: GameState, player: Player) -> bool:
    """Survivor wins if they are alive at game end."""
    if player.role and player.role.name == "Survivor":
        return player.alive
    return False


def check_executioner_win(game_state: GameState, player: Player) -> bool:
    """Executioner wins if their target was lynched."""
    if player.role and player.role.name == "Executioner":
        # The target is stored on the role object
        target = getattr(player.role, 'target', None)
        if target:
            # Check if target was lynched (tracked in game state)
            lynched_players = getattr(game_state, 'lynched_players', [])
            return target in lynched_players
    return False


# =============================================================================
# WIN CONDITION REGISTRY
# =============================================================================

WIN_CONDITIONS: List[WinCondition] = [
    # Team-based wins (exclusive, end game)
    WinCondition(
        name="mafia",
        check=check_mafia_team_win,
        exclusive=True,
        ends_game=True,
        priority=10,
        message_template="MAFIA WINS!"
    ),
    WinCondition(
        name="town",
        check=check_town_team_win,
        exclusive=True,
        ends_game=True,
        priority=10,
        message_template="TOWN WINS!"
    ),

    # Individual wins that end game
    WinCondition(
        name="jester",
        check=check_jester_win,
        exclusive=True,
        ends_game=True,
        priority=5,  # Higher priority - Jester win supersedes other checks
        message_template="{player} (JESTER) WINS! Everyone else loses."
    ),
    WinCondition(
        name="executioner",
        check=check_executioner_win,
        exclusive=False,  # Can win alongside the team that wins
        ends_game=False,
        priority=90,  # Check after team wins
        message_template="{player} (EXECUTIONER) also wins! Their target was lynched."
    ),

    # Individual wins that don't end game (can win alongside teams)
    WinCondition(
        name="survivor",
        check=check_survivor_win,
        exclusive=False,
        ends_game=False,
        priority=100,  # Low priority - checked last
        message_template="{player} (SURVIVOR) also wins by surviving!"
    ),
]

# Map from condition name to condition for quick lookup
WIN_CONDITION_MAP = {wc.name: wc for wc in WIN_CONDITIONS}


# =============================================================================
# WIN CHECKING FUNCTIONS
# =============================================================================

def check_win_conditions(game_state: GameState) -> Optional[str]:
    """
    Check if the game has ended and who won (main game-ending check).

    This is called after each phase to see if the game should end.
    Only checks exclusive, game-ending conditions.

    Returns:
        "mafia" if mafia wins, "town" if town wins, "jester" if jester wins, etc.
        None if game continues.
    """
    alive_players = game_state.get_alive_players()

    if not alive_players:
        return None  # Shouldn't happen, but handle edge case

    # Check exclusive game-ending conditions in priority order
    sorted_conditions = sorted(
        [wc for wc in WIN_CONDITIONS if wc.exclusive and wc.ends_game],
        key=lambda wc: wc.priority
    )

    for condition in sorted_conditions:
        # Check if any player satisfies this condition
        for player in game_state.players:
            if condition.check(game_state, player):
                return condition.name

    return None


def check_all_winners(game_state: GameState) -> List[Tuple[Player, WinCondition]]:
    """
    Check all win conditions and return list of all winners.

    This is called at game end to determine all winners (including non-exclusive
    winners like Survivor who can win alongside the main winner).

    Returns:
        List of (Player, WinCondition) tuples for all winners.
    """
    winners = []

    # Sort by priority
    sorted_conditions = sorted(WIN_CONDITIONS, key=lambda wc: wc.priority)

    for condition in sorted_conditions:
        for player in game_state.players:
            if condition.check(game_state, player):
                winners.append((player, condition))

    return winners


def get_winner_messages(game_state: GameState) -> List[str]:
    """
    Get formatted win messages for all winners.

    Returns list of messages suitable for event log.
    """
    winners = check_all_winners(game_state)
    messages = []

    # Deduplicate by player (a player shouldn't win multiple ways)
    seen_players = set()

    for player, condition in winners:
        if player.name not in seen_players:
            seen_players.add(player.name)
            message = condition.message_template.format(player=player.name)
            messages.append(message)

    return messages


def register_win_condition(condition: WinCondition):
    """
    Register a new win condition.

    Use this to add custom win conditions for new roles.
    """
    WIN_CONDITIONS.append(condition)
    WIN_CONDITION_MAP[condition.name] = condition


def get_win_condition(name: str) -> Optional[WinCondition]:
    """Get a win condition by name."""
    return WIN_CONDITION_MAP.get(name)


# =============================================================================
# HELPER FUNCTIONS FOR SPECIFIC WIN TRIGGERS
# =============================================================================

def trigger_jester_win(game_state: GameState, jester_player: Player):
    """
    Trigger a Jester win. Called from voting resolution.

    Sets game state to indicate Jester won.
    """
    game_state.winner = "jester"
    game_state.winning_jester = jester_player.name


def trigger_executioner_win(game_state: GameState, executioner_player: Player):
    """
    Trigger an Executioner win when their target is lynched.

    Sets game state to indicate Executioner won.
    """
    game_state.winner = "executioner"
    game_state.winning_executioner = executioner_player.name


def record_lynch(game_state: GameState, player_name: str):
    """
    Record that a player was lynched.

    Used for Executioner win condition checking.
    """
    if not hasattr(game_state, 'lynched_players'):
        game_state.lynched_players = []
    game_state.lynched_players.append(player_name)
