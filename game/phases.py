"""Phase handlers for day and night phases."""

import json
import random
from datetime import datetime
from typing import List, Dict, Optional, Any
from .game_state import GameState, Player
from .win_conditions import check_win_conditions
from .error_logger import log_json_parse_failure
from llm.openrouter_client import OpenRouterClient, LLMCancelledException
from llm.prompts import (
    build_night_prompt,
    build_day_discussion_prompt,
    build_turn_poll_prompt,
    build_day_voting_prompt,
    build_mafia_vote_prompt,
)


class GamePausedException(Exception):
    """Raised when the game is paused during phase execution."""
    pass


# Structured output schemas
VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "vote": {"type": "string"},
        "explanation": {"type": "string"}
    },
    "required": ["vote", "explanation"]
}

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "null"]},
        "reasoning": {"type": "string"}
    },
    "required": ["target", "reasoning"]
}

TURN_POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "wants_to_interrupt": {"type": "boolean"},
        "wants_to_respond": {"type": "boolean"},
        "wants_to_pass": {"type": "boolean"}
    },
    "required": ["wants_to_interrupt", "wants_to_respond", "wants_to_pass"]
}


def handle_night_phase(
    game_state: GameState,
    llm_client: OpenRouterClient,
    game_id: str = None,
    emit_callback=None,
    control: Any = None
) -> Dict:
    """
    Handle a complete night phase.

    Args:
        game_state: Current game state
        llm_client: LLM client for making API calls
        game_id: Optional game ID for emitting real-time updates
        emit_callback: Optional callback function(game_id, game_state) to emit updates
        control: Optional GameControl instance for pause/cancel support

    Returns:
        Dict with night action results

    Raises:
        GamePausedException: If the game is paused during execution
    """

    def check_pause():
        """Check if game is paused and raise exception if so."""
        if control and control.pause_event.is_set():
            raise GamePausedException("Game paused")

    def get_cancel_event():
        """Get the cancel event from control, or None."""
        return control.cancel_event if control else None

    def make_checkpoint(action_type: str, context: dict = None):
        """Create a checkpoint before an action."""
        if control:
            control.checkpoint = game_state.create_checkpoint(action_type, context)

    def restore_and_raise(message: str):
        """Restore from checkpoint and raise GamePausedException."""
        if control and control.checkpoint:
            game_state.restore_from_checkpoint(control.checkpoint)
        raise GamePausedException(message)

    game_state.phase = "night"
    game_state.add_event("phase_change", f"Night {game_state.day_number + 1} begins.", "all")

    # Emit update at start of night
    if emit_callback and game_id:
        emit_callback(game_id, game_state)
    
    night_results = {
        "mafia_votes": [],
        "doctor_protection": None,
        "sheriff_investigation": None,
        "vigilante_kill": None,
        "kills": [],
        "protected": None
    }
    
    alive_players = game_state.get_alive_players()
    alive_names = [p.name for p in alive_players]
    
    # 1. Mafia vote
    mafia_players = game_state.get_players_by_role("Mafia")
    if mafia_players:
        mafia_votes = []
        for i, mafia in enumerate(mafia_players):
            check_pause()  # Check before each player's action
            make_checkpoint("mafia_vote", {"player_index": i, "previous_votes": list(mafia_votes)})

            previous_votes = [{"player": v["player"], "target": v.get("target"), "reasoning": v.get("reasoning", "")}
                            for v in mafia_votes]
            prompt = build_mafia_vote_prompt(game_state, mafia, previous_votes)

            messages = [{"role": "user", "content": prompt}]
            mafia.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "mafia_vote",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            try:
                response = llm_client.call_model(
                    mafia.model,
                    messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": ACTION_SCHEMA}},
                    temperature=0.7,
                    cancel_event=get_cancel_event()
                )
            except LLMCancelledException:
                restore_and_raise("Cancelled during mafia vote")

            mafia.last_llm_context["response"] = response

            # Parse response
            target = None
            reasoning = ""
            if "structured_output" in response:
                target = response["structured_output"].get("target")
                reasoning = response["structured_output"].get("reasoning", "")
            else:
                # Try to parse from content
                try:
                    content = response["content"]
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0:
                        parsed = json.loads(content[json_start:json_end])
                        target = parsed.get("target")
                        reasoning = parsed.get("reasoning", "")
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log_json_parse_failure(
                        content=response.get("content", ""),
                        exception=e,
                        player_name=mafia.name,
                        fallback_used={"target": None, "reasoning": ""}
                    )
            
            mafia_votes.append({
                "player": mafia.name,
                "target": target,
                "reasoning": reasoning
            })
            
            # Add mafia discussion message (only visible to mafia)
            if reasoning:
                if target:
                    discussion_msg = f"[Mafia Discussion] I think we should target {target}. {reasoning}"
                else:
                    discussion_msg = f"[Mafia Discussion] I'm not sure who to target. {reasoning}"
                game_state.add_event("mafia_chat", discussion_msg, "mafia", player=mafia.name, priority=7)
            
            # Emit real-time update after each mafia vote
            if emit_callback and game_id:
                emit_callback(game_id, game_state)
        
        night_results["mafia_votes"] = mafia_votes
        
        # Tally mafia votes
        vote_counts = {}
        for vote in mafia_votes:
            target = vote["target"]
            if target:
                vote_counts[target] = vote_counts.get(target, 0) + 1
        
        if vote_counts:
            # Get target with most votes
            mafia_target = max(vote_counts.items(), key=lambda x: x[1])[0]
            night_results["mafia_kill_target"] = mafia_target
        else:
            night_results["mafia_kill_target"] = None
    
    # 2. Doctor protection
    doctor_players = game_state.get_players_by_role("Doctor")
    if doctor_players:
        check_pause()
        make_checkpoint("doctor_protect")

        doctor = doctor_players[0]
        prompt = build_night_prompt(game_state, doctor, "doctor_protect", alive_names)

        messages = [{"role": "user", "content": prompt}]
        doctor.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "doctor_protect",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        try:
            response = llm_client.call_model(
                doctor.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "doctor_action", "schema": ACTION_SCHEMA}},
                temperature=0.7,
                cancel_event=get_cancel_event()
            )
        except LLMCancelledException:
            restore_and_raise("Cancelled during doctor protect")

        doctor.last_llm_context["response"] = response

        target = None
        reasoning = ""
        if "structured_output" in response:
            target = response["structured_output"].get("target")
            reasoning = response["structured_output"].get("reasoning", "")
        else:
            try:
                content = response["content"]
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start >= 0:
                    parsed = json.loads(content[json_start:json_end])
                    target = parsed.get("target")
                    reasoning = parsed.get("reasoning", "")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                log_json_parse_failure(
                    content=response.get("content", ""),
                    exception=e,
                    player_name=doctor.name,
                    fallback_used={"target": None, "reasoning": ""}
                )

        # Validate: can't protect same person twice in a row
        if target and doctor.role.last_protected == target:
            attempted_target = target
            target = None
            # No public log - the Doctor's action is hidden
            # Add doctor monologue (only visible to doctor)
            monologue = f"[Doctor's Thoughts] I wanted to protect {attempted_target} again, but I can't protect the same person twice."
            if reasoning:
                monologue += f" {reasoning}"
            game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)
        else:
            night_results["doctor_protection"] = {
                "target": target,
                "reasoning": reasoning
            }
            if target:
                doctor.role.last_protected = target
                night_results["protected"] = target

            # Add doctor monologue (only visible to doctor)
            if reasoning:
                if target:
                    monologue = f"[Doctor's Thoughts] I'll protect {target} tonight. {reasoning}"
                else:
                    monologue = f"[Doctor's Thoughts] I'm choosing not to protect anyone this night. {reasoning}"
                game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)
        
        # Emit real-time update after doctor action
        if emit_callback and game_id:
            emit_callback(game_id, game_state)
    
    # 3. Sheriff investigation
    sheriff_players = game_state.get_players_by_role("Sheriff")
    if sheriff_players:
        check_pause()
        make_checkpoint("sheriff_investigate")

        sheriff = sheriff_players[0]
        prompt = build_night_prompt(game_state, sheriff, "sheriff_investigate", alive_names)

        messages = [{"role": "user", "content": prompt}]
        sheriff.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "sheriff_investigate",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        try:
            response = llm_client.call_model(
                sheriff.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "sheriff_action", "schema": ACTION_SCHEMA}},
                temperature=0.7,
                cancel_event=get_cancel_event()
            )
        except LLMCancelledException:
            restore_and_raise("Cancelled during sheriff investigate")

        sheriff.last_llm_context["response"] = response

        target = None
        reasoning = ""
        if "structured_output" in response:
            target = response["structured_output"].get("target")
            reasoning = response["structured_output"].get("reasoning", "")
        else:
            try:
                content = response["content"]
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start >= 0:
                    parsed = json.loads(content[json_start:json_end])
                    target = parsed.get("target")
                    reasoning = parsed.get("reasoning", "")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                log_json_parse_failure(
                    content=response.get("content", ""),
                    exception=e,
                    player_name=sheriff.name,
                    fallback_used={"target": None, "reasoning": ""}
                )

        if target:
            target_player = game_state.get_player_by_name(target)
            if target_player:
                result = "mafia" if target_player.team == "mafia" else "town"
                sheriff.role.investigations.append((target, result))
                night_results["sheriff_investigation"] = {
                    "target": target,
                    "result": result,
                    "reasoning": reasoning
                }

                # Add sheriff monologue (only visible to sheriff)
                if reasoning:
                    monologue = f"[Sheriff's Thoughts] I'm investigating {target} tonight. {reasoning}"
                    game_state.add_event("role_action", monologue, "sheriff", player=sheriff.name, priority=6)

                # Add investigation result as separate event (only visible to sheriff)
                game_state.add_event("role_action", f"Investigation result: {target} is {result.upper()}.",
                                    "sheriff", player=sheriff.name, priority=8,
                                    metadata={"target": target, "result": result})
        elif reasoning:
            # Sheriff chose not to investigate (only visible to sheriff)
            monologue = f"[Sheriff's Thoughts] I'm choosing not to investigate anyone this night. {reasoning}"
            game_state.add_event("role_action", monologue, "sheriff", player=sheriff.name, priority=6)
        
        # Emit real-time update after sheriff action
        if emit_callback and game_id:
            emit_callback(game_id, game_state)
    
    # 4. Vigilante kill
    vigilante_players = game_state.get_players_by_role("Vigilante")
    if vigilante_players:
        vigilante = vigilante_players[0]
        if not vigilante.role.bullet_used:
            check_pause()
            make_checkpoint("vigilante_kill")

            prompt = build_night_prompt(game_state, vigilante, "vigilante_kill", alive_names)

            messages = [{"role": "user", "content": prompt}]
            vigilante.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "vigilante_kill",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            try:
                response = llm_client.call_model(
                    vigilante.model,
                    messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "vigilante_action", "schema": ACTION_SCHEMA}},
                    temperature=0.7,
                    cancel_event=get_cancel_event()
                )
            except LLMCancelledException:
                restore_and_raise("Cancelled during vigilante kill")

            vigilante.last_llm_context["response"] = response

            target = None
            reasoning = ""
            if "structured_output" in response:
                target = response["structured_output"].get("target")
                reasoning = response["structured_output"].get("reasoning", "")
            else:
                try:
                    content = response["content"]
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0:
                        parsed = json.loads(content[json_start:json_end])
                        target = parsed.get("target")
                        reasoning = parsed.get("reasoning", "")
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log_json_parse_failure(
                        content=response.get("content", ""),
                        exception=e,
                        player_name=vigilante.name,
                        fallback_used={"target": None, "reasoning": ""}
                    )

            if target:
                vigilante.role.bullet_used = True
                night_results["vigilante_kill"] = {
                    "target": target,
                    "reasoning": reasoning
                }

                # Add vigilante monologue (only visible to vigilante)
                if reasoning:
                    monologue = f"[Vigilante's Thoughts] I'm using my bullet on {target}. {reasoning}"
                    game_state.add_event("role_action", monologue, "vigilante", player=vigilante.name, priority=6)
            elif reasoning:
                # Vigilante chose not to use bullet (only visible to vigilante)
                monologue = f"[Vigilante's Thoughts] I'm saving my bullet for now. {reasoning}"
                game_state.add_event("role_action", monologue, "vigilante", player=vigilante.name, priority=6)
            
            # Emit real-time update after vigilante action
            if emit_callback and game_id:
                emit_callback(game_id, game_state)
    
    # 5. Resolve night actions
    kills_this_night = []
    
    # Mafia kill (protection is hidden - if protected, simply no death occurs)
    if night_results.get("mafia_kill_target"):
        target = night_results["mafia_kill_target"]
        if target != night_results.get("protected"):
            if game_state.kill_player(target, "Killed during the night."):
                kills_this_night.append(target)
        # If protected, no log - the protection is hidden from all players

    # Vigilante kill (protection is hidden - if protected, simply no death occurs)
    if night_results.get("vigilante_kill") and night_results["vigilante_kill"]["target"]:
        target = night_results["vigilante_kill"]["target"]
        if target != night_results.get("protected"):
            if game_state.kill_player(target, "Killed during the night."):
                kills_this_night.append(target)
        # If protected, no log - the protection is hidden from all players
    
    night_results["kills"] = kills_this_night
    game_state.night_actions = night_results

    # Simple end-of-night message (deaths already logged individually by kill_player)
    if not kills_this_night:
        game_state.add_event("system", "No one died during the night.", "all")
    
    # Emit final update after resolving night actions
    if emit_callback and game_id:
        emit_callback(game_id, game_state)
    
    return night_results


