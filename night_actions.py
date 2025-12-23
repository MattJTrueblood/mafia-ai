"""Night actions manager for the Mafia game."""
from typing import List, Dict, Optional, Set
from models import GameState, Player, Action, Role
from player import AIPlayer


class NightActionsManager:
    """Manages night phase actions."""
    
    def __init__(self, game_state: GameState, ai_players: List[AIPlayer]):
        """
        Initialize the night actions manager.
        
        Args:
            game_state: Current game state
            ai_players: List of AI player instances
        """
        self.game_state = game_state
        self.ai_players = {p.player.player_id: p for p in ai_players}
        self.actions: List[Action] = []
    
    def collect_mafia_votes(self) -> GameState:
        """Collect votes from all Mafia players."""
        mafia_players = self.game_state.get_alive_players_by_role(Role.MAFIA)
        
        for player in mafia_players:
            if player.player_id in self.ai_players:
                ai_player = self.ai_players[player.player_id]
                action = ai_player.night_action(self.game_state, "mafia_kill")
                if action:
                    self.actions.append(action)
                    self.game_state.add_night_action(action)
        
        return self.game_state
    
    def collect_doctor_action(self) -> GameState:
        """Collect protection action from Doctor."""
        doctor_players = self.game_state.get_alive_players_by_role(Role.DOCTOR)
        
        if not doctor_players:
            return self.game_state
        
        doctor = doctor_players[0]  # Should only be one doctor
        if doctor.player_id in self.ai_players:
            ai_player = self.ai_players[doctor.player_id]
            action = ai_player.night_action(self.game_state, "protect")
            if action:
                # Validate doctor cannot protect same person twice in a row
                if action.target_id == doctor.last_protected:
                    # Invalid action, create abstain action instead
                    action = Action(
                        actor_id=doctor.player_id,
                        action_type="protect",
                        target_id=None,
                        timestamp=action.timestamp
                    )
                else:
                    # Update last protected
                    if action.target_id:
                        doctor.last_protected = action.target_id
                
                self.actions.append(action)
                self.game_state.add_night_action(action)
        
        return self.game_state
    
    def collect_sheriff_action(self) -> GameState:
        """Collect investigation action from Sheriff."""
        sheriff_players = self.game_state.get_alive_players_by_role(Role.SHERIFF)
        
        if not sheriff_players:
            return self.game_state
        
        sheriff = sheriff_players[0]  # Should only be one sheriff
        if sheriff.player_id in self.ai_players:
            ai_player = self.ai_players[sheriff.player_id]
            action = ai_player.night_action(self.game_state, "investigate")
            if action:
                self.actions.append(action)
                self.game_state.add_night_action(action)
        
        return self.game_state
    
    def collect_vigilante_action(self) -> GameState:
        """Collect kill action from Vigilante."""
        vigilante_players = self.game_state.get_alive_players_by_role(Role.VIGILANTE)
        
        if not vigilante_players:
            return self.game_state
        
        vigilante = vigilante_players[0]  # Should only be one vigilante
        if vigilante.player_id in self.ai_players and not vigilante.has_used_vigilante_bullet:
            ai_player = self.ai_players[vigilante.player_id]
            action = ai_player.night_action(self.game_state, "vigilante_kill")
            if action:
                self.actions.append(action)
                self.game_state.add_night_action(action)
        
        return self.game_state
    
    def collect_all_actions(self) -> GameState:
        """Collect all night actions in the correct order."""
        # Order: Mafia votes, Doctor protects, Sheriff investigates, Vigilante kills
        self.game_state = self.collect_mafia_votes()
        self.game_state = self.collect_doctor_action()
        self.game_state = self.collect_sheriff_action()
        self.game_state = self.collect_vigilante_action()
        
        return self.game_state
    
    def process_actions(self) -> GameState:
        """
        Process all night actions and resolve their effects.
        
        Returns:
            Updated game state
        """
        # Group actions by type
        mafia_kills = []
        doctor_protections = []
        sheriff_investigations = []
        vigilante_kills = []
        
        for action in self.game_state.night_actions:
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
                target = self.game_state.get_player_by_id(action.target_id)
                if target:
                    is_mafia = target.role == Role.MAFIA
                    action.result = "mafia" if is_mafia else "town"
                    # Store result in actor's known_info
                    actor = self.game_state.get_player_by_id(action.actor_id)
                    if actor:
                        actor.known_info[f"investigation_{self.game_state.day_number}"] = {
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
        from models import PlayerStatus
        for target_id in kill_targets:
            if target_id not in protected_players:
                target = self.game_state.get_player_by_id(target_id)
                if target and target.is_alive():
                    target.status = PlayerStatus.KILLED
                    self.game_state.add_game_event(
                        "night_kill",
                        f"{target.name} was killed during the night",
                        {"player_id": target_id, "role": target.role.value}
                    )
        
        return self.game_state
    
    def get_actions_summary(self) -> Dict:
        """Get a summary of all night actions."""
        summary = {
            "mafia_kills": [],
            "protections": [],
            "investigations": [],
            "vigilante_kills": []
        }
        
        for action in self.actions:
            if action.action_type == "mafia_kill" and action.target_id:
                target = self.game_state.get_player_by_id(action.target_id)
                if target:
                    summary["mafia_kills"].append({
                        "actor": self._get_player_name(action.actor_id),
                        "target": target.name
                    })
            elif action.action_type == "protect" and action.target_id:
                target = self.game_state.get_player_by_id(action.target_id)
                if target:
                    summary["protections"].append({
                        "actor": self._get_player_name(action.actor_id),
                        "target": target.name
                    })
            elif action.action_type == "investigate" and action.target_id:
                target = self.game_state.get_player_by_id(action.target_id)
                if target:
                    summary["investigations"].append({
                        "actor": self._get_player_name(action.actor_id),
                        "target": target.name,
                        "result": action.result
                    })
            elif action.action_type == "vigilante_kill" and action.target_id:
                target = self.game_state.get_player_by_id(action.target_id)
                if target:
                    summary["vigilante_kills"].append({
                        "actor": self._get_player_name(action.actor_id),
                        "target": target.name
                    })
        
        return summary
    
    def _get_player_name(self, player_id: str) -> str:
        """Get player name by ID."""
        player = self.game_state.get_player_by_id(player_id)
        return player.name if player else player_id

