"""
Step-based game runner.

Single entry point for executing game steps. Each call to run_step()
executes exactly ONE atomic step and advances the game state.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StepResult:
    """Result of executing a single step."""

    next_step: Optional[str] = None   # Override automatic next step
    next_index: int = 0               # Index within next step (for multi-player steps)
    data: Dict[str, Any] = field(default_factory=dict)  # Step-specific output data
    events_emitted: List[Dict] = field(default_factory=list)  # Events added this step


@dataclass
class StepContext:
    """
    Everything a step handler needs to execute.

    Passed to every handler function. Handlers should not access
    global state or imports - everything comes through this context.
    """

    # Core state
    game_state: Any  # GameState instance
    llm_client: Any  # OpenRouterClient instance
    rules: Any       # GameRules instance

    # Callbacks for UI/status updates
    emit_status: Callable = None           # emit_status(status_type, **kwargs)
    emit_player_status: Callable = None    # emit_player_status(player_name, status)
    emit_event: Callable = None            # emit_event(event_dict)

    # Cancellation support for pause/resume
    cancel_event: Any = None  # gevent.Event or similar

    @property
    def phase(self) -> str:
        """Current game phase (night/day/postgame)."""
        return self.game_state.phase

    @property
    def day_number(self) -> int:
        """Current day number."""
        return self.game_state.day_number

    @property
    def step_index(self) -> int:
        """Current index within a multi-player step."""
        return self.game_state.step_index

    @property
    def phase_data(self) -> Dict:
        """Accumulated data for current phase (votes, messages, etc)."""
        return self.game_state.phase_data

    def get_alive_players(self) -> List:
        """Get all alive players."""
        return self.game_state.get_alive_players()

    def get_players_by_role(self, role_name: str) -> List:
        """Get alive players with a specific role."""
        return self.game_state.get_players_by_role(role_name)

    def get_player_by_name(self, name: str):
        """Get player by name."""
        return self.game_state.get_player_by_name(name)

    def add_event(self, event_type: str, message: str, visibility="all", **kwargs) -> Dict:
        """Add an event to the game log."""
        event = self.game_state.add_event(event_type, message, visibility, **kwargs)
        if self.emit_event:
            self.emit_event(event)
        return event

    def is_cancelled(self) -> bool:
        """Check if execution has been cancelled (for pause support)."""
        if self.cancel_event:
            return self.cancel_event.is_set()
        return False


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_step(
    game_state,
    llm_client,
    rules,
    emit_status: Callable = None,
    emit_player_status: Callable = None,
    emit_event: Callable = None,
    cancel_event = None,
) -> StepResult:
    """
    Execute exactly ONE atomic step, then return.

    This is THE entry point for game execution. Call repeatedly
    until game_state.game_over is True.

    Args:
        game_state: GameState instance
        llm_client: OpenRouterClient instance
        rules: GameRules instance
        emit_status: Callback for UI status updates
        emit_player_status: Callback for player-specific status
        emit_event: Callback for game events
        cancel_event: Event to check for pause requests

    Returns:
        StepResult with next step info and any output data
    """
    # Import handlers (lazy to avoid circular imports)
    from .step_handlers import STEP_HANDLERS

    # Build context for this step
    ctx = StepContext(
        game_state=game_state,
        llm_client=llm_client,
        rules=rules,
        emit_status=emit_status,
        emit_player_status=emit_player_status,
        emit_event=emit_event,
        cancel_event=cancel_event,
    )

    # Get handler for current step
    current_step = game_state.current_step
    handler = STEP_HANDLERS.get(current_step)

    if not handler:
        raise ValueError(f"No handler registered for step: {current_step}")

    # Execute the step
    result = handler(ctx)

    # Advance game state
    if result.next_step:
        game_state.current_step = result.next_step
        game_state.step_index = result.next_index
    else:
        # Use automatic step advancement from phases.py
        from .phases import get_next_step
        next_step, next_index = get_next_step(game_state, rules)
        game_state.current_step = next_step
        game_state.step_index = next_index

    return result
