"""Voting phase manager for the Mafia game."""
from typing import List, Optional, Tuple, Dict
from models import GameState, Player, Vote
from player import AIPlayer


class VotingManager:
    """Manages the voting phase of the game."""
    
    def __init__(self, game_state: GameState, ai_players: List[AIPlayer]):
        """
        Initialize the voting manager.
        
        Args:
            game_state: Current game state
            ai_players: List of AI player instances
        """
        self.game_state = game_state
        self.ai_players = {p.player.player_id: p for p in ai_players}
        self.votes: List[Vote] = []
        self.voting_order: List[str] = []  # Order in which players will vote
        self.current_voter_index = 0
    
    def start_voting(self) -> GameState:
        """Start the voting phase and determine voting order."""
        alive_players = self.game_state.get_alive_players()
        
        # Create voting order (shuffle for fairness)
        import random
        self.voting_order = [p.player_id for p in alive_players]
        random.shuffle(self.voting_order)
        
        self.votes = []
        self.current_voter_index = 0
        
        return self.game_state
    
    def get_next_voter(self) -> Optional[AIPlayer]:
        """Get the next player who should vote."""
        if self.current_voter_index >= len(self.voting_order):
            return None
        
        player_id = self.voting_order[self.current_voter_index]
        return self.ai_players.get(player_id)
    
    def collect_vote(self, vote: Vote) -> GameState:
        """
        Collect a vote from a player.
        
        Args:
            vote: Vote object
        
        Returns:
            Updated game state
        """
        # Validate vote
        voter = self.game_state.get_player_by_id(vote.voter_id)
        if not voter or not voter.is_alive():
            raise ValueError("Only alive players can vote")
        
        if vote.target_id:
            target = self.game_state.get_player_by_id(vote.target_id)
            if not target or not target.is_alive():
                raise ValueError("Can only vote for alive players")
        
        # Add vote to local list
        self.votes.append(vote)
        
        # Add vote to game state
        self.game_state.votes.append(vote)
        voter.vote_history.append(vote.to_dict())
        
        # Add game event
        target_name = target.name if vote.target_id and target else 'abstain'
        self.game_state.add_game_event(
            "vote_cast",
            f"{voter.name} voted for {target_name}",
            {"vote": vote.to_dict()}
        )
        
        # Move to next voter
        self.current_voter_index += 1
        
        return self.game_state
    
    def get_current_votes(self) -> List[Vote]:
        """Get all votes cast so far."""
        return self.votes
    
    def is_voting_complete(self) -> bool:
        """Check if all players have voted."""
        return self.current_voter_index >= len(self.voting_order)
    
    def get_vote_summary(self) -> Dict:
        """Get a summary of current votes."""
        summary = {
            "total_votes": len(self.votes),
            "expected_votes": len(self.voting_order),
            "votes_by_target": {},
            "abstentions": 0
        }
        
        for vote in self.votes:
            if vote.target_id is None:
                summary["abstentions"] += 1
            else:
                target_name = self._get_player_name(vote.target_id)
                if target_name not in summary["votes_by_target"]:
                    summary["votes_by_target"][target_name] = 0
                summary["votes_by_target"][target_name] += 1
        
        return summary
    
    def _get_player_name(self, player_id: str) -> str:
        """Get player name by ID."""
        player = self.game_state.get_player_by_id(player_id)
        return player.name if player else player_id
    
    def process_all_votes(self) -> Tuple[Optional[Player], GameState]:
        """
        Process all votes and determine the lynch result.
        
        Returns:
            Tuple of (lynched_player, updated_game_state)
        """
        # Transfer votes to game state
        for vote in self.votes:
            if vote not in self.game_state.votes:
                self.game_state.votes.append(vote)
        
        # Process votes directly
        alive_players = self.game_state.get_alive_players()
        votes_cast = len(self.game_state.votes)
        
        if votes_cast == 0:
            self.game_state.add_game_event("vote_result", "No votes cast - no one is lynched", {})
            return None, self.game_state
        
        # Count votes
        vote_counts: Dict[str, int] = {}
        abstentions = 0
        
        for vote in self.game_state.votes:
            if vote.target_id is None:
                abstentions += 1
            else:
                vote_counts[vote.target_id] = vote_counts.get(vote.target_id, 0) + 1
        
        if not vote_counts:
            self.game_state.add_game_event("vote_result", "All players abstained - no one is lynched", {})
            return None, self.game_state
        
        # Find player(s) with most votes
        max_votes = max(vote_counts.values())
        candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
        
        if len(candidates) > 1:
            # Tie - no one is lynched
            self.game_state.add_game_event(
                "vote_result",
                f"Tie between {len(candidates)} players - no one is lynched",
                {"candidates": candidates}
            )
            return None, self.game_state
        
        # Lynch the player with most votes
        lynched_id = candidates[0]
        lynched_player = self.game_state.get_player_by_id(lynched_id)
        if lynched_player:
            from models import PlayerStatus
            lynched_player.status = PlayerStatus.LYNCHED
            
            self.game_state.add_game_event(
                "lynch",
                f"{lynched_player.name} was lynched",
                {"player_id": lynched_id, "role": lynched_player.role.value}
            )
        
        return lynched_player, self.game_state

