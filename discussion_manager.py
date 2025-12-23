"""Discussion phase manager for the Mafia game."""
import time
import heapq
from typing import List, Dict, Optional
from models import GameState, Player
from player import AIPlayer


class DiscussionManager:
    """Manages the discussion phase of the game."""
    
    def __init__(self, game_state: GameState, ai_players: List[AIPlayer], time_limit: int = 300):
        """
        Initialize the discussion manager.
        
        Args:
            game_state: Current game state
            ai_players: List of AI player instances
            time_limit: Discussion time limit in seconds
        """
        self.game_state = game_state
        self.ai_players = {p.player.player_id: p for p in ai_players}
        self.time_limit = time_limit
        self.start_time = None
        self.messages: List[Dict] = []
        self.priority_queue: List[tuple] = []  # Min-heap: (-priority, timestamp, player_id)
        self.has_spoken: set = set()  # Track who has spoken in current turn cycle
    
    def start_discussion(self) -> GameState:
        """Start the discussion phase."""
        self.start_time = time.time()
        self.messages = []
        self.priority_queue = []
        self.has_spoken = set()
        
        # Initialize priority queue with all alive players
        alive_players = self.game_state.get_alive_players()
        for player in alive_players:
            if player.player_id in self.ai_players:
                # Initial priority based on role and situation
                priority = self._get_initial_priority(player)
                heapq.heappush(
                    self.priority_queue,
                    (-priority, time.time(), player.player_id)
                )
        
        return self.game_state
    
    def get_next_speaker(self) -> Optional[AIPlayer]:
        """
        Get the next player who should speak based on priority.
        
        Returns:
            AIPlayer instance or None if discussion should end
        """
        if self._is_time_up():
            return None
        
        # If priority queue is empty, reset for new cycle
        if not self.priority_queue:
            if len(self.has_spoken) >= len(self.game_state.get_alive_players()):
                # Everyone has spoken, start new cycle
                self.has_spoken.clear()
                alive_players = self.game_state.get_alive_players()
                for player in alive_players:
                    if player.player_id in self.ai_players:
                        priority = 1.0  # Base priority for new cycle
                        heapq.heappush(
                            self.priority_queue,
                            (-priority, time.time(), player.player_id)
                        )
            else:
                # Some players haven't spoken, add them with base priority
                alive_players = self.game_state.get_alive_players()
                for player in alive_players:
                    if player.player_id not in self.has_spoken and player.player_id in self.ai_players:
                        heapq.heappush(
                            self.priority_queue,
                            (-1.0, time.time(), player.player_id)
                        )
        
        if not self.priority_queue:
            return None
        
        # Get highest priority player (lowest negative value = highest priority)
        _, _, player_id = heapq.heappop(self.priority_queue)
        
        if player_id in self.ai_players:
            return self.ai_players[player_id]
        
        return None
    
    def add_message(self, message: Dict) -> GameState:
        """
        Add a message to the discussion.
        
        Args:
            message: Dict with "content", "player_id", "player_name", "priority"
        
        Returns:
            Updated game state
        """
        self.messages.append(message)
        self.has_spoken.add(message["player_id"])
        
        # Add message to game state
        self.game_state.discussion_messages.append(message)
        
        # Update priority queue based on message content
        self._update_priorities_after_message(message)
        
        return self.game_state
    
    def _update_priorities_after_message(self, message: Dict):
        """Update priorities for other players based on a new message."""
        message_content = message.get("content", "").lower()
        speaker_id = message["player_id"]
        
        # Check if message mentions or accuses other players
        alive_players = self.game_state.get_alive_players()
        for player in alive_players:
            if player.player_id == speaker_id:
                continue
            
            if player.player_id not in self.ai_players:
                continue
            
            player_name_lower = player.name.lower()
            
            # If player is mentioned/accused, increase their priority
            if player_name_lower in message_content:
                # High priority to respond
                priority = 2.0
                if any(word in message_content for word in ["accuse", "suspicious", "mafia", "guilty", "vote", "lynch"]):
                    priority = 3.0  # Very high priority if accused
                
                heapq.heappush(
                    self.priority_queue,
                    (-priority, time.time(), player.player_id)
                )
    
    def _get_initial_priority(self, player: Player) -> float:
        """Get initial priority for a player at discussion start."""
        priority = 1.0
        
        # Mafia might want to speak early to blend in
        if player.role.value == "mafia":
            priority = 1.2
        
        # Sheriff might want to share findings (carefully)
        if player.role.value == "sheriff":
            priority = 1.1
        
        return priority
    
    def _is_time_up(self) -> bool:
        """Check if discussion time limit has been reached."""
        if self.start_time is None:
            return False
        return (time.time() - self.start_time) >= self.time_limit
    
    def get_messages(self) -> List[Dict]:
        """Get all discussion messages."""
        return self.messages
    
    def get_elapsed_time(self) -> float:
        """Get elapsed time since discussion started."""
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time
    
    def should_end_discussion(self) -> bool:
        """Check if discussion should end."""
        return self._is_time_up() or (
            len(self.priority_queue) == 0 and
            len(self.has_spoken) >= len(self.game_state.get_alive_players()) and
            len(self.messages) > 0
        )

