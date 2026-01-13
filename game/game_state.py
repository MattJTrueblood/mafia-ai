"""Game state management with step-based execution."""

import random
import uuid
from typing import List, Dict, Optional, Any, Union
from .roles import Role, ROLE_CLASSES


class Player:
    """Represents a player in the game."""

    def __init__(self, name: str, model: str, role: Optional[Role] = None, is_human: bool = False):
        self.name = name
        self.model = model
        self.role = role
        self.alive = True
        self.team = role.team if role else None
        self.is_human = is_human  # True if this is a human player
        self.last_llm_context = None  # Stores most recent LLM prompt/response for debugging
        self.scratchpad = []  # Private strategic notes written at key decision points

    def __repr__(self):
        status = "alive" if self.alive else "dead"
        human_tag = " (HUMAN)" if self.is_human else ""
        return f"Player(name={self.name}, role={self.role}, {status}{human_tag})"


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
    STEP_SCRATCHPAD_NIGHT_START = "scratchpad_night_start"  # Special role players write strategic notes
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
    STEP_SCRATCHPAD_DAY_START = "scratchpad_day_start"  # Players write strategic notes at day start
    STEP_INTRODUCTION_MESSAGE = "introduction_message"  # Day 1 only: simple round-robin introductions
    STEP_DISCUSSION_POLL = "discussion_poll"  # Followed by round: discussion_poll:0
    STEP_DISCUSSION_MESSAGE = "discussion_message"  # discussion_message:player_name
    STEP_SCRATCHPAD_PRE_VOTE = "scratchpad_pre_vote"  # Players write strategic notes before voting
    STEP_VOTING = "voting"  # voting:0, voting:1, etc.
    STEP_VOTING_RESOLVE = "voting_resolve"

    # Step constants for postgame phase
    STEP_POSTGAME_REVEAL = "postgame_reveal"
    STEP_POSTGAME_DISCUSSION = "postgame_discussion"
    STEP_MVP_VOTING = "mvp_voting"
    STEP_GAME_END = "game_end"

    def __init__(self, players: List[Dict[str, str]], role_distribution: Dict[str, int],
                 human_player_name: Optional[str] = None, forced_role: Optional[str] = None):
        """
        Initialize game state.

        Args:
            players: List of dicts with 'name' and 'model' keys
            role_distribution: Dict mapping role names to counts (from UI presets)
            human_player_name: Optional name of the human player
            forced_role: Optional role to force-assign to the human player
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

        # Human player state
        self.human_player_name = human_player_name
        self.forced_role = forced_role
        self.reveal_all = False  # Toggle for testing - reveals all info
        self.waiting_for_human = False  # True when waiting for human input
        self.human_input_type = None  # "discussion", "vote", "mafia_vote", "role_action", etc.
        self.human_input_context = {}  # Options/metadata for current input request
        self.human_interrupt_requested = False  # True when human wants to speak in day discussion

        # Create player objects
        for player_data in players:
            is_human = human_player_name and player_data["name"] == human_player_name
            player = Player(player_data["name"], player_data["model"], is_human=is_human)
            self.players.append(player)

        # Distribute roles
        self.distribute_roles(role_distribution)

        # Add initial log entry with role counts
        role_counts = {}
        for player in self.players:
            role_name = player.role.name.lower()
            role_counts[role_name] = role_counts.get(role_name, 0) + 1

        # Build role distribution string in a sensible order
        role_order = ["mafia", "villager", "sheriff", "doctor", "vigilante"]
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

    def distribute_roles(self, role_distribution: Dict[str, int]):
        """Distribute roles randomly to players, with optional forced role for human player."""
        roles_to_assign = []
        for role_name, count in role_distribution.items():
            if role_name not in ROLE_CLASSES:
                continue
            for _ in range(count):
                roles_to_assign.append(ROLE_CLASSES[role_name]())

        has_mafia = any(isinstance(r, ROLE_CLASSES["Mafia"]) for r in roles_to_assign)
        if not has_mafia and len(roles_to_assign) > 0:
            roles_to_assign[0] = ROLE_CLASSES["Mafia"]()

        # Handle forced role for human player
        human_role = None
        if self.forced_role and self.human_player_name and self.forced_role in ROLE_CLASSES:
            # Find and remove one instance of the forced role from the pool
            for i, role in enumerate(roles_to_assign):
                if role.name == self.forced_role:
                    human_role = roles_to_assign.pop(i)
                    break

        random.shuffle(roles_to_assign)

        for player in self.players:
            # Assign forced role to human player
            if player.is_human and human_role:
                player.role = human_role
                player.team = human_role.team
            elif roles_to_assign:
                role = roles_to_assign.pop()
                player.role = role
                player.team = role.team
            else:
                player.role = ROLE_CLASSES["Villager"]()
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

    def get_human_player(self) -> Optional[Player]:
        """Get the human player if one exists."""
        if self.human_player_name:
            return self.get_player_by_name(self.human_player_name)
        return None

    def is_human_alive(self) -> bool:
        """Check if human player is still alive."""
        human = self.get_human_player()
        return human is not None and human.alive

    def should_auto_reveal(self) -> bool:
        """Check if visibility should be automatically revealed (human dead or game over)."""
        if self.game_over:
            return True
        human = self.get_human_player()
        if human and not human.alive:
            return True
        return False

    def has_human_player(self) -> bool:
        """Check if this game has a human player."""
        return self.human_player_name is not None

    def set_waiting_for_human(self, input_type: str, context: dict = None):
        """Set state to wait for human input."""
        self.waiting_for_human = True
        self.human_input_type = input_type
        self.human_input_context = context or {}

    def clear_waiting_for_human(self):
        """Clear human input waiting state."""
        self.waiting_for_human = False
        self.human_input_type = None
        self.human_input_context = {}

    def add_event(self, event_type: str, message: str, visibility: Union[str, List[str]] = "all",
                  player: str = None, priority: int = None, metadata: dict = None) -> dict:
        """Add an event to the unified event log.

        Args:
            visibility: "all", "public", or a list of player names who can see this event
        """
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
            "protected_players": [],      # List of players protected by doctors
            "vigilante_kills": [],        # List of vigilante kill targets
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

    def to_dict(self, for_human: bool = False) -> Dict:
        """Convert game state to dictionary for JSON serialization.

        Args:
            for_human: If True, filter based on human player visibility rules
        """
        human_player = self.get_human_player() if for_human else None
        # Should we hide info? Only if for_human mode, human exists, human is alive, and reveal_all is off
        should_hide = (for_human and human_player and human_player.alive
                       and not self.reveal_all and not self.should_auto_reveal())

        players_data = []
        for p in self.players:
            player_dict = {
                "name": p.name,
                "model": p.model,
                "alive": p.alive,
                "has_context": p.last_llm_context is not None,
                "has_scratchpad": hasattr(p, 'scratchpad') and len(p.scratchpad) > 0,
                "is_human": p.is_human,
            }

            # Visibility logic for roles
            if should_hide:
                if p.name == human_player.name:
                    # Human sees their own role
                    player_dict["role"] = p.role.name if p.role else None
                    player_dict["team"] = p.team
                elif human_player.team == "mafia" and p.team == "mafia":
                    # Mafia sees fellow mafia
                    player_dict["role"] = p.role.name if p.role else None
                    player_dict["team"] = p.team
                else:
                    # Hide other roles
                    player_dict["role"] = "???"
                    player_dict["team"] = "unknown"
            else:
                player_dict["role"] = p.role.name if p.role else None
                player_dict["team"] = p.team

            players_data.append(player_dict)

        # Filter events based on visibility
        if should_hide:
            visible_events = self._filter_events_for_human(human_player)
        else:
            visible_events = self.events

        return {
            "game_id": self.game_id,
            "phase": self.phase,
            "day_number": self.day_number,
            "current_step": self.current_step,
            "step_index": self.step_index,
            "players": players_data,
            "events": visible_events,
            "winner": self.winner,
            "game_over": self.game_over,
            # Human player state
            "human_player_name": self.human_player_name,
            "waiting_for_human": self.waiting_for_human,
            "human_input_type": self.human_input_type,
            "human_input_context": self.human_input_context,
            "reveal_all": self.reveal_all,
            "human_interrupt_requested": self.human_interrupt_requested,
        }

    def _filter_events_for_human(self, human_player: Player) -> List[Dict]:
        """Filter events based on human player's visibility."""
        if not human_player:
            return self.events

        visible = []
        for event in self.events:
            visibility = event.get("visibility", "all")

            if visibility in ("all", "public"):
                visible.append(event)
            elif isinstance(visibility, list) and human_player.name in visibility:
                visible.append(event)
            # Events with other visibility values are hidden from human

        return visible
