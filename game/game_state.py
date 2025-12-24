"""Game state management."""

import random
import uuid
from typing import List, Dict, Optional
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
    """Manages the complete state of a Mafia game."""
    
    def __init__(self, players: List[Dict[str, str]], role_distribution: Dict[str, int] = None):
        """
        Initialize game state.
        
        Args:
            players: List of dicts with 'name' and 'model' keys
            role_distribution: Dict mapping role names to counts
        """
        self.game_id = str(uuid.uuid4())
        self.players = []
        self.phase = "night"  # Start with night phase
        self.day_number = 0  # Will be 0 for first night, 1 for first day, etc.
        self.events = []  # Unified event log with visibility
        self._event_counter = 0  # For unique event IDs
        self.vote_results = []  # List of vote results
        self.night_actions = {}  # Dict of night action results
        self.winner = None
        self.game_over = False
        
        # Create player objects
        for player_data in players:
            player = Player(player_data["name"], player_data["model"])
            self.players.append(player)
        
        # Distribute roles
        if role_distribution:
            self.distribute_roles(role_distribution)
        else:
            # Default distribution for number of players
            self.distribute_roles_default()
        
        # Add initial log entry
        self.add_event("system", f"Game started with {len(self.players)} players. Roles have been distributed.", "all")
    
    def distribute_roles_default(self):
        """Distribute roles based on player count with sensible defaults."""
        num_players = len(self.players)

        # Hand-tuned defaults
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
            # Fallback for 13+ (keep ~25% mafia)
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
        # Build list of roles to assign
        roles_to_assign = []
        for role_name, count in role_distribution.items():
            if role_name not in ROLE_CLASSES:
                continue
            for _ in range(count):
                roles_to_assign.append(ROLE_CLASSES[role_name]())
        
        # Ensure we have at least one mafia
        has_mafia = any(isinstance(r, ROLE_CLASSES["Mafia"]) for r in roles_to_assign)
        if not has_mafia and len(roles_to_assign) > 0:
            # Replace first role with mafia
            roles_to_assign[0] = ROLE_CLASSES["Mafia"]()
        
        # Shuffle and assign
        random.shuffle(roles_to_assign)
        
        # Assign roles to players
        for i, player in enumerate(self.players):
            if i < len(roles_to_assign):
                player.role = roles_to_assign[i]
                player.team = roles_to_assign[i].team
            else:
                # Default to town if not enough roles
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
                  player: str = None, priority: int = None, metadata: dict = None) -> dict:
        """
        Add an event to the unified event log.

        Args:
            event_type: Type of event (phase_change, death, discussion, vote, vote_result,
                       mafia_chat, role_action, system)
            message: The event message content
            visibility: Who can see this event:
                       "all" - everyone (game events)
                       "public" - all living players (public discussion)
                       "mafia" - only mafia members
                       "sheriff" - only sheriff
                       "doctor" - only doctor
                       "vigilante" - only vigilante
            player: Name of the player associated with this event (optional)
            priority: Priority level 1-10 for discussion messages (optional)
            metadata: Additional data like vote target, investigation result (optional)

        Returns:
            The created event dict
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
    
    def to_dict(self) -> Dict:
        """Convert game state to dictionary for JSON serialization."""
        return {
            "game_id": self.game_id,
            "phase": self.phase,
            "day_number": self.day_number,
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
            "events": self.events,  # Unified event log with visibility
            "winner": self.winner,
            "game_over": self.game_over
        }

