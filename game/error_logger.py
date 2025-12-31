"""Centralized error logging for Mafia AI application.

This module provides context-aware logging with game state tracking.
All logs are written to both file and console in plain text format.
"""

import logging
import traceback
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler


# ============================================================================
# SECTION 1: Context Management (Greenlet-Safe)
# ============================================================================

_game_context: ContextVar[Dict[str, Any]] = ContextVar('game_context', default={})


def set_game_context(
    game_id: str = None,
    phase: str = None,
    day_number: int = None,
    current_step: str = None,
    player_name: str = None
):
    """Update the current game context (partial updates supported).

    Args:
        game_id: Game identifier
        phase: Current phase (night/day)
        day_number: Current day number
        current_step: Current step identifier
        player_name: Current player name
    """
    context = _game_context.get().copy()

    if game_id is not None:
        context['game_id'] = game_id
    if phase is not None:
        context['phase'] = phase
    if day_number is not None:
        context['day_number'] = day_number
    if current_step is not None:
        context['current_step'] = current_step
    if player_name is not None:
        context['player_name'] = player_name

    _game_context.set(context)


def get_game_context() -> Dict[str, Any]:
    """Get the current game context."""
    return _game_context.get().copy()


def clear_game_context():
    """Clear the game context (for cleanup)."""
    _game_context.set({})


def format_context() -> str:
    """Format context for log messages: 'game_id:phase:day:step:player'"""
    context = _game_context.get()

    if not context:
        return "no-context"

    parts = []
    parts.append(context.get('game_id', 'unknown'))
    parts.append(context.get('phase', 'unknown'))
    parts.append(str(context.get('day_number', '?')))
    parts.append(context.get('current_step', 'unknown'))
    parts.append(context.get('player_name', 'unknown'))

    return ':'.join(parts)


# ============================================================================
# SECTION 2: Logger Configuration
# ============================================================================

class ContextualFormatter(logging.Formatter):
    """Custom formatter that includes game context in log messages."""

    def format(self, record):
        # Add context to record
        context_str = format_context()
        record.context = context_str if context_str else "no-context"
        return super().format(record)


