"""Core game engine for the Mafia AI game."""
import random
from typing import List, Dict, Optional, Tuple
from models import GameState, Player, Role, Phase, PlayerStatus, Vote, Action
from config import ROLES


class GameEngine:
    """Manages the core game logic and state."""
    
    def __init__(self):
        """Initialize the game engine."""
        self.state = GameState()
        self.is_running = False
    
    def start_game(
        self,
        player_configs: List[Dict[str, str]],
        roles: Optional[List[Role]] = None
    ) -> GameState:
        """
        Initialize and start a new game.
        
        Args:
            player_configs: List of dicts with "name" and "model" keys
            roles: Optional list of roles to assign. If None, roles are auto-assigned.
        
        Returns:
            The initialized game state.
        """
        if self.is_running:
            raise ValueError("Game is already running")
        
        # Create players
        players = []
        for i, config in enumerate(player_configs):
            player = Player(
                name=config["name"],
                role=Role.TOWN,  # Will be assigned below
                model=config["model"],
                player_id=f"player_{i}"
            )
            players.append(player)
        
        # Assign roles
        if roles is None:
            roles = self._assign_roles(len(players))
        
        if len(roles) != len(players):
            raise ValueError(f"Number of roles ({len(roles)}) must match number of players ({len(players)})")
        
        # Shuffle and assign roles
        random.shuffle(roles)
        for player, role in zip(players, roles):
            player.role = role
            # Initialize role-specific state
            if role == Role.VIGILANTE:
                player.has_used_vigilante_bullet = False
            if role == Role.DOCTOR:
                player.last_protected = None
        
        # Initialize game state
        self.state = GameState(players=players, phase=Phase.NIGHT, day_number=0)
        self.is_running = True
        
        self.state.add_game_event(
            "game_start",
            f"Game started with {len(players)} players",
            {"player_count": len(players)}
        )
        
        return self.state
    
    def _assign_roles(self, player_count: int) -> List[Role]:
        """
        Automatically assign roles based on player count.
        
        Role distribution:
        - 5 players: 2 Mafia, 1 Sheriff, 1 Doctor, 1 Town
        - 6 players: 2 Mafia, 1 Sheriff, 1 Doctor, 1 Vigilante, 1 Town
        - 7 players: 2 Mafia, 1 Sheriff, 1 Doctor, 1 Vigilante, 2 Town
        - 8+ players: 2 Mafia, 1 Sheriff, 1 Doctor, 1 Vigilante, rest Town
        """
        roles = [Role.MAFIA, Role.MAFIA]  # Always 2 mafia
        
        if player_count >= 5:
            roles.append(Role.SHERIFF)
        if player_count >= 5:
            roles.append(Role.DOCTOR)
        if player_count >= 6:
            roles.append(Role.VIGILANTE)
        
        # Fill rest with Town
        while len(roles) < player_count:
            roles.append(Role.TOWN)
        
        return roles[:player_count]
    
    def transition_to_day(self) -> GameState:
        """Transition from night phase to day phase."""
        if self.state.phase != Phase.NIGHT:
            raise ValueError("Can only transition to day from night phase")
        
        self.state.day_number += 1
        self.state.phase = Phase.DAY
        
        # Reveal night events
        night_summary = self._summarize_night_events()
        self.state.add_game_event(
            "day_start",
            f"Day {self.state.day_number} begins",
            {"night_summary": night_summary}
        )
        
        return self.state
    
    def transition_to_discussion(self) -> GameState:
        """Transition to discussion phase."""
        if self.state.phase != Phase.DAY:
            raise ValueError("Can only start discussion during day phase")
        
        self.state.phase = Phase.DISCUSSION
        self.state.add_game_event("discussion_start", "Discussion phase begins", {})
        return self.state
    
    def transition_to_voting(self) -> GameState:
        """Transition from discussion to voting phase."""
        if self.state.phase != Phase.DISCUSSION:
            raise ValueError("Can only start voting from discussion phase")
        
        self.state.phase = Phase.VOTING
        self.state.votes = []  # Clear previous votes
        self.state.add_game_event("voting_start", "Voting phase begins", {})
        return self.state
    
    def transition_to_night(self) -> GameState:
        """Transition from day phase to night phase."""
        if self.state.phase != Phase.VOTING:
            raise ValueError("Can only transition to night from voting phase")
        
        self.state.phase = Phase.NIGHT
        self.state.night_actions = []  # Clear previous night actions
        self.state.add_game_event("night_start", f"Night {self.state.day_number + 1} begins", {})
        return self.state
    
    def add_vote(self, vote: Vote) -> GameState:
        """Add a vote to the current voting phase."""
        if self.state.phase != Phase.VOTING:
            raise ValueError("Can only vote during voting phase")
        
        # Validate voter is alive
        voter = self.state.get_player_by_id(vote.voter_id)
        if not voter or not voter.is_alive():
            raise ValueError("Only alive players can vote")
        
        # Validate target if specified
        if vote.target_id:
            target = self.state.get_player_by_id(vote.target_id)
            if not target or not target.is_alive():
                raise ValueError("Can only vote for alive players")
        
        self.state.votes.append(vote)
        voter.vote_history.append(vote.to_dict())
        
        self.state.add_game_event(
            "vote_cast",
            f"{voter.name} voted for {target.name if vote.target_id else 'abstain'}",
            {"vote": vote.to_dict()}
        )
        
        return self.state
    
    def process_votes(self) -> Tuple[Optional[Player], GameState]:
        """
        Process all votes and determine the lynch result.
        
        Returns:
            Tuple of (lynched_player, updated_game_state)
            lynched_player is None if no one was lynched (tie or all abstained)
        """
        if self.state.phase != Phase.VOTING:
            raise ValueError("Can only process votes during voting phase")
        
        alive_players = self.state.get_alive_players()
        votes_cast = len(self.state.votes)
        
        if votes_cast == 0:
            self.state.add_game_event("vote_result", "No votes cast - no one is lynched", {})
            return None, self.state
        
        # Count votes
        vote_counts: Dict[str, int] = {}
        abstentions = 0
        
        for vote in self.state.votes:
            if vote.target_id is None:
                abstentions += 1
            else:
                vote_counts[vote.target_id] = vote_counts.get(vote.target_id, 0) + 1
        
        if not vote_counts:
            self.state.add_game_event("vote_result", "All players abstained - no one is lynched", {})
            return None, self.state
        
        # Find player(s) with most votes
        max_votes = max(vote_counts.values())
        candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
        
        if len(candidates) > 1:
            # Tie - no one is lynched
            self.state.add_game_event(
                "vote_result",
                f"Tie between {len(candidates)} players - no one is lynched",
                {"candidates": candidates}
            )
            return None, self.state
        
        # Lynch the player with most votes
        lynched_id = candidates[0]
        lynched_player = self.state.get_player_by_id(lynched_id)
        lynched_player.status = PlayerStatus.LYNCHED
        
        self.state.add_game_event(
            "lynch",
            f"{lynched_player.name} was lynched",
            {"player_id": lynched_id, "role": lynched_player.role.value}
        )
        
        return lynched_player, self.state
    
    def add_night_action(self, action: Action) -> GameState:
        """Add a night action to be processed."""
        if self.state.phase != Phase.NIGHT:
            raise ValueError("Can only perform night actions during night phase")
        
        # Validate actor is alive
        actor = self.state.get_player_by_id(action.actor_id)
        if not actor or not actor.is_alive():
            raise ValueError("Only alive players can perform night actions")
        
        self.state.night_actions.append(action)
        actor.action_history.append(action.to_dict())
        
        return self.state
    
    def process_night_actions(self) -> GameState:
        """
        Process all night actions and resolve their effects.
        
        Returns:
            Updated game state with night action results.
        """
        if self.state.phase != Phase.NIGHT:
            raise ValueError("Can only process night actions during night phase")
        
        # Group actions by type
        mafia_kills = []
        doctor_protections = []
        sheriff_investigations = []
        vigilante_kills = []
        
        for action in self.state.night_actions:
            if action.action_type == "mafia_kill":
                mafia_kills.append(action)
            elif action.action_type == "protect":
                doctor_protections.append(action)
            elif action.action_type == "investigate":
                sheriff_investigations.append(action)
            elif action.action_type == "vigilante_kill":
                vigilante_kills.append(action)
        
        # Process investigations first (they don't affect kills)
        for action in sheriff_investigations:
            if action.target_id:
                target = self.state.get_player_by_id(action.target_id)
                if target:
                    is_mafia = target.role == Role.MAFIA
                    action.result = "mafia" if is_mafia else "town"
                    # Store result in actor's known_info
                    actor = self.state.get_player_by_id(action.actor_id)
                    if actor:
                        actor.known_info[f"investigation_{self.state.day_number}"] = {
                            "target": action.target_id,
                            "result": action.result
                        }
        
        # Determine kill targets
        kill_targets = set()
        
        # Mafia kill (majority vote)
        if mafia_kills:
            mafia_vote_counts: Dict[str, int] = {}
            for action in mafia_kills:
                if action.target_id:
                    mafia_vote_counts[action.target_id] = mafia_vote_counts.get(action.target_id, 0) + 1
            
            if mafia_vote_counts:
                max_mafia_votes = max(mafia_vote_counts.values())
                mafia_targets = [pid for pid, count in mafia_vote_counts.items() if count == max_mafia_votes]
                if len(mafia_targets) == 1:
                    kill_targets.add(mafia_targets[0])
        
        # Vigilante kill
        if vigilante_kills:
            for action in vigilante_kills:
                if action.target_id:
                    kill_targets.add(action.target_id)
        
        # Apply doctor protections
        protected_players = set()
        for action in doctor_protections:
            if action.target_id:
                protected_players.add(action.target_id)
        
        # Execute kills (only if not protected)
        for target_id in kill_targets:
            if target_id not in protected_players:
                target = self.state.get_player_by_id(target_id)
                if target and target.is_alive():
                    target.status = PlayerStatus.KILLED
                    self.state.add_game_event(
                        "night_kill",
                        f"{target.name} was killed during the night",
                        {"player_id": target_id, "role": target.role.value}
                    )
        
        return self.state
    
    def check_win_conditions(self) -> Optional[str]:
        """
        Check if the game has ended and determine the winner.
        
        Returns:
            "mafia" if mafia wins, "town" if town wins, None if game continues
        """
        alive_players = self.state.get_alive_players()
        alive_mafia = self.state.get_alive_players_by_role(Role.MAFIA)
        alive_town = [p for p in alive_players if p.role != Role.MAFIA]
        
        # Mafia wins if they equal or outnumber town
        if len(alive_mafia) >= len(alive_town):
            self.state.winner = "mafia"
            self.state.phase = Phase.GAME_OVER
            self.is_running = False
            self.state.add_game_event("game_end", "Mafia wins!", {"winner": "mafia"})
            return "mafia"
        
        # Town wins if all mafia are dead
        if len(alive_mafia) == 0:
            self.state.winner = "town"
            self.state.phase = Phase.GAME_OVER
            self.is_running = False
            self.state.add_game_event("game_end", "Town wins!", {"winner": "town"})
            return "town"
        
        return None
    
    def _summarize_night_events(self) -> Dict:
        """Create a summary of what happened during the night."""
        summary = {
            "killed": [],
            "protected": [],
            "investigations": []
        }
        
        for action in self.state.night_actions:
            if action.action_type == "mafia_kill" and action.target_id:
                target = self.state.get_player_by_id(action.target_id)
                if target and target.status == PlayerStatus.KILLED:
                    summary["killed"].append(target.name)
            elif action.action_type == "protect" and action.target_id:
                target = self.state.get_player_by_id(action.target_id)
                if target:
                    summary["protected"].append(target.name)
            elif action.action_type == "investigate" and action.target_id:
                target = self.state.get_player_by_id(action.target_id)
                if target:
                    summary["investigations"].append({
                        "target": target.name,
                        "result": action.result
                    })
        
        return summary
    
    def get_state(self) -> GameState:
        """Get the current game state."""
        return self.state

