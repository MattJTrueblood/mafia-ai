"""Flask application for Mafia AI game."""

# CRITICAL: gevent.monkey_patch() MUST be called before any other imports
from gevent import monkey
monkey.patch_all()

import gevent
from gevent.event import Event

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from game.game_state import GameState
from game.step_processor import process_step
from llm.openrouter_client import OpenRouterClient, LLMCancelledException
from game.error_logger import initialize_logging, set_game_context, clear_game_context, log_exception
import config
import logging
import sys
from gevent.hub import Hub

# =============================================================================
# UNIFIED EXCEPTION LOGGING - Catches ALL exceptions automatically
# =============================================================================

_original_hub_handle_error = Hub.handle_error
_original_sys_excepthook = sys.excepthook

def unified_greenlet_exception_handler(self, context, type, value, tb):
    """
    Unified exception handler for ALL gevent greenlets.
    This catches EVERY exception in EVERY greenlet before any other handler.
    """
    log_exception(value, f"Greenlet exception in {context}")
    # Call original handler to maintain gevent's behavior
    _original_hub_handle_error(self, context, type, value, tb)

def unified_thread_exception_handler(exc_type, exc_value, exc_traceback):
    """
    Unified exception handler for main thread uncaught exceptions.
    """
    log_exception(exc_value, "Uncaught exception in main thread")
    # Call original handler to maintain normal behavior
    _original_sys_excepthook(exc_type, exc_value, exc_traceback)

Hub.handle_error = unified_greenlet_exception_handler
sys.excepthook = unified_thread_exception_handler

# =============================================================================

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

games = {}


class GameControl:
    """Control state for a running game loop."""

    def __init__(self):
        self.pause_event = Event()  # Set when game is paused
        self.cancel_event = Event()  # Set to cancel current LLM call
        self.loop_greenlet = None  # The running game loop greenlet
        self.is_running = False  # Whether the loop is active


game_controls = {}
game_clients = {}

llm_client = OpenRouterClient()

initialize_logging(log_dir="logs", log_level=logging.INFO)


def emit_game_state_update(game_id, game_state):
    """Emit game state update to all clients watching this game."""
    socketio.emit('game_state_update', game_state.to_dict(), room=game_id)


def emit_discussion_status(game_id, status):
    """Emit discussion status update for UI visibility."""
    socketio.emit('discussion_status', status, room=game_id)


def emit_player_status(game_id, player_name, status):
    """Emit player API status update for UI visibility.

    This is the UNIVERSAL status for any player waiting on an API response.
    The UI should show "..." next to the player's name when status is "pending".

    Args:
        game_id: The game ID
        player_name: Name of the player
        status: "pending" or "complete"
    """
    socketio.emit('player_status', {'player': player_name, 'status': status}, room=game_id)


def cleanup_game(game_id):
    """Clean up a game completely - stop loop, delete all state."""
    if game_id not in games:
        return

    if game_id in game_controls:
        control = game_controls[game_id]
        if control.loop_greenlet and not control.loop_greenlet.dead:
            control.pause_event.set()
            control.cancel_event.set()
            control.loop_greenlet.kill()
        del game_controls[game_id]

    if game_id in games:
        del games[game_id]

    if game_id in game_clients:
        del game_clients[game_id]


def game_loop(game_id: str):
    """
    Main game loop running as a greenlet.
    Runs continuously until game is over or loop is killed.

    Uses step-based execution: each iteration processes exactly one atomic step.
    The game can be paused between any two steps and will resume exactly where it left off.
    """
    if game_id not in games or game_id not in game_controls:
        return

    game_state = games[game_id]
    control = game_controls[game_id]
    control.is_running = True

    set_game_context(game_id=game_id)

    try:
        while not game_state.game_over:
            set_game_context(
                phase=game_state.phase,
                day_number=game_state.day_number,
                current_step=game_state.current_step
            )

            while control.pause_event.is_set():
                gevent.sleep(0.1)  # Cooperative yield

            control.cancel_event.clear()

            try:
                process_step(
                    game_state=game_state,
                    llm_client=llm_client,
                    cancel_event=control.cancel_event,
                    emit_callback=emit_game_state_update,
                    emit_status_callback=emit_discussion_status,
                    emit_player_status_callback=emit_player_status,
                    game_id=game_id,
                )

                # Small yield to allow other greenlets to run
                gevent.sleep(0)

            except LLMCancelledException:
                # LLM call was cancelled - treat as pause
                control.pause_event.set()
                emit_game_state_update(game_id, game_state)
                socketio.emit('pause_state', {'paused': True}, room=game_id)
                continue
            except Exception as e:
                log_exception(e, "Error in game loop", extra_context={
                    "game_over": game_state.game_over,
                    "step_index": game_state.step_index
                })
                game_state.add_event("system", f"Error: {str(e)}", "all")
                emit_game_state_update(game_id, game_state)
                # Pause on error so user can investigate
                control.pause_event.set()
                socketio.emit('pause_state', {'paused': True}, room=game_id)

    finally:
        clear_game_context()
        control.is_running = False


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

    game_state = GameState(players)
    games[game_state.game_id] = game_state
    
    return jsonify({"game_id": game_state.game_id, "redirect": url_for("game_view", game_id=game_state.game_id)})


@socketio.on('join_game')
def handle_join_game(data):
    """Handle client joining a game room."""
    game_id = data.get('game_id')
    if game_id in games:
        join_room(game_id)

        if game_id not in game_clients:
            game_clients[game_id] = set()
        game_clients[game_id].add(request.sid)

        emit('joined_game', {'game_id': game_id})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection and cleanup empty games."""
    games_to_check = []
    for game_id, clients in list(game_clients.items()):
        if request.sid in clients:
            clients.remove(request.sid)
            games_to_check.append(game_id)

    for game_id in games_to_check:
        if game_id in game_clients and len(game_clients[game_id]) == 0:
            cleanup_game(game_id)


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


@app.route("/game/<game_id>/start", methods=["POST"])
def start_game_loop(game_id):
    """Start the continuous game loop."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    if game_id in game_controls and game_controls[game_id].is_running:
        return jsonify({"error": "Game already running"}), 400

    control = GameControl()
    game_controls[game_id] = control

    control.loop_greenlet = gevent.spawn(game_loop, game_id)

    return jsonify({"started": True})


@app.route("/game/<game_id>/pause", methods=["POST"])
def toggle_pause(game_id):
    """Toggle pause state for a game."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    if game_id not in game_controls:
        return jsonify({"error": "Game not started"}), 400

    control = game_controls[game_id]

    if control.pause_event.is_set():
        # Resume: clear both events
        control.cancel_event.clear()
        control.pause_event.clear()
    else:
        # Pause: set both events (cancel in-flight LLM call)
        control.pause_event.set()
        control.cancel_event.set()

    is_paused = control.pause_event.is_set()
    socketio.emit('pause_state', {'paused': is_paused}, room=game_id)
    return jsonify({"paused": is_paused})


@app.route("/game/<game_id>/pause/state")
def get_pause_state(game_id):
    """Get current pause state for a game."""
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404

    if game_id not in game_controls:
        return jsonify({"paused": False, "started": False})

    control = game_controls[game_id]
    return jsonify({"paused": control.pause_event.is_set(), "started": control.is_running})


if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)

