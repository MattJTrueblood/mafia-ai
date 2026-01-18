"""Prompt templates for different game phases and roles."""

from typing import List, Dict
from .builder import ContextBuilder
from .template_manager import get_template_manager


# Helper functions (preserved from original prompts.py)

def get_visible_events(game_state, viewing_player=None) -> list:
    """Get all events visible to a specific player, in chronological order.

    Visibility can be:
    - "all" or "public": visible to everyone
    - A list of player names: visible only to those players
    """
    if viewing_player is None:
        return [e for e in game_state.events if e.get("visibility") in ("all", "public")]

    player_name = viewing_player.name
    visible = []

    for event in game_state.events:
        visibility = event.get("visibility", "all")

        if visibility in ("all", "public"):
            visible.append(event)
        elif isinstance(visibility, list) and player_name in visibility:
            visible.append(event)

    return visible


def format_event_for_prompt(event) -> str:
    """Format a single event for display in a prompt."""
    player = event.get("player")
    message = event.get("message", "")
    event_type = event.get("type", "")

    if player and event_type in ("discussion", "vote", "mafia_chat", "role_action"):
        return f"{player}: {message}"
    else:
        return message


# Public API functions

def build_sheriff_post_investigation_prompt(game_state, player, target: str, result: str) -> str:
    """Build prompt for sheriff's reflection after seeing investigation result.

    Args:
        game_state: Current game state
        player: The sheriff player
        target: Name of the player investigated
        result: Investigation result ("mafia" or "town")

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)
    action_result = f"You just investigated {target} and learned they are {result.upper()}."
    context = builder.build_context(
        player=player,
        phase='post_role_action',
        action_result=action_result
    )
    return get_template_manager().render('night/post_role_action.jinja2', context)


# Placeholder for remaining functions
def build_night_prompt(game_state, player, action_type: str, available_targets: List[str]) -> str:
    """Build prompt for night phase actions (legacy function).

    This function is being phased out in favor of more specific role-based prompts.

    Args:
        game_state: Current game state
        player: The player taking action
        action_type: "mafia_vote", "doctor_protect", "sheriff_investigate", "vigilante_kill"
        available_targets: List of player names that can be targeted

    Returns:
        Prompt string
    """
    # Delegate to the appropriate specialized function based on action_type
    if action_type == "mafia_vote":
        return build_mafia_vote_prompt(game_state, player, [], [])
    elif action_type in ["doctor_protect", "sheriff_investigate", "vigilante_kill"]:
        role_map = {
            "doctor_protect": "doctor",
            "sheriff_investigate": "sheriff",
            "vigilante_kill": "vigilante"
        }
        role_type = role_map[action_type]
        # Use role_action directly
        return build_role_action_prompt(game_state, player, role_type, available_targets)
    else:
        raise ValueError(f"Unknown action_type: {action_type}")

def build_day_discussion_prompt(game_state, player, is_interrupt: bool = False, is_respond: bool = False) -> str:
    """Build prompt for day phase discussion.

    Args:
        game_state: Current game state
        player: The player speaking
        is_interrupt: Whether this is an interrupt (urgent) message
        is_respond: Whether this is a response (player was mentioned/asked)

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)
    context = builder.build_context(
        player=player,
        phase='day_discussion',
        is_interrupt=is_interrupt,
        is_respond=is_respond
    )
    return get_template_manager().render('day/discussion.jinja2', context)

def build_turn_poll_prompt(game_state, player) -> str:
    """Build prompt for players to indicate if they want to interrupt, respond, or pass.

    Args:
        game_state: Current game state
        player: The player being prompted

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)
    context = builder.build_context(
        player=player,
        phase='turn_poll'
    )
    return get_template_manager().render('day/turn_poll.jinja2', context)

def build_day_voting_prompt(game_state, player) -> str:
    """Build prompt for day phase voting.

    Args:
        game_state: Current game state
        player: The voting player

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    alive_players = game_state.get_alive_players()
    available_targets = [p.name for p in alive_players]  # Self-votes allowed

    context = builder.build_context(
        player=player,
        phase='day_voting',
        available_targets=available_targets
    )
    return get_template_manager().render('day/voting.jinja2', context)

