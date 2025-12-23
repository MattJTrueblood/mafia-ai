"""Flask web application for the Mafia AI game."""
from flask import Flask, render_template, jsonify, request
import json
import threading
import time
from typing import List
from models import GameState, Player, Role, Phase
from game_engine import GameEngine
from openrouter_client import OpenRouterClient
from player import AIPlayer
from discussion_manager import DiscussionManager
from voting_manager import VotingManager
from night_actions import NightActionsManager
from config import DEFAULT_MODELS, DEFAULT_DISCUSSION_TIME_LIMIT

app = Flask(__name__)

# Global game state
game_engine = None
ai_players = []
game_thread = None
game_lock = threading.Lock()


class GameOrchestrator:
    """Orchestrates the game flow."""
    
    def __init__(self, engine: GameEngine, players: List[AIPlayer]):
        self.engine = engine
        self.players = players
        self.discussion_manager = None
        self.voting_manager = None
        self.night_actions_manager = None
        self.is_paused = False
    
    def run_game_loop(self):
        """Run the main game loop."""
        while self.engine.is_running and not self.is_paused:
            state = self.engine.get_state()
            
            if state.phase == Phase.NIGHT:
                self._run_night_phase()
            elif state.phase == Phase.DAY:
                self.engine.transition_to_discussion()
                self._run_discussion_phase()
                self.engine.transition_to_voting()
                self._run_voting_phase()
                self.engine.transition_to_night()
            
            # Check win conditions
            winner = self.engine.check_win_conditions()
            if winner:
                break
            
            # Small delay to prevent tight loop
            time.sleep(0.5)
    
    def _run_night_phase(self):
        """Execute the night phase."""
        state = self.engine.get_state()
        
        # Initialize night actions manager
        self.night_actions_manager = NightActionsManager(state, self.players)
        
        # Collect all night actions
        updated_state = self.night_actions_manager.collect_all_actions()
        self.engine.state = updated_state  # Update engine state
        
        # Process night actions
        updated_state = self.night_actions_manager.process_actions()
        self.engine.state = updated_state  # Update engine state
        
        # Transition to day
        self.engine.transition_to_day()
    
    def _run_discussion_phase(self):
        """Execute the discussion phase."""
        state = self.engine.get_state()
        
        # Initialize discussion manager
        self.discussion_manager = DiscussionManager(
            state, self.players, time_limit=DEFAULT_DISCUSSION_TIME_LIMIT
        )
        self.discussion_manager.start_discussion()
        
        # Run discussion until time limit or natural end
        max_messages = 50  # Safety limit
        message_count = 0
        
        while not self.discussion_manager.should_end_discussion() and message_count < max_messages:
            if self.is_paused:
                break
            
            speaker = self.discussion_manager.get_next_speaker()
            if not speaker:
                break
            
            # Get message from AI player
            recent_messages = self.discussion_manager.get_messages()[-5:]
            message = speaker.get_discussion_message(
                state,
                recent_messages
            )
            
            # Add message to discussion
            updated_state = self.discussion_manager.add_message(message)
            self.engine.state = updated_state  # Update engine state
            state = updated_state  # Update local state reference
            message_count += 1
            
            # Small delay between messages
            time.sleep(1)
    
    def _run_voting_phase(self):
        """Execute the voting phase."""
        state = self.engine.get_state()
        
        # Initialize voting manager
        self.voting_manager = VotingManager(state, self.players)
        self.voting_manager.start_voting()
        
        # Collect votes sequentially
        while not self.voting_manager.is_voting_complete():
            if self.is_paused:
                break
            
            voter = self.voting_manager.get_next_voter()
            if not voter:
                break
            
            # Get vote from AI player
            current_votes = self.voting_manager.get_current_votes()
            vote = voter.vote(state, current_votes)
            
            # Collect vote
            updated_state = self.voting_manager.collect_vote(vote)
            self.engine.state = updated_state  # Update engine state
            state = updated_state  # Update local state reference
            
            # Small delay between votes
            time.sleep(1)
        
        # Process all votes
        lynched_player, updated_state = self.voting_manager.process_all_votes()
        self.engine.state = updated_state


@app.route('/')
def index():
    """Render the game setup page."""
    return render_template('index.html')


@app.route('/game')
def game():
    """Render the active game view."""
    return render_template('game.html')


@app.route('/api/start_game', methods=['POST'])
def start_game():
    """Start a new game."""
    global game_engine, ai_players, game_thread, game_orchestrator
    
    with game_lock:
        if game_engine and game_engine.is_running:
            return jsonify({"error": "Game is already running"}), 400
        
        data = request.json
        player_configs = data.get('players', [])
        
        if len(player_configs) < 5:
            return jsonify({"error": "Need at least 5 players"}), 400
        
        # Create game engine
        game_engine = GameEngine()
        client = OpenRouterClient()
        
        # Create AI players
        ai_players = []
        for config in player_configs:
            player = Player(
                name=config['name'],
                role=Role.TOWN,  # Will be assigned by engine
                model=config.get('model', DEFAULT_MODELS[0]),
                player_id=f"player_{len(ai_players)}"
            )
            ai_player = AIPlayer(player, client)
            ai_players.append(ai_player)
        
        # Start game
        player_configs_for_engine = [
            {"name": p.player.name, "model": p.player.model}
            for p in ai_players
        ]
        game_engine.start_game(player_configs_for_engine)
        
        # Create orchestrator
        game_orchestrator = GameOrchestrator(game_engine, ai_players)
        
        # Start game thread
        game_thread = threading.Thread(target=game_orchestrator.run_game_loop, daemon=True)
        game_thread.start()
        
        return jsonify({"status": "started", "game_state": game_engine.get_state().to_dict()})
    
    return jsonify({"error": "Failed to start game"}), 500


@app.route('/api/game_state', methods=['GET'])
def get_game_state():
    """Get the current game state."""
    global game_engine
    
    with game_lock:
        if not game_engine:
            return jsonify({"error": "No game running"}), 404
        
        state = game_engine.get_state()
        return jsonify(state.to_dict())


@app.route('/api/pause_game', methods=['POST'])
def pause_game():
    """Pause the game."""
    global game_orchestrator
    
    with game_lock:
        if game_orchestrator:
            game_orchestrator.is_paused = True
            return jsonify({"status": "paused"})
        
        return jsonify({"error": "No game running"}), 404


@app.route('/api/resume_game', methods=['POST'])
def resume_game():
    """Resume the game."""
    global game_orchestrator, game_thread
    
    with game_lock:
        if game_orchestrator:
            game_orchestrator.is_paused = False
            if not game_thread or not game_thread.is_alive():
                game_thread = threading.Thread(
                    target=game_orchestrator.run_game_loop, daemon=True
                )
                game_thread.start()
            return jsonify({"status": "resumed"})
        
        return jsonify({"error": "No game running"}), 404


@app.route('/api/available_models', methods=['GET'])
def get_available_models():
    """Get list of available models."""
    return jsonify({"models": DEFAULT_MODELS})


if __name__ == '__main__':
    # Create necessary directories
    import os
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)