def handle_day_phase(
    game_state: GameState,
    llm_client: OpenRouterClient,
    game_id: str = None,
    emit_callback=None,
    emit_status_callback=None,
    control: Any = None
) -> Dict:
    """
    Handle a complete day phase.

    Args:
        game_state: Current game state
        llm_client: LLM client for making API calls
        game_id: Optional game ID for emitting real-time updates
        emit_callback: Optional callback function(game_id, game_state) to emit updates
        emit_status_callback: Optional callback for discussion status updates
        control: Optional GameControl instance for pause/cancel support

    Returns:
        Dict with day phase results

    Raises:
        GamePausedException: If the game is paused during execution
    """

    def check_pause():
        """Check if game is paused and raise exception if so."""
        if control and control.pause_event.is_set():
            raise GamePausedException("Game paused")

    def get_cancel_event():
        """Get the cancel event from control, or None."""
        return control.cancel_event if control else None

    def make_checkpoint(action_type: str, context: dict = None):
        """Create a checkpoint before an action."""
        if control:
            control.checkpoint = game_state.create_checkpoint(action_type, context)

    def restore_and_raise(message: str):
        """Restore from checkpoint and raise GamePausedException."""
        if control and control.checkpoint:
            game_state.restore_from_checkpoint(control.checkpoint)
        raise GamePausedException(message)

    # Check if we're resuming from a paused state
    resuming = game_state.discussion_state is not None

    if not resuming:
        # Fresh day phase start
        game_state.phase = "day"
        game_state.day_number += 1
        game_state.add_event("phase_change", f"Day {game_state.day_number} begins.", "all")
    else:
        # Resuming from pause
        game_state.add_event("system", "Discussion resumed.", "all")
    
    day_results = {
        "discussion": [],
        "votes": [],
        "lynch_target": None
    }
    
    alive_players = game_state.get_alive_players()
    
    if not alive_players:
        return day_results

    # Night events already logged during night phase, no need to repeat

    # Emit update at start of day
    if emit_callback and game_id:
        emit_callback(game_id, game_state)

    # 1. Discussion phase - round-robin with interrupt system
    max_discussion_messages = 10  # Max total messages before cutoff
    alive_names = [p.name for p in alive_players]

    # Restore or initialize discussion state
    if resuming and game_state.discussion_state:
        # Restore from saved state
        saved = game_state.discussion_state
        discussion_messages = saved.get("discussion_messages", [])
        current_speaker_index = saved.get("current_speaker_index", 0)
        consecutive_no_interrupt_rounds = saved.get("consecutive_no_interrupt_rounds", 0)
        # Restore round-robin order by name, filtering out dead players
        saved_order_names = saved.get("round_robin_order_names", [])
        round_robin_order = []
        for name in saved_order_names:
            player = next((p for p in alive_players if p.name == name), None)
            if player:
                round_robin_order.append(player)
        # Add any new alive players not in the saved order
        for p in alive_players:
            if p not in round_robin_order:
                round_robin_order.append(p)
        # Clear the saved state since we're resuming
        game_state.discussion_state = None
    else:
        # Fresh start
        discussion_messages = []
        current_speaker_index = 0
        # Build round-robin order (randomize initial order for fairness)
        round_robin_order = list(alive_players)
        random.shuffle(round_robin_order)

    # Helper to emit discussion status for UI visibility
    def emit_status(action: str, waiting_player: str = None, extra: dict = None):
        if emit_status_callback and game_id:
            status = {
                "action": action,
                "waiting_player": waiting_player,
                "message_count": len(discussion_messages),
                "max_messages": max_discussion_messages,
                "interrupting_players": [],  # Will be populated during interrupt polling
            }
            if extra:
                status.update(extra)
            emit_status_callback(game_id, status)

    # Helper function to poll for turn actions (interrupt/respond/pass)
    def poll_for_turn_actions(exclude_player: str = None) -> tuple:
        """Poll all players to see who wants to interrupt, respond, or pass.

        Returns:
            tuple: (interrupting_players, responding_players, passing_players)

        Raises:
            LLMCancelledException: If cancelled during polling
        """
        interrupting_players = []
        responding_players = []
        passing_players = []
        players_waiting = []

        for player in alive_players:
            if player.name == exclude_player:
                continue
            players_waiting.append(player.name)

        # Emit status showing we're polling for turn actions
        emit_status("turn_polling", extra={"players_being_polled": players_waiting})

        for player in alive_players:
            if player.name == exclude_player:
                continue

            prompt = build_turn_poll_prompt(game_state, player)
            messages = [{"role": "user", "content": prompt}]

            try:
                # Emit waiting status for this player
                emit_status("waiting_turn_poll", waiting_player=player.name,
                           extra={"interrupting_players": interrupting_players})

                player.last_llm_context = {
                    "messages": messages,
                    "timestamp": datetime.now().isoformat(),
                    "action_type": "turn_poll",
                    "phase": game_state.phase,
                    "day": game_state.day_number
                }
                response = llm_client.call_model(
                    player.model,
                    messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "turn_poll", "schema": TURN_POLL_SCHEMA}},
                    temperature=0.3,
                    max_tokens=100,
                    cancel_event=get_cancel_event()
                )
                player.last_llm_context["response"] = response

                wants_to_interrupt = False
                wants_to_respond = False
                wants_to_pass = False

                if "structured_output" in response:
                    wants_to_interrupt = response["structured_output"].get("wants_to_interrupt", False)
                    wants_to_respond = response["structured_output"].get("wants_to_respond", False)
                    wants_to_pass = response["structured_output"].get("wants_to_pass", False)
                else:
                    try:
                        content = response.get("content", "")
                        json_start = content.find("{")
                        json_end = content.rfind("}") + 1
                        if json_start >= 0:
                            parsed = json.loads(content[json_start:json_end])
                            wants_to_interrupt = parsed.get("wants_to_interrupt", False)
                            wants_to_respond = parsed.get("wants_to_respond", False)
                            wants_to_pass = parsed.get("wants_to_pass", False)
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        # If we can't parse, assume no action
                        log_json_parse_failure(
                            content=response.get("content", ""),
                            exception=e,
                            player_name=player.name,
                            fallback_used={"wants_to_interrupt": False, "wants_to_respond": False, "wants_to_pass": False}
                        )

                if wants_to_interrupt:
                    interrupting_players.append(player.name)
                elif wants_to_respond:
                    responding_players.append(player.name)
                if wants_to_pass:
                    passing_players.append(player.name)

                # Emit updated status
                emit_status("waiting_turn_poll", waiting_player=player.name,
                           extra={"interrupting_players": interrupting_players, "responding_players": responding_players})
            except LLMCancelledException:
                raise  # Re-raise cancellation
            except Exception as e:
                # On error, assume no action - don't hold up the game
                continue

        return interrupting_players, responding_players, passing_players

    # Helper function to get a message from a player (no structured output required)
    def get_player_message(player: "Player", is_interrupt: bool = False, is_respond: bool = False) -> tuple:
        """Get a discussion message from a player.

        Returns:
            tuple: (message, failure_reason) - message is the text, failure_reason is None on success

        Raises:
            LLMCancelledException: If cancelled during message generation
        """
        prompt = build_day_discussion_prompt(game_state, player, is_interrupt=is_interrupt, is_respond=is_respond)
        messages = [{"role": "user", "content": prompt}]

        try:
            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "discussion_message_interrupt" if is_interrupt else "discussion_message",
                "phase": game_state.phase,
                "day": game_state.day_number
            }
            response = llm_client.call_model(
                player.model,
                messages,
                temperature=0.8,
                max_tokens=300,
                cancel_event=get_cancel_event()
            )
            player.last_llm_context["response"] = response

            # No structured output - just use the content directly
            raw_content = response.get("content", "")
            message = raw_content.strip()

            # Track if we had to extract from JSON
            extracted_from_json = False

            # Clean up the message - remove any JSON wrapper if the model added one
            if message.startswith("{") and "message" in message:
                try:
                    json_start = message.find("{")
                    json_end = message.rfind("}") + 1
                    if json_start >= 0:
                        parsed = json.loads(message[json_start:json_end])
                        if "message" in parsed:
                            message = parsed.get("message", "")
                            extracted_from_json = True
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log_json_parse_failure(
                        content=message,
                        exception=e,
                        player_name=player.name,
                        fallback_used=message
                    )

            # Store debug info
            player.last_llm_context["debug"] = {
                "raw_content_length": len(raw_content),
                "processed_message_length": len(message),
                "extracted_from_json": extracted_from_json,
                "raw_content_preview": raw_content[:200] if raw_content else "(empty)"
            }

            if not message:
                return ("", f"Empty response (raw length: {len(raw_content)}, preview: {raw_content[:100]})")

            return (message[:500], None)  # Limit message length
        except LLMCancelledException:
            raise  # Re-raise cancellation
        except Exception as e:
            error_msg = f"Exception: {type(e).__name__}: {str(e)}"
            player.last_llm_context["error"] = error_msg
            return ("", error_msg)

    # Emit initial discussion status
    emit_status("discussion_start")

    # Main discussion loop
    if not resuming:
        consecutive_no_interrupt_rounds = 0
    max_no_interrupt_rounds = 2  # End discussion if 2 full rounds with no interrupts

    while len(discussion_messages) < max_discussion_messages:
        # Check for pause at start of each round
        check_pause()

        # Get the current speaker from round-robin
        current_speaker = round_robin_order[current_speaker_index % len(round_robin_order)]

        # Check if current speaker is still alive
        if not current_speaker.alive:
            current_speaker_index += 1
            continue

        # Phase 1: Poll for interrupts (exclude current speaker)
        # Checkpoint before interrupt polling
        make_checkpoint("discussion_interrupt_poll", {
            "speaker_index": current_speaker_index,
            "messages_count": len(discussion_messages),
            "discussion_messages": list(discussion_messages),
            "consecutive_no_interrupt_rounds": consecutive_no_interrupt_rounds,
            "round_robin_order_names": [p.name for p in round_robin_order],
        })

        try:
            interrupting_players, responding_players, passing_players = poll_for_turn_actions(exclude_player=current_speaker.name)
        except LLMCancelledException:
            restore_and_raise("Cancelled during turn polling")

        # Phase 2: Determine who speaks (interrupt > respond > regular)
        is_interrupt = False
        is_respond = False
        if interrupting_players:
            # Random selection among those who want to interrupt
            selected_speaker_name = random.choice(interrupting_players)
            selected_speaker = next((p for p in alive_players if p.name == selected_speaker_name), None)
            is_interrupt = True
        elif responding_players:
            # Random selection among those who want to respond
            selected_speaker_name = random.choice(responding_players)
            selected_speaker = next((p for p in alive_players if p.name == selected_speaker_name), None)
            is_respond = True
        else:
            # No interrupts/responds - current speaker goes
            selected_speaker = current_speaker

        if not selected_speaker:
            current_speaker_index += 1
            continue

        # Phase 3: Get the message from the selected speaker
        emit_status("waiting_message", waiting_player=selected_speaker.name,
                   extra={"is_interrupt": is_interrupt, "is_respond": is_respond})

        # Checkpoint before getting message
        make_checkpoint("discussion_message", {
            "speaker": selected_speaker.name,
            "is_interrupt": is_interrupt,
            "is_respond": is_respond,
            "speaker_index": current_speaker_index,
            "messages_count": len(discussion_messages),
            "discussion_messages": list(discussion_messages),
            "consecutive_no_interrupt_rounds": consecutive_no_interrupt_rounds,
            "round_robin_order_names": [p.name for p in round_robin_order],
        })

        try:
            message, failure_reason = get_player_message(selected_speaker, is_interrupt=is_interrupt, is_respond=is_respond)
        except LLMCancelledException:
            restore_and_raise("Cancelled during discussion message")

        if message:
            # Determine turn type for UI display
            if is_interrupt:
                turn_type = "interrupt"
            elif is_respond:
                turn_type = "respond"
            else:
                turn_type = "regular"

            # Add the message to discussion
            game_state.add_event("discussion", message, "public", player=selected_speaker.name,
                                metadata={"turn_type": turn_type})
            discussion_messages.append({
                "player": selected_speaker.name,
                "message": message,
                "is_interrupt": is_interrupt,
                "is_respond": is_respond
            })
            day_results["discussion"].append({
                "player": selected_speaker.name,
                "message": message,
                "is_interrupt": is_interrupt,
                "is_respond": is_respond
            })

            # Move speaker to back of round-robin queue
            if selected_speaker in round_robin_order:
                round_robin_order.remove(selected_speaker)
                round_robin_order.append(selected_speaker)
                current_speaker_index = 0  # Reset index since order changed

            # Emit real-time update after each message
            if emit_callback and game_id:
                emit_callback(game_id, game_state)
        else:
            # Player failed to produce a message - add visible event
            if is_interrupt:
                turn_type = "interrupt"
            elif is_respond:
                turn_type = "response"
            else:
                turn_type = "turn"
            failure_msg = f"[{selected_speaker.name}'s {turn_type} produced no message: {failure_reason}]"
            game_state.add_event("system", failure_msg, "all", player=selected_speaker.name)

            # Still emit update so the failure is visible
            if emit_callback and game_id:
                emit_callback(game_id, game_state)

    emit_status("discussion_end")
    game_state.add_event("system", "Discussion phase ends.", "all")

    # Check for pause before voting
    check_pause()

    # 3. Voting phase - real-time, one player at a time
    game_state.add_event("system", "Voting phase begins.", "all")
    if emit_callback and game_id:
        emit_callback(game_id, game_state)

    votes = []
    alive_names = [p.name for p in alive_players]

    for i, player in enumerate(alive_players):
        check_pause()  # Check before each vote
        make_checkpoint("day_vote", {"voter_index": i, "previous_votes": list(votes)})

        prompt = build_day_voting_prompt(game_state, player)

        messages = [{"role": "user", "content": prompt}]

        try:
            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "day_vote",
                "phase": game_state.phase,
                "day": game_state.day_number
            }
            response = llm_client.call_model(
                player.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "vote", "schema": VOTE_SCHEMA}},
                temperature=0.7,
                cancel_event=get_cancel_event()
            )
            player.last_llm_context["response"] = response

            vote_target = "abstain"
            explanation = ""

            if "structured_output" in response:
                vote_target = response["structured_output"].get("vote", "abstain")
                explanation = response["structured_output"].get("explanation", "")
            else:
                try:
                    content = response["content"]
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0:
                        parsed = json.loads(content[json_start:json_end])
                        vote_target = parsed.get("vote", "abstain")
                        explanation = parsed.get("explanation", "")
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log_json_parse_failure(
                        content=response.get("content", ""),
                        exception=e,
                        player_name=player.name,
                        fallback_used={"vote": "abstain", "explanation": ""}
                    )

            # Validate vote
            if vote_target != "abstain" and vote_target not in alive_names:
                vote_target = "abstain"

            votes.append({
                "player": player.name,
                "vote": vote_target,
                "explanation": explanation
            })

            # Add vote with explanation as a single event
            if vote_target != "abstain":
                vote_msg = f"I vote to lynch {vote_target}."
                if explanation:
                    vote_msg += f" {explanation}"
            else:
                vote_msg = f"I abstain from voting."
                if explanation:
                    vote_msg += f" {explanation}"
            game_state.add_event("vote", vote_msg, "all", player=player.name, priority=8,
                                metadata={"target": vote_target})

            # Emit real-time update after each vote
            if emit_callback and game_id:
                emit_callback(game_id, game_state)
        except LLMCancelledException:
            restore_and_raise("Cancelled during voting")
        except Exception as e:
            # Default to abstain on error
            votes.append({
                "player": player.name,
                "vote": "abstain",
                "explanation": "Error processing vote"
            })

            # Emit update even on error
            if emit_callback and game_id:
                emit_callback(game_id, game_state)
    
    day_results["votes"] = votes

    # Tally votes (including abstain as a valid option)
    vote_counts = {}
    for vote in votes:
        target = vote["vote"]
        vote_counts[target] = vote_counts.get(target, 0) + 1

    if vote_counts:
        # Get the highest vote count
        max_votes = max(vote_counts.values())
        candidates = [name for name, count in vote_counts.items() if count == max_votes]

        # Tie results in no lynch
        if len(candidates) > 1:
            day_results["lynch_target"] = None
            game_state.add_event("vote_result", f"Tie in voting between {', '.join(candidates)}. No one was lynched.", "all")
        elif candidates[0] == "abstain":
            # Abstain won outright
            day_results["lynch_target"] = None
            game_state.add_event("vote_result", f"No one was lynched. Abstain received the most votes ({vote_counts['abstain']}).", "all")
        else:
            # Clear winner
            winner = candidates[0]
            day_results["lynch_target"] = winner
            game_state.kill_player(winner, f"Lynched by vote ({vote_counts[winner]} votes).")
    else:
        day_results["lynch_target"] = None
        game_state.add_event("vote_result", "No votes were cast.", "all")

    return day_results

