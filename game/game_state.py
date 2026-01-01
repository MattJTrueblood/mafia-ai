"""Game state management with step-based execution."""

import random
import uuid
from typing import List, Dict, Optional, Any
from .roles import Role, ROLE_CLASSES


class Player:
    """Represents a player in the game."""

    def __init__(self, name: str, model: str, role: Optional[Role] = None):
        self.name = name
        self.model = model
        self.role = role
        self.alive = True
        self.team = role.team if role else None
        self.last_llm_context = None  # Stores most recent LLM prompt/response for debugging

    def __repr__(self):
        status = "alive" if self.alive else "dead"
        return f"Player(name={self.name}, role={self.role}, {status})"


class GameState:
    """
    Manages the complete state of a Mafia game.

    Uses a step-based execution model where the game progresses through
    discrete steps. Each step is a single atomic action (usually one LLM call).
    The game can be paused between any two steps and will resume exactly
    where it left off.
    """

    # Step constants for night phase
    STEP_NIGHT_START = "night_start"
    STEP_MAFIA_DISCUSSION = "mafia_discussion"  # Mafia discuss before voting
    STEP_MAFIA_VOTE = "mafia_vote"  # Followed by index: mafia_vote:0, mafia_vote:1, etc.
    STEP_DOCTOR_DISCUSS = "doctor_discuss"  # Doctor thinks through options
    STEP_DOCTOR_ACT = "doctor_act"  # Doctor chooses target
    STEP_SHERIFF_DISCUSS = "sheriff_discuss"  # Sheriff thinks through options
    STEP_SHERIFF_ACT = "sheriff_act"  # Sheriff chooses target
    STEP_VIGILANTE_DISCUSS = "vigilante_discuss"  # Vigilante thinks through options
    STEP_VIGILANTE_ACT = "vigilante_act"  # Vigilante chooses target
    STEP_NIGHT_RESOLVE = "night_resolve"

    # Step constants for day phase
    STEP_DAY_START = "day_start"
    STEP_INTRODUCTION_MESSAGE = "introduction_message"  # Day 1 only: simple round-robin introductions
    STEP_DISCUSSION_POLL = "discussion_poll"  # Followed by round: discussion_poll:0
    STEP_DISCUSSION_MESSAGE = "discussion_message"  # discussion_message:player_name
    STEP_VOTING = "voting"  # voting:0, voting:1, etc.
    STEP_VOTING_RESOLVE = "voting_resolve"

    # Step constants for postgame phase
    STEP_POSTGAME_REVEAL = "postgame_reveal"
    STEP_POSTGAME_DISCUSSION = "postgame_discussion"
    STEP_MVP_VOTING = "mvp_voting"
    STEP_GAME_END = "game_end"

    def __init__(self, players: List[Dict[str, str]], role_distribution: Dict[str, int] = None):
        """
        Initialize game state.

        Args:
            players: List of dicts with 'name' and 'model' keys
            role_distribution: Dict mapping role names to counts
        """
        self.game_id = str(uuid.uuid4())
        self.players = []
        self.phase = "day"  # Start in day phase for introduction day
        self.day_number = 1  # Day 1 is introduction day
        self.events = []  # Unified event log with visibility
        self._event_counter = 0  # For unique event IDs
        self.winner = None
        self.game_over = False

        # Step-based execution state
        self.current_step = self.STEP_DAY_START  # Start with introduction day
        self.step_index = 0  # Sub-index within a step type (e.g., which mafia member)

        # Phase-specific accumulated data (will be initialized after role distribution)
        self.phase_data = {}

        # Create player objects
        for player_data in players:
            player = Player(player_data["name"], player_data["model"])
            self.players.append(player)

        # Distribute roles
        if role_distribution:
            self.distribute_roles(role_distribution)
        else:
            self.distribute_roles_default()

        # Add initial log entry with role counts
        role_counts = {}
        for player in self.players:
            role_name = player.role.name.lower()
            role_counts[role_name] = role_counts.get(role_name, 0) + 1

        # Build role distribution string in a sensible order
        role_order = ["mafia", "town", "sheriff", "doctor", "vigilante"]
        role_parts = []
        for role in role_order:
            if role in role_counts:
                role_parts.append(f"{role_counts[role]} {role}")
        # Add any other roles not in the standard order
        for role, count in role_counts.items():
            if role not in role_order:
                role_parts.append(f"{count} {role}")

        role_str = ", ".join(role_parts)
        self.add_event("system", f"Game started with {len(self.players)} players. Roles have been distributed: {role_str}.", "all")

        # Initialize phase_data for introduction day
        alive = self.get_alive_players()
        random.shuffle(alive)
        self.phase_data = {
            "discussion_messages": [],
            "speaker_order": [p.name for p in alive],
            "current_speaker_index": 0,
            "player_last_message_index": {},
            "last_was_respond": False,
            "votes": [],
            "round_passes": [],
        }

    def distribute_roles_default(self):
        """Distribute roles based on player count with sensible defaults."""
        num_players = len(self.players)

        if num_players == 5:
            distribution = {"Mafia": 1, "Sheriff": 1, "Town": 3}
        elif num_players == 6:
            distribution = {"Mafia": 2, "Sheriff": 1, "Town": 3}
        elif num_players == 7:
            distribution = {"Mafia": 2, "Sheriff": 1, "Doctor": 1, "Town": 3}
        elif num_players == 8:
            distribution = {"Mafia": 2, "Sheriff": 1, "Doctor": 1, "Town": 4}
        elif num_players == 9:
            distribution = {"Mafia": 2, "Sheriff": 1, "Doctor": 1, "Vigilante": 1, "Town": 4}
        elif num_players == 10:
            distribution = {"Mafia": 3, "Sheriff": 1, "Doctor": 1, "Town": 5}
        elif num_players == 11:
            distribution = {"Mafia": 3, "Sheriff": 1, "Doctor": 1, "Vigilante": 1, "Town": 5}
        elif num_players == 12:
            distribution = {"Mafia": 3, "Sheriff": 1, "Doctor": 1, "Vigilante": 1, "Town": 6}
        elif num_players <= 4:
            distribution = {"Mafia": 1, "Town": num_players - 1}
        else:
            mafia_count = max(2, round(num_players / 4))
            special_count = 3
            distribution = {
                "Mafia": mafia_count,
                "Sheriff": 1,
                "Doctor": 1,
                "Vigilante": 1,
                "Town": max(1, num_players - mafia_count - special_count),
            }

        self.distribute_roles(distribution)

    def distribute_roles(self, role_distribution: Dict[str, int]):
        """Distribute roles randomly to players."""
        roles_to_assign = []
        for role_name, count in role_distribution.items():
            if role_name not in ROLE_CLASSES:
                continue
            for _ in range(count):
                roles_to_assign.append(ROLE_CLASSES[role_name]())

        has_mafia = any(isinstance(r, ROLE_CLASSES["Mafia"]) for r in roles_to_assign)
        if not has_mafia and len(roles_to_assign) > 0:
            roles_to_assign[0] = ROLE_CLASSES["Mafia"]()

        random.shuffle(roles_to_assign)

        for i, player in enumerate(self.players):
            if i < len(roles_to_assign):
                player.role = roles_to_assign[i]
                player.team = roles_to_assign[i].team
            else:
                player.role = ROLE_CLASSES["Town"]()
                player.team = "town"

    def get_alive_players(self) -> List[Player]:
        """Get list of alive players."""
        return [p for p in self.players if p.alive]

    def get_players_by_role(self, role_name: str) -> List[Player]:
        """Get alive players with a specific role."""
        return [p for p in self.get_alive_players() if p.role and p.role.name == role_name]

    def get_player_by_name(self, name: str) -> Optional[Player]:
        """Get player by name."""
        for player in self.players:
            if player.name == name:
                return player
        return None

    def add_event(self, event_type: str, message: str, visibility: str = "all",
                  player: str = None, priority: int = None, metadata: dict = None,
                  reasoning: str = None) -> dict:
        """Add an event to the unified event log."""
        self._event_counter += 1
        event = {
            "id": self._event_counter,
            "type": event_type,
            "phase": self.phase,
            "day": self.day_number,
            "message": message,
            "player": player,
            "visibility": visibility,
            "priority": priority,
            "metadata": metadata
        }

        # Add reasoning if present
        if reasoning:
            event["reasoning"] = reasoning

        self.events.append(event)
        return event

    def kill_player(self, player_name: str, reason: str = ""):
        """Kill a player."""
        player = self.get_player_by_name(player_name)
        if player and player.alive:
            player.alive = False
            self.add_event("death", f"{player_name} has died. {reason}", "all",
                          metadata={"player": player_name, "reason": reason})
            return True
        return False

    def start_night_phase(self):
        """Initialize state for a new night phase."""
        self.phase = "night"
        self.current_step = self.STEP_NIGHT_START
        self.step_index = 0
        self.phase_data = {
            "mafia_discussion_messages": [],
            "mafia_votes": [],
            "doctor_discussion": None,
            "doctor_protection": None,
            "sheriff_discussion": None,
            "sheriff_investigation": None,
            "vigilante_discussion": None,
            "vigilante_kill": None,
            "protected_player": None,
        }

    def start_day_phase(self):
        """Initialize state for a new day phase."""
        self.phase = "day"
        self.day_number += 1
        self.current_step = self.STEP_DAY_START
        self.step_index = 0
        # Build randomized speaker order
        alive = self.get_alive_players()
        random.shuffle(alive)
        self.phase_data = {
            "discussion_messages": [],
            "speaker_order": [p.name for p in alive],
            "current_speaker_index": 0,
            "player_last_message_index": {},  # Maps player_name -> message index for recency selection
            "last_was_respond": False,  # Tracks if last message was a respond (to block respond chains)
            "votes": [],
            "round_passes": [],  # Tracks players who passed in current round - prevents infinite polling
        }

    def to_dict(self) -> Dict:
        """Convert game state to dictionary for JSON serialization."""
        return {
            "game_id": self.game_id,
            "phase": self.phase,
            "day_number": self.day_number,
            "current_step": self.current_step,
            "step_index": self.step_index,
            "players": [
                {
                    "name": p.name,
                    "model": p.model,
                    "role": p.role.name if p.role else None,
                    "alive": p.alive,
                    "team": p.team,
                    "has_context": p.last_llm_context is not None
                }
                for p in self.players
            ],
            "events": self.events,
            "winner": self.winner,
            "game_over": self.game_over
        }