def build_mafia_vote_prompt(game_state, player, previous_votes: List[Dict], discussion_messages: List[Dict] = None) -> str:
    """Build prompt for mafia night voting (after discussion).

    Args:
        game_state: Current game state
        player: The mafia player voting
        previous_votes: List of votes cast so far
        discussion_messages: Optional mafia discussion messages

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    alive_players = game_state.get_alive_players()
    available_targets = [p.name for p in alive_players]

    context = builder.build_context(
        player=player,
        phase='mafia_vote',
        available_targets=available_targets,
        previous_votes=previous_votes,
        discussion_messages=discussion_messages or []
    )
    return get_template_manager().render('night/mafia_vote.jinja2', context)

def build_mafia_discussion_prompt(game_state, player, previous_messages: List[Dict]) -> str:
    """Build prompt for mafia night discussion (before voting).

    Args:
        game_state: Current game state
        player: The mafia player
        previous_messages: List of previous discussion messages

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    alive_players = game_state.get_alive_players()
    available_targets = [p.name for p in alive_players]

    context = builder.build_context(
        player=player,
        phase='mafia_discussion',
        available_targets=available_targets,
        previous_messages=previous_messages
    )
    return get_template_manager().render('night/mafia_discussion.jinja2', context)

def build_role_discussion_prompt(game_state, player, role_type: str, available_targets: List[str]) -> str:
    """Build prompt for role's thinking/discussion phase (before action).

    Args:
        game_state: Current game state
        player: The player with the role
        role_type: "doctor", "sheriff", or "vigilante"
        available_targets: List of alive player names

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Role-specific action descriptions and constraints
    action_config = {
        "doctor": {
            "action_description": "who to protect tonight",
            "constraint": f"You cannot protect {player.role.last_protected} again (protected last night)."
            if hasattr(player.role, 'last_protected') and player.role.last_protected else None
        },
        "sheriff": {
            "action_description": "who to investigate tonight",
            "constraint": None
        },
        "vigilante": {
            "action_description": "whether to use your bullet tonight",
            "constraint": "You have already used your bullet."
            if hasattr(player.role, 'bullet_used') and player.role.bullet_used else None
        }
    }

    config = action_config.get(role_type, {})

    context = builder.build_context(
        player=player,
        phase='role_discussion',
        available_targets=available_targets,
        action_description=config.get("action_description", "your action"),
        constraint_message=config.get("constraint")
    )
    return get_template_manager().render('night/role_discussion.jinja2', context)

def build_role_action_prompt(game_state, player, role_type: str, available_targets: List[str], previous_discussion: str = "") -> str:
    """Build prompt for role's action decision (after discussion).

    Args:
        game_state: Current game state
        player: The player with the role
        role_type: "doctor", "sheriff", or "vigilante"
        available_targets: List of alive player names
        previous_discussion: Optional previous thinking/discussion

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Role-specific action descriptions and constraints
    action_config = {
        "doctor": {
            "action_description": "who to protect tonight",
            "constraint": f"You CANNOT protect {player.role.last_protected} (protected last night)."
            if hasattr(player.role, 'last_protected') and player.role.last_protected else None
        },
        "sheriff": {
            "action_description": "who to investigate tonight",
            "constraint": None
        },
        "vigilante": {
            "action_description": "whether to use your bullet tonight (or save it)",
            "constraint": None
        }
    }

    config = action_config.get(role_type, {})

    context = builder.build_context(
        player=player,
        phase='role_action',
        available_targets=available_targets,
        action_description=config.get("action_description", "your action"),
        constraint_message=config.get("constraint"),
        previous_discussion=previous_discussion
    )
    return get_template_manager().render('night/role_action.jinja2', context)

