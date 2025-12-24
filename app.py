"""Flask application for Mafia AI game."""

# CRITICAL: gevent.monkey_patch() MUST be called before any other imports
from gevent import monkey
monkey.patch_all()

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from game.game_state import GameState
from game.phases import handle_night_phase, handle_day_phase
from game.win_conditions import check_win_conditions
from llm.openrouter_client import OpenRouterClient
import config

app = Flask(__name__)
app.secret_key = "mafia-ai-secret-key"  # Change in production
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# In-memory game storage (for MVP)
games = {}

# Initialize LLM client
llm_client = OpenRouterClient()


def emit_game_state_update(game_id, game_state):
    """Emit game state update to all clients watching this game."""
    socketio.emit('game_state_update', game_state.to_dict(), room=game_id)


def emit_discussion_status(game_id, status):
    """Emit discussion status update for UI visibility."""
    socketio.emit('discussion_status', status, room=game_id)


@app.route("/")
def index():
    """Setup page for player selection."""
    return render_template("index.html", 
                         default_models=config.DEFAULT_MODELS,
                         model_pricing=config.MODEL_PRICING)


@app.route("/start_game", methods=["POST"])
def start_game():
    """Initialize a new game with players."""
    data = request.json
    players = data.get("players", [])
    
    if len(players) < 3:
        return jsonify({"error": "Need at least 3 players"}), 400
    
    # Create game state
    game_state = GameState(players)
    games[game_state.game_id] = game_state
    
    return jsonify({"game_id": game_state.game_id, "redirect": url_for("game_view", game_id=game_state.game_id)})


@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    pass


@socketio.on('join_game')
def handle_join_game(data):
    """Handle client joining a game room."""
    game_id = data.get('game_id')
    if game_id in games:
        join_room(game_id)
        emit('joined_game', {'game_id': game_id})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    pass


@app.route("/game/<game_id>")
def game_view(game_id):
    """Game view page."""
    if game_id not in games:
        return "Game not found", 404
    
    game_state = games[game_id]
    return render_template("game.html", game_id=game_id, game_state=game_state.to_dict())


@app.route("/game/<game_id>/state")
def get_game_state(game_id):
    """Get current game state as JSON."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    game_state = games[game_id]
    return jsonify(game_state.to_dict())


@app.route("/game/<game_id>/player/<player_name>/context")
def get_player_context(game_id, player_name):
    """Get the most recent LLM context for a player (for debugging prompts)."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    game_state = games[game_id]
    player = game_state.get_player_by_name(player_name)

    if not player:
        return jsonify({"error": "Player not found"}), 404

    if not player.last_llm_context:
        return jsonify({"error": "No context available yet"}), 404

    return jsonify({
        "player_name": player_name,
        "context": player.last_llm_context
    })


@app.route("/game/<game_id>/next", methods=["POST"])
def next_action(game_id):
    """Advance to next phase/action."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    
    game_state = games[game_id]
    
    # Check if game is over
    if game_state.game_over:
        emit_game_state_update(game_id, game_state)
        return jsonify({"message": "Game is over", "game_state": game_state.to_dict()})
    
    # Check win conditions first
    winner = check_win_conditions(game_state)
    if winner:
        game_state.winner = winner
        game_state.game_over = True
        game_state.add_event("system", f"Game over! {'Mafia' if winner == 'mafia' else 'Town'} wins!", "all")
        emit_game_state_update(game_id, game_state)
        return jsonify({"message": "Game over", "winner": winner, "game_state": game_state.to_dict()})
    
    try:
        # Process current phase
        if game_state.phase == "night":
            # Handle night phase (with real-time updates during actions)
            handle_night_phase(game_state, llm_client, game_id=game_id, emit_callback=emit_game_state_update)
            
            # Emit final update after night phase
            emit_game_state_update(game_id, game_state)
            
            # Check win conditions after night
            winner = check_win_conditions(game_state)
            if winner:
                game_state.winner = winner
                game_state.game_over = True
                game_state.add_event("system", f"Game over! {'Mafia' if winner == 'mafia' else 'Town'} wins!", "all")
                emit_game_state_update(game_id, game_state)
            else:
                # Transition to day phase (but don't process it yet)
                game_state.phase = "day"
                emit_game_state_update(game_id, game_state)
        
        elif game_state.phase == "day":
            # Handle day phase (with real-time updates during discussion)
            handle_day_phase(game_state, llm_client, game_id=game_id, emit_callback=emit_game_state_update, emit_status_callback=emit_discussion_status)
            
            # Emit final update after day phase
            emit_game_state_update(game_id, game_state)
            
            # Check win conditions after day
            winner = check_win_conditions(game_state)
            if winner:
                game_state.winner = winner
                game_state.game_over = True
                game_state.add_event("system", f"Game over! {'Mafia' if winner == 'mafia' else 'Town'} wins!", "all")
                emit_game_state_update(game_id, game_state)
            else:
                # Transition to night phase (but don't process it yet)
                game_state.phase = "night"
                emit_game_state_update(game_id, game_state)
        
        return jsonify({"message": "Phase completed", "game_state": game_state.to_dict()})
    
    except Exception as e:
        emit_game_state_update(game_id, game_state)
        return jsonify({"error": str(e), "game_state": game_state.to_dict()}), 500


if __name__ == "__main__":
    # Use eventlet for proper WebSocket support
    socketio.run(app, debug=True, port=5000)

