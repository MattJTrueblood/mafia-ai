"""Data models for the Mafia AI game."""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum


class Role(Enum):
    """Player roles in the game."""
    MAFIA = "mafia"
    TOWN = "town"
    SHERIFF = "sheriff"
    DOCTOR = "doctor"
    VIGILANTE = "vigilante"


class Phase(Enum):
    """Game phases."""
    DAY = "day"
    NIGHT = "night"
    DISCUSSION = "discussion"
    VOTING = "voting"
    GAME_OVER = "game_over"


class PlayerStatus(Enum):
    """Player status."""
    ALIVE = "alive"
    DEAD = "dead"
    LYNCHED = "lynched"
    KILLED = "killed"


@dataclass
class Player:
    """Represents a player in the game."""
    name: str
    role: Role
    model: str  # OpenRouter model identifier
    status: PlayerStatus = PlayerStatus.ALIVE
    player_id: Optional[str] = None
    
    # Game state tracking
    known_info: Dict[str, Any] = field(default_factory=dict)  # Information this player knows
    vote_history: List[Dict] = field(default_factory=list)  # History of votes cast
    action_history: List[Dict] = field(default_factory=list)  # History of night actions
    
    # Role-specific state
    has_used_vigilante_bullet: bool = False
    last_protected: Optional[str] = None  # For doctor (cannot protect same person twice in a row)
    
    def __post_init__(self):
        if self.player_id is None:
            self.player_id = f"player_{id(self)}"
    
    def is_alive(self) -> bool:
        """Check if player is alive."""
        return self.status == PlayerStatus.ALIVE
    
    def to_dict(self) -> Dict:
        """Convert player to dictionary (for JSON serialization)."""
        return {
            "name": self.name,
            "role": self.role.value if isinstance(self.role, Role) else self.role,
            "model": self.model,
            "status": self.status.value if isinstance(self.status, PlayerStatus) else self.status,
            "player_id": self.player_id,
            "is_alive": self.is_alive()
        }


@dataclass
class Vote:
    """Represents a vote cast by a player."""
    voter_id: str
    target_id: Optional[str]  # None means abstain
    explanation: str
    phase: str
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    
    def to_dict(self) -> Dict:
        """Convert vote to dictionary."""
        return {
            "voter_id": self.voter_id,
            "target_id": self.target_id,
            "explanation": self.explanation,
            "phase": self.phase,
            "timestamp": self.timestamp
        }


@dataclass
class Action:
    """Represents a night action taken by a player."""
    actor_id: str
    action_type: str  # "kill", "protect", "investigate", "vigilante_kill"
    target_id: Optional[str]  # None means abstain/no action
    result: Optional[Any] = None  # Action result (e.g., investigation result)
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    
    def to_dict(self) -> Dict:
        """Convert action to dictionary."""
        return {
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "target_id": self.target_id,
            "result": self.result,
            "timestamp": self.timestamp
        }


@dataclass
class GameState:
    """Represents the current state of the game."""
    players: List[Player] = field(default_factory=list)
    phase: Phase = Phase.NIGHT
    day_number: int = 0
    votes: List[Vote] = field(default_factory=list)
    night_actions: List[Action] = field(default_factory=list)
    game_history: List[Dict] = field(default_factory=list)
    discussion_messages: List[Dict] = field(default_factory=list)
    winner: Optional[str] = None  # "mafia" or "town"
    
    def get_alive_players(self) -> List[Player]:
        """Get all alive players."""
        return [p for p in self.players if p.is_alive()]
    
    def get_player_by_id(self, player_id: str) -> Optional[Player]:
        """Get a player by their ID."""
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None
    
    def get_players_by_role(self, role: Role) -> List[Player]:
        """Get all players with a specific role."""
        return [p for p in self.players if p.role == role]
    
    def get_alive_players_by_role(self, role: Role) -> List[Player]:
        """Get all alive players with a specific role."""
        return [p for p in self.get_alive_players() if p.role == role]
    
    def add_game_event(self, event_type: str, description: str, data: Optional[Dict] = None):
        """Add an event to game history."""
        self.game_history.append({
            "type": event_type,
            "description": description,
            "day": self.day_number,
            "phase": self.phase.value if isinstance(self.phase, Phase) else self.phase,
            "data": data or {}
        })
    
    def to_dict(self) -> Dict:
        """Convert game state to dictionary (for JSON serialization)."""
        return {
            "players": [p.to_dict() for p in self.players],
            "phase": self.phase.value if isinstance(self.phase, Phase) else self.phase,
            "day_number": self.day_number,
            "votes": [v.to_dict() for v in self.votes],
            "night_actions": [a.to_dict() for a in self.night_actions],
            "game_history": self.game_history,
            "discussion_messages": self.discussion_messages,
            "winner": self.winner,
            "alive_count": len(self.get_alive_players())
        }