def initialize_logging(log_dir: str = "logs", log_level: int = logging.INFO):
    """Initialize the logging system with file and console handlers.

    Creates log directory if it doesn't exist.
    Configures formatters for plain text output.
    Sets up rotation for log files.

    Args:
        log_dir: Directory for log files (default: "logs")
        log_level: Minimum log level to record (default: INFO)
    """
    # Create log directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    # Get or create logger
    logger = logging.getLogger("mafia_ai")
    logger.setLevel(log_level)

    # Clear any existing handlers
    logger.handlers.clear()

    # Create formatters
    formatter = ContextualFormatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(context)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler with rotation (10MB max, 5 backups)
    file_handler = RotatingFileHandler(
        log_path / "mafia_errors.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info("Logging system initialized")


# ============================================================================
# SECTION 3: High-Level Logging Functions
# ============================================================================

def log_exception(
    exception: Exception,
    message: str,
    player_name: str = None,
    extra_context: Dict[str, Any] = None
):
    """Log an exception with full traceback and context.

    Args:
        exception: The caught exception
        message: Descriptive message about what was being attempted
        player_name: Optional player name (overrides context)
        extra_context: Additional context to include
    """
    logger = _get_logger()

    # Temporarily override player_name in context if provided
    original_context = None
    if player_name is not None:
        original_context = get_game_context()
        set_game_context(player_name=player_name)

    try:
        # Build detailed message
        msg_parts = [message]

        if extra_context:
            msg_parts.append(f"Extra context: {extra_context}")

        msg_parts.append("Traceback:")
        msg_parts.append(_format_exception(exception))

        full_message = "\n".join(msg_parts)
        logger.error(full_message)

    finally:
        # Restore original context
        if original_context is not None:
            _game_context.set(original_context)


def log_empty_result(
    component: str,
    player_name: str = None,
    response_preview: str = None,
    extra_context: Dict[str, Any] = None
):
    """Log when an LLM response or structured output is empty.

    Args:
        component: What component returned empty (e.g., "openrouter_response")
        player_name: Optional player name
        response_preview: First 100 chars of response (if any)
        extra_context: Additional context
    """
    logger = _get_logger()

    # Temporarily override player_name in context if provided
    original_context = None
    if player_name is not None:
        original_context = get_game_context()
        set_game_context(player_name=player_name)

    try:
        msg_parts = [f"Empty result from {component}"]

        if response_preview:
            msg_parts.append(f"Response preview: {response_preview[:100]}")
        else:
            msg_parts.append("Response preview: (none)")

        if extra_context:
            for key, value in extra_context.items():
                msg_parts.append(f"{key}: {value}")

        full_message = "\n".join(msg_parts)
        logger.warning(full_message)

    finally:
        # Restore original context
        if original_context is not None:
            _game_context.set(original_context)


def log_json_parse_failure(
    content: str,
    exception: Exception,
    player_name: str = None,
    fallback_used: Any = None
):
    """Log JSON parsing failure with content preview.

    Args:
        content: The content that failed to parse
        exception: The JSONDecodeError or other exception
        player_name: Optional player name
        fallback_used: The fallback value used (for visibility)
    """
    logger = _get_logger()

    # Temporarily override player_name in context if provided
    original_context = None
    if player_name is not None:
        original_context = get_game_context()
        set_game_context(player_name=player_name)

    try:
        msg_parts = ["JSON parsing failed"]

        # Include content preview (first 200 chars)
        if content:
            preview = content[:200]
            if len(content) > 200:
                preview += "..."
            msg_parts.append(f"Content preview: {preview}")
        else:
            msg_parts.append("Content preview: (empty)")

        if fallback_used is not None:
            msg_parts.append(f"Fallback used: {fallback_used}")

        msg_parts.append("Exception:")
        msg_parts.append(str(exception))

        full_message = "\n".join(msg_parts)
        logger.error(full_message)

    finally:
        # Restore original context
        if original_context is not None:
            _game_context.set(original_context)


def log_warning(
    message: str,
    player_name: str = None,
    extra_context: Dict[str, Any] = None
):
    """Log a warning message with context.

    Args:
        message: Warning message
        player_name: Optional player name
        extra_context: Additional context
    """
    logger = _get_logger()

    # Temporarily override player_name in context if provided
    original_context = None
    if player_name is not None:
        original_context = get_game_context()
        set_game_context(player_name=player_name)

    try:
        msg_parts = [message]

        if extra_context:
            for key, value in extra_context.items():
                msg_parts.append(f"{key}: {value}")

        full_message = "\n".join(msg_parts)
        logger.warning(full_message)

    finally:
        # Restore original context
        if original_context is not None:
            _game_context.set(original_context)


def log_info(
    message: str,
    player_name: str = None,
    extra_context: Dict[str, Any] = None
):
    """Log an info message with context.

    Args:
        message: Info message
        player_name: Optional player name
        extra_context: Additional context
    """
    logger = _get_logger()

    # Temporarily override player_name in context if provided
    original_context = None
    if player_name is not None:
        original_context = get_game_context()
        set_game_context(player_name=player_name)

    try:
        msg_parts = [message]

        if extra_context:
            for key, value in extra_context.items():
                msg_parts.append(f"{key}: {value}")

        full_message = "\n".join(msg_parts)
        logger.info(full_message)

    finally:
        # Restore original context
        if original_context is not None:
            _game_context.set(original_context)


# ============================================================================
# SECTION 4: Internal Helpers
# ============================================================================

def _get_logger() -> logging.Logger:
    """Get the configured logger instance."""
    return logging.getLogger("mafia_ai")


def _format_exception(exception: Exception) -> str:
    """Format exception with full traceback."""
    return ''.join(traceback.format_exception(
        type(exception), exception, exception.__traceback__
    ))