def build_postgame_discussion_prompt(game_state, player) -> str:
    """Build prompt for postgame discussion.

    Args:
        game_state: Current game state
        player: The player sharing their thoughts

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Prepare player list with roles
    all_players = []
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_players.append({
            'name': p.name,
            'role_text': role_text,
            'status': status
        })

    winner = "Town" if game_state.winner == "town" else "Mafia"

    context = builder.build_context(
        player=player,
        phase='postgame_discussion',
        all_players=all_players,
        winner=winner
    )
    return get_template_manager().render('postgame/discussion.jinja2', context)

def build_mvp_vote_prompt(game_state, player) -> str:
    """Build prompt for MVP voting.

    Args:
        game_state: Current game state
        player: The voting player

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Prepare player list with roles
    all_players = []
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_players.append({
            'name': p.name,
            'role_text': role_text,
            'status': status
        })

    other_players = [p.name for p in game_state.players if p.name != player.name]
    winner = "Town" if game_state.winner == "town" else "Mafia"

    context = builder.build_context(
        player=player,
        phase='mvp_voting',
        all_players=all_players,
        other_players=other_players,
        winner=winner
    )
    return get_template_manager().render('postgame/mvp_vote.jinja2', context)

def build_introduction_prompt(game_state, player) -> str:
    """Build prompt for Day 1 introduction messages.

    Args:
        game_state: Current game state
        player: The player introducing themselves

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)
    context = builder.build_context(
        player=player,
        phase='introduction'
    )
    return get_template_manager().render('day/introduction.jinja2', context)


def build_scratchpad_prompt(game_state, player, timing: str) -> str:
    """Build prompt for scratchpad strategic note writing.

    Args:
        game_state: Current game state
        player: The player writing notes
        timing: "day_start", "pre_vote", or "night_start"

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Timing-specific context
    timing_context = {
        "day_start": {
            "title": f"Day {game_state.day_number} Start",
            "description": "You're at the beginning of the day, before discussion starts."
        },
        "pre_vote": {
            "title": f"Day {game_state.day_number} Pre-Vote",
            "description": "Discussion has ended. You're about to vote on who to lynch."
        },
        "night_start": {
            "title": f"Night {game_state.day_number} Start",
            "description": "Night has begun. You'll soon take your night action."
        }
    }

    context = builder.build_context(
        player=player,
        phase='scratchpad',
        timing=timing,
        timing_title=timing_context[timing]["title"],
        timing_description=timing_context[timing]["description"]
    )
    return get_template_manager().render('scratchpad.jinja2', context)


def build_trashtalk_poll_prompt(game_state, player) -> str:
    """Build prompt for trashtalk polling (who wants to speak).

    Args:
        game_state: Current game state
        player: The player being polled

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Prepare player list with roles (all revealed in postgame)
    all_players = []
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_players.append({
            'name': p.name,
            'role_text': role_text,
            'status': status
        })

    winner = "Town" if game_state.winner == "town" else "Mafia" if game_state.winner == "mafia" else game_state.winner

    context = builder.build_context(
        player=player,
        phase='trashtalk_poll',
        all_players=all_players,
        winner=winner
    )
    return get_template_manager().render('postgame/trashtalk_poll.jinja2', context)


def build_trashtalk_message_prompt(game_state, player, is_interrupt: bool = False, is_respond: bool = False) -> str:
    """Build prompt for trashtalk message generation.

    Args:
        game_state: Current game state
        player: The player speaking
        is_interrupt: Whether this is an interrupt
        is_respond: Whether this is a response to the last speaker

    Returns:
        Prompt string
    """
    builder = ContextBuilder(game_state)

    # Prepare player list with roles (all revealed in postgame)
    all_players = []
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_players.append({
            'name': p.name,
            'role_text': role_text,
            'status': status
        })

    winner = "Town" if game_state.winner == "town" else "Mafia" if game_state.winner == "mafia" else game_state.winner

    context = builder.build_context(
        player=player,
        phase='trashtalk_message',
        all_players=all_players,
        winner=winner,
        is_interrupt=is_interrupt,
        is_respond=is_respond
    )
    return get_template_manager().render('postgame/trashtalk_message.jinja2', context)
