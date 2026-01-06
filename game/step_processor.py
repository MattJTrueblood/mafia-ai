"""
Step-based game processor.

This module provides a unified step processor that executes the game
one step at a time. Each step is a single atomic action (typically one LLM call).
The game can be paused between any two steps.
"""

import json
import random
import gevent
import logging
from gevent import Greenlet
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .game_state import GameState
from .win_conditions import check_win_conditions
from llm.openrouter_client import OpenRouterClient, LLMCancelledException
from llm.prompts import (
    build_night_prompt,
    build_day_discussion_prompt,
    build_turn_poll_prompt,
    build_day_voting_prompt,
    build_mafia_vote_prompt,
    build_mafia_discussion_prompt,
    build_role_discussion_prompt,
    build_role_action_prompt,
    build_postgame_discussion_prompt,
    build_mvp_vote_prompt,
    build_sheriff_post_investigation_prompt,
)


# Structured output schemas
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "null"]}
    },
    "required": ["target"]
}

TARGET_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "null"]}
    },
    "required": ["target"]
}

MVP_VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "reason": {"type": "string"}
    },
    "required": ["target", "reason"]
}

VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "vote": {"type": "string"},
        "explanation": {"type": "string"}
    },
    "required": ["vote", "explanation"]
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


class StepResult:
    """Result of executing a single step."""

    def __init__(self, completed: bool = True, next_step: str = None, next_index: int = 0):
        self.completed = completed  # Whether the step completed successfully
        self.next_step = next_step  # Next step to execute (None = determine automatically)
        self.next_index = next_index  # Index for next step


def process_step(
    game_state: GameState,
    llm_client: OpenRouterClient,
    cancel_event: Any = None,
    emit_callback: Callable = None,
    emit_status_callback: Callable = None,
    emit_player_status_callback: Callable = None,
    game_id: str = None,
) -> StepResult:
    """
    Process a single step in the game.

    This function executes exactly ONE atomic action based on the current
    game state (current_step and step_index). After execution, it updates
    the game state and returns.

    Args:
        game_state: Current game state
        llm_client: LLM client for making API calls
        cancel_event: Optional event to check for cancellation
        emit_callback: Optional callback to emit state updates
        emit_status_callback: Optional callback for discussion status
        emit_player_status_callback: Universal callback for player API status
        game_id: Game ID for callbacks

    Returns:
        StepResult indicating what to do next

    Raises:
        LLMCancelledException: If cancelled during LLM call
    """

    def emit_update():
        if emit_callback and game_id:
            emit_callback(game_id, game_state)

    def emit_status(action: str, **kwargs):
        if emit_status_callback and game_id:
            status = {"action": action, **kwargs}
            emit_status_callback(game_id, status)

    def emit_player_status(player_name: str, status: str):
        """Emit player pending/complete status for UI."""
        if emit_player_status_callback and game_id:
            emit_player_status_callback(game_id, player_name, status)

    def call_llm_with_status(player, model: str, messages: List, **kwargs):
        """
        Universal wrapper for LLM calls that emits pending/complete status.

        Emits 'pending' before the call and 'complete' after, regardless of success/failure.
        This ensures the UI always shows "..." while a player's API call is in flight.
        """
        emit_player_status(player.name, "pending")
        try:
            result = llm_client.call_model(model, messages, **kwargs)
            return result
        finally:
            emit_player_status(player.name, "complete")

    step = game_state.current_step
    index = game_state.step_index

    # =========================================================================
    # NIGHT PHASE STEPS
    # =========================================================================

    if step == GameState.STEP_NIGHT_START:
        # Initialize night phase
        game_state.phase_data = {
            "mafia_discussion_messages": [],
            "mafia_votes": [],
            "doctor_discussion": None,
            "doctor_protection": None,
            "sheriff_discussion": None,
            "sheriff_investigation": None,
            "vigilante_discussion": None,
            "vigilante_kill": None,
            "protected_player": None,
        }
        game_state.add_event("phase_change", f"Night {game_state.day_number} begins.", "all")
        game_state.add_event("system", "Mafia night actions begin.", "mafia")
        emit_update()

        # Advance to night scratchpad (special roles only)
        game_state.current_step = GameState.STEP_SCRATCHPAD_NIGHT_START
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_SCRATCHPAD_NIGHT_START:
        alive_players = game_state.get_alive_players()

        # Filter to only special roles
        eligible_players = [p for p in alive_players if _should_write_night_scratchpad(p)]

        if eligible_players:
            def scratchpad_func(player):
                return _execute_scratchpad_writing(
                    game_state, player, "night_start",
                    llm_client, cancel_event, emit_player_status
                )

            _execute_parallel_votes(
                eligible_players, scratchpad_func, emit_update,
                game_state, cancel_event
            )

        # Don't add event (night is silent)
        emit_update()

        # Move to mafia discussion
        game_state.current_step = GameState.STEP_MAFIA_DISCUSSION
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_MAFIA_DISCUSSION:
        mafia_players = game_state.get_players_by_role("Mafia")

        if index == 0:
            # First mafia member - add phase marker
            game_state.add_event("system", "Mafia Discussion phase begins.", "mafia")
            emit_update()

        if index >= len(mafia_players):
            # All mafia have discussed, move to voting
            game_state.add_event("system", "Mafia Discussion phase ends.", "mafia")
            game_state.add_event("system", "Mafia vote phase begins.", "mafia")
            game_state.current_step = GameState.STEP_MAFIA_VOTE
            game_state.step_index = 0
            emit_update()
            return StepResult()

        # Get this mafia member's discussion message
        mafia = mafia_players[index]
        previous_messages = game_state.phase_data.get("mafia_discussion_messages", [])

        message = _execute_mafia_discussion(game_state, mafia, previous_messages, llm_client, cancel_event, emit_player_status)
        game_state.phase_data["mafia_discussion_messages"].append({
            "player": mafia.name,
            "message": message
        })

        # Log the discussion message
        game_state.add_event("mafia_chat", f"[Mafia Discussion] {mafia.name}: {message}", "mafia",
                            player=mafia.name, priority=7)
        emit_update()

        # Advance to next mafia member
        game_state.step_index = index + 1
        return StepResult()

    elif step == GameState.STEP_MAFIA_VOTE:
        mafia_players = game_state.get_players_by_role("Mafia")
        discussion_messages = game_state.phase_data.get("mafia_discussion_messages", [])
        alive_names = [p.name for p in game_state.get_alive_players()]

        # Define vote function for each mafia member
        def vote_func(mafia):
            prompt = build_mafia_vote_prompt(game_state, mafia, [], discussion_messages)
            messages = [{"role": "user", "content": prompt}]

            mafia.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "mafia_vote",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            response = call_llm_with_status(
                mafia, mafia.model, messages,
                response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": TARGET_ONLY_SCHEMA}},
                temperature=0.7,
                cancel_event=cancel_event
            )
            mafia.last_llm_context["response"] = response

            target = _parse_target_response(response)
            if target and target not in alive_names:
                target = None

            # Log the vote immediately when this player completes
            vote_msg = f"[Mafia Vote] {mafia.name} votes to kill {target}" if target else f"[Mafia Vote] {mafia.name} abstains"
            game_state.add_event("mafia_chat", vote_msg, "mafia", player=mafia.name, priority=7)

            return {"player": mafia.name, "target": target}

        # Execute all votes in parallel
        results = _execute_parallel_votes(
            players=mafia_players,
            vote_func=vote_func,
            emit_update=emit_update,
            game_state=game_state,
            cancel_event=cancel_event
        )

        # Store all votes
        game_state.phase_data["mafia_votes"] = results

        # Tally votes and announce result
        _tally_mafia_votes(game_state)
        target = game_state.phase_data.get("mafia_kill_target")
        if target:
            game_state.add_event("system", f"Mafia has chosen to kill {target}.", "mafia")
        game_state.add_event("system", "Mafia night actions end.", "mafia")
        game_state.current_step = GameState.STEP_DOCTOR_DISCUSS
        game_state.step_index = 0
        emit_update()
        return StepResult()

    elif step == GameState.STEP_DOCTOR_DISCUSS:
        doctor_players = game_state.get_players_by_role("Doctor")

        if not doctor_players:
            # No doctor, skip to sheriff
            game_state.current_step = GameState.STEP_SHERIFF_DISCUSS
            game_state.step_index = 0
            return StepResult()

        doctor = doctor_players[0]
        game_state.add_event("system", "Doctor night phase begins.", "doctor")

        # Get doctor's discussion/thinking
        discussion = _execute_role_discussion(game_state, doctor, "doctor", llm_client, cancel_event, emit_player_status)
        game_state.phase_data["doctor_discussion"] = discussion

        game_state.add_event("role_action", f"[Doctor Discussion] {doctor.name}: {discussion}",
                            "doctor", player=doctor.name, priority=6)
        emit_update()

        game_state.current_step = GameState.STEP_DOCTOR_ACT
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_DOCTOR_ACT:
        doctor_players = game_state.get_players_by_role("Doctor")
        doctor = doctor_players[0]

        # Get actual target
        target = _execute_role_action(game_state, doctor, "doctor", llm_client, cancel_event, emit_player_status)

        # Check if same target as last night
        if target and doctor.role.last_protected == target:
            game_state.add_event("role_action",
                f"Doctor {doctor.name} cannot protect {target} again (protected last night).",
                "doctor", player=doctor.name, priority=7)
            target = None
        elif target:
            doctor.role.last_protected = target
            game_state.phase_data["protected_player"] = target

            game_state.add_event("role_action", f"Doctor {doctor.name} protects {target}.",
                                "doctor", player=doctor.name, priority=7)

        game_state.phase_data["doctor_protection"] = {"target": target}
        game_state.add_event("system", "Doctor night phase ends.", "doctor")
        emit_update()

        game_state.current_step = GameState.STEP_SHERIFF_DISCUSS
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_SHERIFF_DISCUSS:
        sheriff_players = game_state.get_players_by_role("Sheriff")

        if not sheriff_players:
            # No sheriff, skip to vigilante
            game_state.current_step = GameState.STEP_VIGILANTE_DISCUSS
            game_state.step_index = 0
            return StepResult()

        sheriff = sheriff_players[0]
        game_state.add_event("system", "Sheriff night phase begins.", "sheriff")

        # Get sheriff's discussion/thinking
        discussion = _execute_role_discussion(game_state, sheriff, "sheriff", llm_client, cancel_event, emit_player_status)
        game_state.phase_data["sheriff_discussion"] = discussion

        game_state.add_event("role_action", f"[Sheriff Discussion] {sheriff.name}: {discussion}",
                            "sheriff", player=sheriff.name, priority=6)
        emit_update()

        game_state.current_step = GameState.STEP_SHERIFF_ACT
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_SHERIFF_ACT:
        sheriff_players = game_state.get_players_by_role("Sheriff")
        sheriff = sheriff_players[0]

        # Get actual target
        target = _execute_role_action(game_state, sheriff, "sheriff", llm_client, cancel_event, emit_player_status)

        result = None
        if target:
            target_player = game_state.get_player_by_name(target)
            if target_player:
                result = "mafia" if target_player.team == "mafia" else "town"
                sheriff.role.investigations.append((target, result))

                game_state.add_event("role_action", f"Sheriff {sheriff.name} investigates {target}.",
                                    "sheriff", player=sheriff.name, priority=7)
                game_state.add_event("role_action", f"{target} is {result.upper()}!",
                                    "sheriff", player=sheriff.name, priority=8,
                                    metadata={"target": target, "result": result})

                # Get sheriff's reaction to the result
                reaction = _execute_sheriff_post_investigation(
                    game_state, sheriff, target, result, llm_client, cancel_event, emit_player_status
                )
                if reaction:
                    game_state.add_event("role_action", f"[Sheriff Discussion] {sheriff.name}: {reaction}",
                                        "sheriff", player=sheriff.name, priority=9)

        game_state.phase_data["sheriff_investigation"] = {"target": target, "result": result}
        game_state.add_event("system", "Sheriff night phase ends.", "sheriff")
        emit_update()

        game_state.current_step = GameState.STEP_VIGILANTE_DISCUSS
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VIGILANTE_DISCUSS:
        vigilante_players = game_state.get_players_by_role("Vigilante")

        if not vigilante_players:
            # No vigilante, skip to night resolve
            game_state.current_step = GameState.STEP_NIGHT_RESOLVE
            game_state.step_index = 0
            return StepResult()

        vigilante = vigilante_players[0]

        if vigilante.role.bullet_used:
            # Bullet already used, skip
            game_state.current_step = GameState.STEP_NIGHT_RESOLVE
            game_state.step_index = 0
            return StepResult()

        game_state.add_event("system", "Vigilante night phase begins.", "vigilante")

        # Get vigilante's discussion/thinking
        discussion = _execute_role_discussion(game_state, vigilante, "vigilante", llm_client, cancel_event, emit_player_status)
        game_state.phase_data["vigilante_discussion"] = discussion

        game_state.add_event("role_action", f"[Vigilante Discussion] {vigilante.name}: {discussion}",
                            "vigilante", player=vigilante.name, priority=6)
        emit_update()

        game_state.current_step = GameState.STEP_VIGILANTE_ACT
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VIGILANTE_ACT:
        vigilante_players = game_state.get_players_by_role("Vigilante")
        vigilante = vigilante_players[0]

        # Get actual target
        target = _execute_role_action(game_state, vigilante, "vigilante", llm_client, cancel_event, emit_player_status)

        if target:
            vigilante.role.bullet_used = True
            game_state.add_event("role_action", f"Vigilante shoots {target} tonight.",
                                "vigilante", player=vigilante.name, priority=7)
        else:
            game_state.add_event("role_action", "Vigilante shoots nobody tonight.",
                                "vigilante", player=vigilante.name, priority=7)

        game_state.phase_data["vigilante_kill"] = {"target": target}
        game_state.add_event("system", "Vigilante night phase ends.", "vigilante")
        emit_update()

        game_state.current_step = GameState.STEP_NIGHT_RESOLVE
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_NIGHT_RESOLVE:
        _resolve_night_actions(game_state)
        game_state.add_event("phase_change", f"Night {game_state.day_number + 1} ends.", "all")
        emit_update()

        # Check win conditions
        winner = check_win_conditions(game_state)
        if winner:
            game_state.winner = winner
            # Transition to postgame instead of ending immediately
            game_state.current_step = GameState.STEP_POSTGAME_REVEAL
            game_state.step_index = 0
            emit_update()
            return StepResult()

        # Transition to day
        game_state.start_day_phase()
        emit_update()
        return StepResult()

    # =========================================================================
    # DAY PHASE STEPS
    # =========================================================================

    elif step == GameState.STEP_DAY_START:
        game_state.add_event("phase_change", f"Day {game_state.day_number} begins.", "all")
        # Add remaining players count
        alive = game_state.get_alive_players()
        names = ", ".join([p.name for p in alive])
        game_state.add_event("system", f"Remaining players ({len(alive)}): {names}", "all")

        # Check if this is the introduction day (Day 1)
        if game_state.day_number == 1:
            game_state.add_event("system", "Introduction phase begins. Each player will introduce themselves.", "all")
            emit_update()
            emit_status("introduction_start", message_count=0, max_messages=len(alive))

            # Move to introduction messages (simple round-robin)
            game_state.current_step = GameState.STEP_INTRODUCTION_MESSAGE
            game_state.step_index = 0
            return StepResult()
        else:
            # Regular discussion day
            game_state.add_event("system", f"Day {game_state.day_number} discussion phase begins.", "all")
            emit_update()
            emit_status("discussion_start", message_count=0, max_messages=10)

            # Move to scratchpad writing (Day 2+)
            game_state.current_step = GameState.STEP_SCRATCHPAD_DAY_START
            game_state.step_index = 0
            return StepResult()

    elif step == GameState.STEP_SCRATCHPAD_DAY_START:
        # Skip scratchpad on Day 1 (introduction day, no strategic context yet)
        if game_state.day_number == 1:
            game_state.current_step = GameState.STEP_DISCUSSION_POLL
            game_state.step_index = 0
            return StepResult()

        alive_players = game_state.get_alive_players()

        # Define scratchpad function for parallel execution
        def scratchpad_func(player):
            return _execute_scratchpad_writing(
                game_state, player, "day_start",
                llm_client, cancel_event, emit_player_status
            )

        # Execute in parallel for all alive players
        _execute_parallel_votes(
            alive_players, scratchpad_func, emit_update,
            game_state, cancel_event
        )

        # Add event (private, no content logged)
        game_state.add_event("system", "Players wrote strategic notes.", "all")
        emit_update()

        # Move to discussion polling
        game_state.current_step = GameState.STEP_DISCUSSION_POLL
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_INTRODUCTION_MESSAGE:
        # Simple round-robin introductions for Day 1
        speaker_order = game_state.phase_data.get("speaker_order", [])
        current_idx = game_state.step_index

        if current_idx >= len(speaker_order):
            # All players have introduced themselves - move to voting
            game_state.add_event("system", "Introduction phase complete.", "all")
            emit_status("turn_polling", waiting_player=None)
            emit_update()
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            return StepResult()

        # Get current speaker
        speaker_name = speaker_order[current_idx]
        speaker = game_state.get_player_by_name(speaker_name)

        if not speaker or not speaker.alive:
            # Skip dead/missing player, move to next
            game_state.step_index += 1
            return StepResult()

        emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=False, is_respond=False)

        # Get introduction from player
        introduction = _get_introduction_message(game_state, speaker, llm_client, cancel_event, emit_player_status)

        if introduction:
            # Add introduction to game log
            game_state.add_event("discussion", introduction, "public", player=speaker_name,
                                metadata={"turn_type": "introduction"})

            # Track in phase data for history
            if "discussion_messages" not in game_state.phase_data:
                game_state.phase_data["discussion_messages"] = []
            game_state.phase_data["discussion_messages"].append({
                "player": speaker_name,
                "message": introduction,
                "is_interrupt": False,
                "is_respond": False
            })

        emit_update()

        # Move to next player
        game_state.step_index += 1
        return StepResult()

    elif step == GameState.STEP_DISCUSSION_POLL:

        # Check if discussion should end
        messages = game_state.phase_data.get("discussion_messages", [])
        max_messages = 10

        logging.info(f"[POLL] Starting poll round. Messages so far: {len(messages)}/{max_messages}")

        if len(messages) >= max_messages:
            # Max messages reached - end discussion
            logging.info(f"[POLL] Max messages reached, moving to pre-vote scratchpad")
            emit_status("discussion_end")
            game_state.add_event("system", f"Day {game_state.day_number} discussion phase ends.", "all")
            game_state.current_step = GameState.STEP_SCRATCHPAD_PRE_VOTE
            game_state.step_index = 0
            emit_update()
            return StepResult()

        # Get current speaker from round-robin
        speaker_order = game_state.phase_data.get("speaker_order", [])
        speaker_idx = game_state.phase_data.get("current_speaker_index", 0)

        logging.info(f"[POLL] Speaker order: {speaker_order}, current_speaker_index: {speaker_idx}")

        if not speaker_order:
            # No speakers, move to voting
            emit_status("turn_polling", waiting_player=None)
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            return StepResult()

        # Get the last speaker (to prevent consecutive messages)
        last_speaker = game_state.phase_data.get("last_speaker", None)

        # Find the next valid round-robin speaker (skip dead players and last speaker)
        current_speaker_name = None
        current_speaker = None
        attempts = 0
        while attempts < len(speaker_order):
            candidate_name = speaker_order[speaker_idx % len(speaker_order)]
            candidate = game_state.get_player_by_name(candidate_name)

            if candidate and candidate.alive and candidate_name != last_speaker:
                current_speaker_name = candidate_name
                current_speaker = candidate
                break

            # Skip this candidate (dead or was last speaker)
            speaker_idx += 1
            game_state.phase_data["current_speaker_index"] = speaker_idx
            attempts += 1

        if not current_speaker:
            # No valid speakers found, move to voting
            logging.info(f"[POLL] No valid speaker found, moving to voting")
            emit_status("turn_polling", waiting_player=None)
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            return StepResult()

        logging.info(f"[POLL] Current speaker: {current_speaker_name}, last_speaker: {last_speaker}")

        # Check if last message was a respond - if so, block further responds
        # This prevents infinite respond chains
        last_was_respond = game_state.phase_data.get("last_was_respond", False)

        # Poll for interrupts, responds, and passes (exclude only the last speaker)
        emit_status("turn_polling", waiting_player=None)
        logging.info(f"[POLL] About to poll players (excluding last_speaker: {last_speaker})")
        interrupting, responding, passing = _poll_for_turn_actions(
            game_state, last_speaker, llm_client, cancel_event, emit_status, emit_player_status
        )

        logging.info(f"[POLL] Poll results: interrupting={interrupting}, responding={responding}, passing={passing}")

        # If last message was a respond, ignore all respond requests (only allow interrupts)
        if last_was_respond:
            logging.info(f"[POLL] Last message was respond, blocking further responds")
            responding = []

        # Track players who passed this round
        round_passes = game_state.phase_data.get("round_passes", [])
        logging.info(f"[POLL] round_passes BEFORE update: {round_passes}")
        for passer in passing:
            if passer not in round_passes:
                round_passes.append(passer)
        game_state.phase_data["round_passes"] = round_passes
        logging.info(f"[POLL] round_passes AFTER update: {round_passes}")

        # Emit status with polling results
        emit_status("turn_poll_result",
            interrupting_players=interrupting,
            responding_players=responding,
            passing_players=round_passes)

        if interrupting:
            logging.info(f"[POLL] DECISION: Someone interrupted -> moving to MESSAGE")
            # Someone wants to interrupt - pick whoever spoke least recently
            interrupter_name = _select_speaker_by_recency(interrupting, game_state)
            game_state.phase_data["next_speaker"] = interrupter_name
            game_state.phase_data["is_interrupt"] = True
            game_state.phase_data["is_respond"] = False
        elif responding:
            logging.info(f"[POLL] DECISION: Someone responded -> moving to MESSAGE")
            # Someone wants to respond - pick whoever spoke least recently
            responder_name = _select_speaker_by_recency(responding, game_state)
            game_state.phase_data["next_speaker"] = responder_name
            game_state.phase_data["is_interrupt"] = False
            game_state.phase_data["is_respond"] = True
        else:
            # No interrupts or responds - find first speaker who didn't pass
            logging.info(f"[POLL] No interrupts/responds. Finding first speaker who didn't pass.")
            logging.info(f"[POLL] Speaker order: {speaker_order}, round_passes: {round_passes}")

            # Search through speaker order starting from current position
            chosen_speaker = None
            search_attempts = 0
            search_idx = speaker_idx

            while search_attempts < len(speaker_order):
                candidate_name = speaker_order[search_idx % len(speaker_order)]
                candidate = game_state.get_player_by_name(candidate_name)

                # Check if candidate is alive and didn't pass
                if candidate and candidate.alive and candidate_name != last_speaker:
                    if candidate_name not in round_passes:
                        # Found someone who didn't pass
                        chosen_speaker = candidate_name
                        game_state.phase_data["current_speaker_index"] = search_idx
                        logging.info(f"[POLL] Found speaker who didn't pass: {chosen_speaker} at index {search_idx}")
                        break

                search_idx += 1
                search_attempts += 1

            # If everyone passed, force the current speaker to speak anyway
            if not chosen_speaker:
                chosen_speaker = current_speaker_name
                logging.info(f"[POLL] Everyone passed! Forcing current speaker {chosen_speaker} to speak anyway")

            game_state.phase_data["next_speaker"] = chosen_speaker
            game_state.phase_data["is_interrupt"] = False
            game_state.phase_data["is_respond"] = False
            logging.info(f"[POLL] DECISION: {chosen_speaker} will speak -> moving to MESSAGE")

        # Move to getting the message
        logging.info(f"[POLL] Moving to DISCUSSION_MESSAGE step for speaker: {game_state.phase_data.get('next_speaker')}")
        game_state.current_step = GameState.STEP_DISCUSSION_MESSAGE
        game_state.step_index = len(messages)
        return StepResult()

    elif step == GameState.STEP_SCRATCHPAD_PRE_VOTE:
        alive_players = game_state.get_alive_players()

        def scratchpad_func(player):
            return _execute_scratchpad_writing(
                game_state, player, "pre_vote",
                llm_client, cancel_event, emit_player_status
            )

        _execute_parallel_votes(
            alive_players, scratchpad_func, emit_update,
            game_state, cancel_event
        )

        game_state.add_event("system", "Players wrote strategic notes.", "all")
        emit_status("turn_polling", waiting_player=None)
        emit_update()

        # Move to voting
        game_state.current_step = GameState.STEP_VOTING
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_DISCUSSION_MESSAGE:

        speaker_name = game_state.phase_data.get("next_speaker")
        is_interrupt = game_state.phase_data.get("is_interrupt", False)
        is_respond = game_state.phase_data.get("is_respond", False)

        logging.info(f"[MESSAGE] Getting message from {speaker_name} (interrupt={is_interrupt}, respond={is_respond})")

        if not speaker_name:
            # No speaker, go back to polling
            logging.info(f"[MESSAGE] No speaker set, returning to poll")
            game_state.current_step = GameState.STEP_DISCUSSION_POLL
            return StepResult()

        speaker = game_state.get_player_by_name(speaker_name)
        if not speaker or not speaker.alive:
            logging.info(f"[MESSAGE] Speaker not found or dead, returning to poll")
            game_state.current_step = GameState.STEP_DISCUSSION_POLL
            return StepResult()

        emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=is_interrupt, is_respond=is_respond)

        message = _get_discussion_message(game_state, speaker, is_interrupt, is_respond, llm_client, cancel_event, emit_player_status)

        if message:
            logging.info(f"[MESSAGE] Got message from {speaker_name}: {message[:50]}...")
            # Track message index for recency-based speaker selection
            msg_index = len(game_state.phase_data["discussion_messages"])
            game_state.phase_data["player_last_message_index"][speaker_name] = msg_index

            # Determine turn type for UI display (not included in LLM context)
            if is_interrupt:
                turn_type = "interrupt"
            elif is_respond:
                turn_type = "respond"
            else:
                turn_type = "regular"

            game_state.add_event("discussion", message, "public", player=speaker_name,
                                metadata={"turn_type": turn_type})
            game_state.phase_data["discussion_messages"].append({
                "player": speaker_name,
                "message": message,
                "is_interrupt": is_interrupt,
                "is_respond": is_respond
            })
            # Track last speaker to prevent consecutive messages
            game_state.phase_data["last_speaker"] = speaker_name
            # Track if last message was a respond (to block respond chains)
            game_state.phase_data["last_was_respond"] = is_respond
            # Clear round passes after successful message (prevents accumulation)
            logging.info(f"[MESSAGE] Clearing round_passes (was: {game_state.phase_data.get('round_passes', [])})")
            game_state.phase_data["round_passes"] = []

            # Move speaker to back of round-robin queue (for all message types)
            speaker_order = game_state.phase_data.get("speaker_order", [])
            if speaker_name in speaker_order:
                speaker_order.remove(speaker_name)
                speaker_order.append(speaker_name)
                game_state.phase_data["speaker_order"] = speaker_order
                # Reset speaker index since order changed
                game_state.phase_data["current_speaker_index"] = 0

        emit_update()

        # Go back to polling
        logging.info(f"[MESSAGE] Message complete, returning to DISCUSSION_POLL")
        game_state.current_step = GameState.STEP_DISCUSSION_POLL
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VOTING:
        alive_players = game_state.get_alive_players()
        alive_names = [p.name for p in alive_players]

        game_state.add_event("system", f"Day {game_state.day_number} voting phase begins.", "all")
        emit_update()

        # Define vote function for each player
        def vote_func(player):
            prompt = build_day_voting_prompt(game_state, player)
            messages = [{"role": "user", "content": prompt}]

            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "day_vote",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            response = call_llm_with_status(
                player, player.model, messages,
                response_format={"type": "json_schema", "json_schema": {"name": "vote", "schema": VOTE_SCHEMA}},
                temperature=0.7,
                cancel_event=cancel_event
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
                    idx = content.find("{")
                    if idx >= 0:
                        parsed = json.loads(content[idx:content.rfind("}")+1])
                        vote_target = parsed.get("vote", "abstain")
                        explanation = parsed.get("explanation", "")
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logging.error(f"JSON parse failed for {player.name}: {e}, using fallback vote=abstain")

            # Validate vote
            if vote_target != "abstain" and vote_target not in alive_names:
                vote_target = "abstain"

            # Log the vote immediately when this player completes
            if vote_target != "abstain":
                msg = f"I vote to lynch {vote_target}."
            else:
                msg = "I abstain from voting."
            if explanation:
                msg += f" {explanation}"

            game_state.add_event("vote", msg, "all", player=player.name, priority=8,
                                metadata={"target": vote_target})

            return {"player": player.name, "vote": vote_target, "explanation": explanation}

        # Execute all votes in parallel
        results = _execute_parallel_votes(
            players=alive_players,
            vote_func=vote_func,
            emit_update=emit_update,
            game_state=game_state,
            cancel_event=cancel_event
        )

        # Store all votes
        game_state.phase_data["votes"] = results

        # Proceed to resolve
        game_state.current_step = GameState.STEP_VOTING_RESOLVE
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VOTING_RESOLVE:
        _resolve_voting(game_state)
        game_state.add_event("system", f"Day {game_state.day_number} voting phase ends.", "all")
        game_state.add_event("system", f"Day {game_state.day_number} ends.", "all")
        emit_update()

        # Check win conditions
        winner = check_win_conditions(game_state)
        if winner:
            game_state.winner = winner
            # Transition to postgame instead of ending immediately
            game_state.current_step = GameState.STEP_POSTGAME_REVEAL
            game_state.step_index = 0
            emit_update()
            return StepResult()

        # Transition to night
        game_state.start_night_phase()
        emit_update()
        return StepResult()

    # =========================================================================
    # POSTGAME PHASE STEPS
    # =========================================================================

    elif step == GameState.STEP_POSTGAME_REVEAL:
        # Reveal all roles
        winner_text = "TOWN" if game_state.winner == "town" else "MAFIA"
        game_state.add_event("system", f"{winner_text} WINS!", "all")
        game_state.add_event("system", "", "all")  # Empty line
        game_state.add_event("system", "ROLE REVEAL:", "all")
        for player in game_state.players:
            role_text = "mafia" if player.team == "mafia" else player.role.name.lower()
            game_state.add_event("system", f"{player.name}: {role_text}", "all")

        game_state.add_event("system", "Postgame discussion phase begins.", "all")
        game_state.phase_data["postgame_messages"] = []
        game_state.phase_data["mvp_votes"] = []
        emit_update()

        game_state.current_step = GameState.STEP_POSTGAME_DISCUSSION
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_POSTGAME_DISCUSSION:
        all_players = game_state.players  # Include dead players

        if index >= len(all_players):
            game_state.add_event("system", "Postgame discussion phase ends.", "all")
            game_state.add_event("system", "MVP voting phase begins.", "all")
            game_state.current_step = GameState.STEP_MVP_VOTING
            game_state.step_index = 0
            emit_update()
            return StepResult()

        player = all_players[index]
        message = _execute_postgame_discussion(game_state, player, llm_client, cancel_event, emit_player_status)

        if message:
            game_state.add_event("discussion", message, "all", player=player.name)
            game_state.phase_data["postgame_messages"].append({
                "player": player.name,
                "message": message
            })
        emit_update()

        game_state.step_index = index + 1
        return StepResult()

    elif step == GameState.STEP_MVP_VOTING:
        all_players = game_state.players
        all_names = [p.name for p in all_players]

        # Define vote function for each player
        def mvp_vote_func(player):
            prompt = build_mvp_vote_prompt(game_state, player)
            messages = [{"role": "user", "content": prompt}]

            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "mvp_vote",
                "phase": "postgame",
                "day": game_state.day_number
            }

            try:
                response = call_llm_with_status(
                    player, player.model, messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "mvp_vote", "schema": MVP_VOTE_SCHEMA}},
                    temperature=0.7,
                    cancel_event=cancel_event
                )
                player.last_llm_context["response"] = response

                target = None
                reason = ""

                if "structured_output" in response:
                    target = response["structured_output"].get("target")
                    reason = response["structured_output"].get("reason", "")
                else:
                    try:
                        content = response.get("content", "")
                        idx = content.find("{")
                        if idx >= 0:
                            parsed = json.loads(content[idx:content.rfind("}")+1])
                            target = parsed.get("target")
                            reason = parsed.get("reason", "")
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logging.error(f"JSON parse failed for {player.name}: {e}")

                # Validate: can't vote for self, must be valid player
                if target == player.name or (target and target not in all_names):
                    others = [p.name for p in all_players if p.name != player.name]
                    target = random.choice(others) if others else None
                    reason = reason or "Good game."

                if not target:
                    others = [p.name for p in all_players if p.name != player.name]
                    target = random.choice(others) if others else None
                    reason = reason or "Good game."

                # Log immediately
                game_state.add_event("vote", f"I vote {target}. {reason}", "all", player=player.name)

                return {"player": player.name, "target": target, "reason": reason}

            except LLMCancelledException:
                raise
            except Exception:
                others = [p.name for p in all_players if p.name != player.name]
                target = random.choice(others) if others else None
                game_state.add_event("vote", f"I vote {target}. Good game.", "all", player=player.name)
                return {"player": player.name, "target": target, "reason": "Good game."}

        # Execute all MVP votes in parallel
        results = _execute_parallel_votes(
            players=all_players,
            vote_func=mvp_vote_func,
            emit_update=emit_update,
            game_state=game_state,
            cancel_event=cancel_event
        )

        # Store all votes
        game_state.phase_data["mvp_votes"] = results

        # Tally MVP votes and announce winner
        _resolve_mvp_voting(game_state)
        game_state.game_over = True
        game_state.current_step = GameState.STEP_GAME_END
        emit_update()
        return StepResult()

    elif step == GameState.STEP_GAME_END:
        # Game is fully over
        return StepResult()

    else:
        # Unknown step - shouldn't happen
        raise ValueError(f"Unknown step: {step}")


# =============================================================================
# NIGHT ACTION EXECUTORS
# =============================================================================

def _execute_mafia_vote(
    game_state: GameState,
    mafia,
    previous_votes: list,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute a single mafia member's vote."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prev = [{"player": v["player"], "target": v.get("target")}
            for v in previous_votes]

    prompt = build_mafia_vote_prompt(game_state, mafia, prev)
    messages = [{"role": "user", "content": prompt}]

    mafia.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "mafia_vote",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    response = llm_client.call_model(
        mafia.model, messages,
        response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": ACTION_SCHEMA}},
        temperature=0.7,
        cancel_event=cancel_event
    )
    mafia.last_llm_context["response"] = response

    target = _parse_action_response(response)

    # Log mafia discussion
    if target:
        msg = f"[Mafia Discussion] I think we should target {target}."
    else:
        msg = f"[Mafia Discussion] I'm not sure who to target."
    game_state.add_event("mafia_chat", msg, "mafia", player=mafia.name, priority=7)

    return {"player": mafia.name, "target": target}


def _tally_mafia_votes(game_state: GameState):
    """Tally mafia votes and determine kill target."""
    votes = game_state.phase_data.get("mafia_votes", [])
    vote_counts = {}
    for v in votes:
        t = v.get("target")
        if t:
            vote_counts[t] = vote_counts.get(t, 0) + 1

    if vote_counts:
        mafia_target = max(vote_counts.items(), key=lambda x: x[1])[0]
        game_state.phase_data["mafia_kill_target"] = mafia_target
    else:
        game_state.phase_data["mafia_kill_target"] = None


def _execute_doctor_protect(
    game_state: GameState,
    doctor,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute doctor protection action."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prompt = build_night_prompt(game_state, doctor, "doctor_protect", alive_names)
    messages = [{"role": "user", "content": prompt}]

    doctor.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "doctor_protect",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    response = llm_client.call_model(
        doctor.model, messages,
        response_format={"type": "json_schema", "json_schema": {"name": "doctor_action", "schema": ACTION_SCHEMA}},
        temperature=0.7,
        cancel_event=cancel_event
    )
    doctor.last_llm_context["response"] = response

    target = _parse_action_response(response)

    # Check if same target as last night
    if target and doctor.role.last_protected == target:
        monologue = f"[Doctor's Thoughts] I wanted to protect {target} again, but I can't protect the same person twice."
        game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)
        target = None
    else:
        if target:
            doctor.role.last_protected = target
            monologue = f"[Doctor's Thoughts] I'll protect {target} tonight."
        else:
            monologue = f"[Doctor's Thoughts] I'm choosing not to protect anyone."
        game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)

    return {"target": target}


def _execute_sheriff_investigate(
    game_state: GameState,
    sheriff,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute sheriff investigation action."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prompt = build_night_prompt(game_state, sheriff, "sheriff_investigate", alive_names)
    messages = [{"role": "user", "content": prompt}]

    sheriff.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "sheriff_investigate",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    response = llm_client.call_model(
        sheriff.model, messages,
        response_format={"type": "json_schema", "json_schema": {"name": "sheriff_action", "schema": ACTION_SCHEMA}},
        temperature=0.7,
        cancel_event=cancel_event
    )
    sheriff.last_llm_context["response"] = response

    target = _parse_action_response(response)

    result = None
    if target:
        target_player = game_state.get_player_by_name(target)
        if target_player:
            result = "mafia" if target_player.team == "mafia" else "town"
            sheriff.role.investigations.append((target, result))

            game_state.add_event("role_action",
                f"[Sheriff's Thoughts] I'm investigating {target}.",
                "sheriff", player=sheriff.name, priority=6)
            game_state.add_event("role_action",
                f"Investigation result: {target} is {result.UPPER()}.",
                "sheriff", player=sheriff.name, priority=8,
                metadata={"target": target, "result": result})

    return {"target": target, "result": result}


def _execute_vigilante_kill(
    game_state: GameState,
    vigilante,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute vigilante kill action."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prompt = build_night_prompt(game_state, vigilante, "vigilante_kill", alive_names)
    messages = [{"role": "user", "content": prompt}]

    vigilante.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "vigilante_kill",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    response = llm_client.call_model(
        vigilante.model, messages,
        response_format={"type": "json_schema", "json_schema": {"name": "vigilante_action", "schema": ACTION_SCHEMA}},
        temperature=0.7,
        cancel_event=cancel_event
    )
    vigilante.last_llm_context["response"] = response

    target = _parse_action_response(response)

    if target:
        vigilante.role.bullet_used = True
        game_state.add_event("role_action",
            f"[Vigilante's Thoughts] I'm using my bullet on {target}.",
            "vigilante", player=vigilante.name, priority=6)
    else:
        game_state.add_event("role_action",
            f"[Vigilante's Thoughts] I'm saving my bullet.",
            "vigilante", player=vigilante.name, priority=6)

    return {"target": target}


def _resolve_night_actions(game_state: GameState):
    """Resolve night actions and apply kills."""
    protected = game_state.phase_data.get("protected_player")
    kills = []

    # Mafia kill
    mafia_target = game_state.phase_data.get("mafia_kill_target")
    if mafia_target and mafia_target != protected:
        target_player = game_state.get_player_by_name(mafia_target)
        if target_player and target_player.alive:
            target_player.alive = False
            game_state.add_event("death", f"{mafia_target} has been found dead, killed during the night!",
                                "all", metadata={"player": mafia_target, "reason": "mafia_kill"})
            kills.append(mafia_target)

    # Vigilante kill
    vig_data = game_state.phase_data.get("vigilante_kill")
    if vig_data and vig_data.get("target"):
        vig_target = vig_data["target"]
        if vig_target != protected and vig_target not in kills:
            target_player = game_state.get_player_by_name(vig_target)
            if target_player and target_player.alive:
                target_player.alive = False
                game_state.add_event("death", f"{vig_target} has been found dead, killed during the night!",
                                    "all", metadata={"player": vig_target, "reason": "vigilante_kill"})
                kills.append(vig_target)

    if not kills:
        game_state.add_event("system", "Nobody was killed last night.", "all")


# =============================================================================
# DAY ACTION EXECUTORS
# =============================================================================

def _select_speaker_by_recency(candidates: List[str], game_state: GameState) -> str:
    """Select candidate whose last message was least recent. Random if tied.

    Players who haven't spoken yet get highest priority (index -1).
    """
    if len(candidates) == 1:
        return candidates[0]

    last_indices = game_state.phase_data.get("player_last_message_index", {})

    def recency_key(name):
        return last_indices.get(name, -1)  # -1 = never spoken = highest priority

    min_index = min(recency_key(c) for c in candidates)
    tied = [c for c in candidates if recency_key(c) == min_index]
    return random.choice(tied)


def _poll_for_turn_actions(
    game_state: GameState,
    exclude_player: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_status,
    emit_player_status = None
) -> tuple:
    """Poll all players to see who wants to interrupt, respond, or pass.
    Uses parallel execution for faster polling.

    Returns:
        Tuple of (interrupting_players, responding_players, passing_players)
    """
    alive = game_state.get_alive_players()
    players_to_poll = [p for p in alive if p.name != exclude_player]

    if not players_to_poll:
        return [], [], []

    results = [None] * len(players_to_poll)

    def check_single_player(idx: int, player):
        """Check a single player for interrupt/respond/pass."""
        try:
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledException("Turn poll cancelled")

            # Emit pending status
            if emit_player_status:
                emit_player_status(player.name, "pending")

            prompt = build_turn_poll_prompt(game_state, player)
            messages = [{"role": "user", "content": prompt}]

            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "turn_poll",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            response = llm_client.call_model(
                player.model, messages,
                response_format={"type": "json_schema", "json_schema": {"name": "turn_poll", "schema": TURN_POLL_SCHEMA}},
                temperature=0.3,
                cancel_event=cancel_event
            )
            player.last_llm_context["response"] = response

            wants_interrupt = False
            wants_respond = False
            wants_pass = False

            # Check for empty response - log warning but continue
            content = response.get("content", "")
            # Only warn if BOTH content and structured_output are missing
            if not content and "structured_output" not in response:
                logging.warning(f"Empty result from turn_poll for {player.name} (model: {player.model})")

            # Parse response based on what's available
            if "structured_output" not in response and not content:
                # Treat as "wait for turn" - all False
                pass  # Variables already initialized to False above
            elif "structured_output" in response:
                wants_interrupt = response["structured_output"].get("wants_to_interrupt", False)
                wants_respond = response["structured_output"].get("wants_to_respond", False)
                wants_pass = response["structured_output"].get("wants_to_pass", False)
            else:
                try:
                    idx_json = content.find("{")
                    if idx_json >= 0:
                        parsed = json.loads(content[idx_json:content.rfind("}")+1])
                        wants_interrupt = parsed.get("wants_to_interrupt", False)
                        wants_respond = parsed.get("wants_to_respond", False)
                        wants_pass = parsed.get("wants_to_pass", False)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logging.error(f"JSON parse failed for {player.name}: {e}")

            result = {
                "player": player.name,
                "wants_to_interrupt": wants_interrupt,
                "wants_to_respond": wants_respond,
                "wants_to_pass": wants_pass
            }
            results[idx] = result

        except LLMCancelledException:
            raise
        except Exception as e:
            import traceback
            print(f"[TurnPoll] EXCEPTION for {player.name} ({player.model}): {e}")
            traceback.print_exc()
            # Store error in context so it shows in debug panel
            player.last_llm_context["response"] = {"content": "", "error": str(e)}
            player.last_llm_context["error"] = str(e)
            results[idx] = {
                "player": player.name,
                "wants_to_interrupt": False,
                "wants_to_respond": False,
                "wants_to_pass": False,
                "error": True
            }
        finally:
            # Emit complete status
            if emit_player_status:
                emit_player_status(player.name, "complete")

    # Spawn greenlets for all players
    greenlets = []
    for idx, player in enumerate(players_to_poll):
        g = gevent.spawn(check_single_player, idx, player)
        greenlets.append(g)

    # Wait for all to complete
    gevent.joinall(greenlets, raise_error=True)

    # Collect results - interrupt takes priority over respond
    interrupting = []
    responding = []
    passing = []
    for result in results:
        if result:
            if result.get("wants_to_interrupt"):
                interrupting.append(result["player"])
            elif result.get("wants_to_respond"):
                # Only count as responding if not interrupting
                responding.append(result["player"])
            if result.get("wants_to_pass"):
                passing.append(result["player"])

    return interrupting, responding, passing


def _get_introduction_message(
    game_state: GameState,
    player,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> Optional[str]:
    """Get an introduction message from a player on Day 1."""
    from llm.prompts import build_introduction_prompt

    prompt = build_introduction_prompt(game_state, player)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "introduction_message",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            temperature=0.9,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        content = response.get("content", "").strip()
        return content if content else None

    except LLMCancelledException:
        raise
    except Exception as e:
        import traceback
        print(f"[Introduction] EXCEPTION for {player.name}: {e}")
        traceback.print_exc()
        player.last_llm_context["error"] = str(e)
        return None
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _get_discussion_message(
    game_state: GameState,
    player,
    is_interrupt: bool,
    is_respond: bool,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> Optional[str]:
    """Get a discussion message from a player."""
    prompt = build_day_discussion_prompt(game_state, player, is_interrupt=is_interrupt, is_respond=is_respond)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        # Determine action type for logging
        if is_interrupt:
            action_type = "discussion_message_interrupt"
        elif is_respond:
            action_type = "discussion_message_respond"
        else:
            action_type = "discussion_message"

        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            temperature=0.8,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        content = response.get("content", "").strip()

        # Clean up JSON wrapper if present
        if content.startswith("{") and "message" in content:
            try:
                parsed = json.loads(content)
                content = parsed.get("message", content)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logging.error(f"JSON parse failed for {player.name}: {e}")

        # Strip surrounding quotes if present
        content = _strip_quotes(content)
        # Strip player name prefix if present
        content = _strip_player_name_prefix(content, player.name)
        return content[:500] if content else None

    except LLMCancelledException:
        raise
    except Exception:
        return None
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _execute_day_vote(
    game_state: GameState,
    player,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute a single player's day vote."""
    prompt = build_day_voting_prompt(game_state, player)
    messages = [{"role": "user", "content": prompt}]

    player.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "day_vote",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    try:
        response = llm_client.call_model(
            player.model, messages,
            response_format={"type": "json_schema", "json_schema": {"name": "vote", "schema": VOTE_SCHEMA}},
            temperature=0.7,
            cancel_event=cancel_event
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
                idx = content.find("{")
                if idx >= 0:
                    parsed = json.loads(content[idx:content.rfind("}")+1])
                    vote_target = parsed.get("vote", "abstain")
                    explanation = parsed.get("explanation", "")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logging.error(f"JSON parse failed for {player.name}: {e}")

        # Validate vote
        alive_names = [p.name for p in game_state.get_alive_players()]
        if vote_target != "abstain" and vote_target not in alive_names:
            vote_target = "abstain"

        # Log the vote
        if vote_target != "abstain":
            msg = f"I vote to lynch {vote_target}."
        else:
            msg = "I abstain from voting."
        if explanation:
            msg += f" {explanation}"

        game_state.add_event("vote", msg, "all", player=player.name, priority=8,
                            metadata={"target": vote_target})

        return {"player": player.name, "vote": vote_target, "explanation": explanation}

    except LLMCancelledException:
        raise
    except Exception:
        return {"player": player.name, "vote": "abstain", "explanation": "Error processing vote"}


def _resolve_voting(game_state: GameState):
    """Resolve voting and apply lynch. Requires MAJORITY to lynch."""
    votes = game_state.phase_data.get("votes", [])
    vote_counts = {}

    for v in votes:
        target = v.get("vote", "abstain")
        vote_counts[target] = vote_counts.get(target, 0) + 1

    if not vote_counts:
        game_state.add_event("vote_result", "No votes were cast.", "all")
        return

    alive_count = len(game_state.get_alive_players())
    majority_threshold = (alive_count // 2) + 1  # More than half

    # Check if any non-abstain candidate has majority
    lynch_target = None
    lynch_votes = 0
    for name, count in vote_counts.items():
        if name != "abstain" and count >= majority_threshold:
            lynch_target = name
            lynch_votes = count
            break

    if lynch_target:
        # Majority achieved - lynch occurs
        target_player = game_state.get_player_by_name(lynch_target)
        game_state.kill_player(lynch_target, f"Lynched by vote ({lynch_votes} votes).")
        # Role flip
        if target_player:
            role_flip = "MAFIA" if target_player.team == "mafia" else "TOWN"
            game_state.add_event("system", f"{lynch_target} was {role_flip}.", "all")
    else:
        # No majority - no lynch
        game_state.add_event("vote_result",
            "Nobody died, as no player received a majority of votes.", "all")


# =============================================================================
# PARALLEL EXECUTION HELPERS
# =============================================================================

def _execute_parallel_votes(
    players: List,
    vote_func: Callable,
    emit_update: Callable,
    game_state: GameState,
    cancel_event: Any = None
) -> List[Dict]:
    """
    Execute voting for multiple players in parallel.

    The vote_func should use call_llm_with_status internally to emit pending/complete
    status for the universal player status tracking.

    Args:
        players: List of players who will vote
        vote_func: Function to call for each player, should return dict with vote result
        emit_update: Callback to emit game state updates
        game_state: Current game state
        cancel_event: Optional cancellation event

    Returns:
        List of vote results in player order
    """
    results = [None] * len(players)

    def execute_single_vote(idx: int, player):
        """Execute a single vote and store result."""
        try:
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledException("Vote cancelled")

            result = vote_func(player)
            results[idx] = result

            # Emit game state update to show the vote in the log
            emit_update()

        except LLMCancelledException:
            raise
        except Exception as e:
            # On error, store a default result
            results[idx] = {"player": player.name, "error": str(e)}

    # Spawn greenlets for all players
    greenlets = []
    for idx, player in enumerate(players):
        g = gevent.spawn(execute_single_vote, idx, player)
        greenlets.append(g)

    # Wait for all to complete
    gevent.joinall(greenlets, raise_error=True)

    return results


# =============================================================================
# HELPERS
# =============================================================================

def _strip_quotes(text: str) -> str:
    """Strip surrounding quotation marks from text if present."""
    if not text:
        return text
    # Strip leading/trailing whitespace first
    text = text.strip()
    # Check for matching quotes and strip them
    if len(text) >= 2:
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
    return text


def _strip_player_name_prefix(text: str, player_name: str) -> str:
    """Strip player name prefix from text if present (e.g., 'Frank: message')."""
    if not text or not player_name:
        return text
    text = text.strip()
    prefix = f"{player_name}:"
    if text.startswith(prefix):
        text = text[len(prefix):].strip()
    return text


def _should_write_night_scratchpad(player):
    """Determine if player should write scratchpad at night start."""
    if not player.alive:
        return False
    role_name = player.role.name if player.role else None
    return role_name in ["Doctor", "Sheriff", "Vigilante", "Mafia"]


def _parse_action_response(response: Dict) -> str:
    """Parse target from an action response."""
    target = None

    if "structured_output" in response:
        target = response["structured_output"].get("target")
    else:
        try:
            content = response.get("content", "")
            idx = content.find("{")
            if idx >= 0:
                parsed = json.loads(content[idx:content.rfind("}")+1])
                target = parsed.get("target")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"JSON parse failed: {e}")

    return target


def _parse_target_response(response: Dict) -> str:
    """Parse target from a target-only response."""
    target = None

    if "structured_output" in response:
        target = response["structured_output"].get("target")
        logging.info(f"Parsed target from structured_output: {repr(target)}")
    else:
        try:
            content = response.get("content", "")
            idx = content.find("{")
            if idx >= 0:
                parsed = json.loads(content[idx:content.rfind("}")+1])
                target = parsed.get("target")
                logging.info(f"Parsed target from content JSON: {repr(target)}")
            else:
                logging.warning(f"No JSON found in response content: {content[:200]}")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"JSON parse failed: {e}. Content: {response.get('content', '')[:200]}")

    return target


# =============================================================================
# NEW EXECUTOR FUNCTIONS
# =============================================================================

def _execute_mafia_discussion(
    game_state: GameState,
    mafia,
    previous_messages: list,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute a mafia member's discussion message (before voting)."""
    prompt = build_mafia_discussion_prompt(game_state, mafia, previous_messages)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(mafia.name, "pending")

    try:
        mafia.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "mafia_discussion",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            mafia.model, messages,
            temperature=0.8,
            cancel_event=cancel_event
        )
        mafia.last_llm_context["response"] = response

        content = response.get("content", "").strip()
        content = _strip_quotes(content)
        content = _strip_player_name_prefix(content, mafia.name)
        return content[:1000] if content else "No comment."
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(mafia.name, "complete")


def _execute_mafia_vote_only(
    game_state: GameState,
    mafia,
    previous_votes: list,
    llm_client: OpenRouterClient,
    cancel_event
) -> str:
    """Execute a mafia member's vote."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prev = [{"player": v["player"], "target": v.get("target")} for v in previous_votes]
    discussion_messages = game_state.phase_data.get("mafia_discussion_messages", [])

    prompt = build_mafia_vote_prompt(game_state, mafia, prev, discussion_messages)
    messages = [{"role": "user", "content": prompt}]

    mafia.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "mafia_vote",
        "phase": game_state.phase,
        "day": game_state.day_number
    }

    response = llm_client.call_model(
        mafia.model, messages,
        response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": TARGET_ONLY_SCHEMA}},
        temperature=0.7,
        cancel_event=cancel_event
    )
    mafia.last_llm_context["response"] = response

    target = _parse_target_response(response)

    # Validate target
    if target and target not in alive_names:
        target = None

    return target


def _execute_role_discussion(
    game_state: GameState,
    player,
    role_type: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute a role's discussion/thinking phase."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    prompt = build_role_discussion_prompt(game_state, player, role_type, alive_names)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": f"{role_type}_discussion",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            temperature=0.8,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        content = response.get("content", "").strip()
        content = _strip_quotes(content)
        content = _strip_player_name_prefix(content, player.name)
        return content[:1000] if content else "No comment."
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _execute_scratchpad_writing(
    game_state: GameState,
    player,
    timing: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute scratchpad writing for a single player."""
    from llm.prompts import build_scratchpad_prompt

    prompt = build_scratchpad_prompt(game_state, player, timing)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": f"scratchpad_{timing}",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            temperature=0.7,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        note = response.get("content", "").strip()
        note = _strip_quotes(note)
        note = _strip_player_name_prefix(note, player.name)

        # Store in player's scratchpad
        if note:
            player.scratchpad.append({
                "day": game_state.day_number,
                "phase": game_state.phase,
                "timing": timing,
                "note": note,
                "timestamp": datetime.now().isoformat()
            })

        return note
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _execute_role_action(
    game_state: GameState,
    player,
    role_type: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute a role's action (target only)."""
    alive_names = [p.name for p in game_state.get_alive_players()]
    discussion = game_state.phase_data.get(f"{role_type}_discussion", "")
    prompt = build_role_action_prompt(game_state, player, role_type, alive_names, discussion)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": f"{role_type}_action",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            response_format={"type": "json_schema", "json_schema": {"name": f"{role_type}_action", "schema": TARGET_ONLY_SCHEMA}},
            temperature=0.7,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        target = _parse_target_response(response)

        # Validate target
        if target and target not in alive_names:
            logging.warning(f"{role_type.capitalize()} {player.name} selected invalid target: {target}. Available: {alive_names}")
            logging.warning(f"Raw LLM response: {response}")
            target = None

        if not target:
            logging.warning(f"{role_type.capitalize()} {player.name} did not select a valid target")
            logging.warning(f"Parsed target value: {repr(target)}")
            logging.warning(f"Available targets: {alive_names}")
            logging.warning(f"Raw LLM response: {response}")

        return target
    except Exception as e:
        logging.error(f"Error executing {role_type} action for {player.name}: {e}", exc_info=True)
        return None
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _execute_sheriff_post_investigation(
    game_state: GameState,
    sheriff,
    target: str,
    result: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute sheriff's reaction after seeing investigation result."""
    prompt = build_sheriff_post_investigation_prompt(game_state, sheriff, target, result)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(sheriff.name, "pending")

    try:
        sheriff.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "sheriff_post_investigation",
            "phase": game_state.phase,
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            sheriff.model, messages,
            temperature=0.8,
            cancel_event=cancel_event
        )
        sheriff.last_llm_context["response"] = response

        content = response.get("content", "").strip()
        # Strip surrounding quotes if present
        content = _strip_quotes(content)
        content = _strip_player_name_prefix(content, sheriff.name)
        return content[:800] if content else None
    except Exception as e:
        log_exception(e, "Sheriff post-investigation failed", player_name=sheriff.name)
        return None
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(sheriff.name, "complete")


def _execute_postgame_discussion(
    game_state: GameState,
    player,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_player_status = None
) -> str:
    """Execute a player's postgame discussion message."""
    prompt = build_postgame_discussion_prompt(game_state, player)
    messages = [{"role": "user", "content": prompt}]

    # Emit pending status
    if emit_player_status:
        emit_player_status(player.name, "pending")

    try:
        player.last_llm_context = {
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "action_type": "postgame_discussion",
            "phase": "postgame",
            "day": game_state.day_number
        }

        response = llm_client.call_model(
            player.model, messages,
            temperature=0.8,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        content = response.get("content", "").strip()
        content = _strip_quotes(content)
        content = _strip_player_name_prefix(content, player.name)
        return content[:500] if content else None
    except Exception as e:
        log_exception(e, "Postgame discussion failed", player_name=player.name)
        return None
    finally:
        # Emit complete status
        if emit_player_status:
            emit_player_status(player.name, "complete")


def _execute_mvp_vote(
    game_state: GameState,
    player,
    llm_client: OpenRouterClient,
    cancel_event
) -> Dict:
    """Execute a player's MVP vote."""
    prompt = build_mvp_vote_prompt(game_state, player)
    messages = [{"role": "user", "content": prompt}]

    player.last_llm_context = {
        "messages": messages,
        "timestamp": datetime.now().isoformat(),
        "action_type": "mvp_vote",
        "phase": "postgame",
        "day": game_state.day_number
    }

    try:
        response = llm_client.call_model(
            player.model, messages,
            response_format={"type": "json_schema", "json_schema": {"name": "mvp_vote", "schema": MVP_VOTE_SCHEMA}},
            temperature=0.7,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        target = None
        reason = ""

        if "structured_output" in response:
            target = response["structured_output"].get("target")
            reason = response["structured_output"].get("reason", "")
        else:
            try:
                content = response.get("content", "")
                idx = content.find("{")
                if idx >= 0:
                    parsed = json.loads(content[idx:content.rfind("}")+1])
                    target = parsed.get("target")
                    reason = parsed.get("reason", "")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logging.error(f"JSON parse failed for {player.name}: {e}")

        # Validate: can't vote for self
        if target == player.name:
            target = None

        # Validate: must be a valid player
        all_names = [p.name for p in game_state.players]
        if target and target not in all_names:
            target = None

        if not target:
            # Pick random other player
            others = [p.name for p in game_state.players if p.name != player.name]
            target = random.choice(others) if others else None
            reason = reason or "Good game."

        return {"player": player.name, "target": target, "reason": reason}

    except Exception as e:
        log_exception(e, "MVP vote failed, using random fallback", player_name=player.name)
        others = [p.name for p in game_state.players if p.name != player.name]
        target = random.choice(others) if others else None
        return {"player": player.name, "target": target, "reason": "Good game."}


def _resolve_mvp_voting(game_state: GameState):
    """Tally MVP votes and announce winner."""
    votes = game_state.phase_data.get("mvp_votes", [])
    vote_counts = {}

    for v in votes:
        target = v.get("target")
        if target:
            vote_counts[target] = vote_counts.get(target, 0) + 1

    if vote_counts:
        max_votes = max(vote_counts.values())
        winners = [name for name, count in vote_counts.items() if count == max_votes]

        if len(winners) == 1:
            game_state.add_event("system", f"MVP: {winners[0]} with {max_votes} votes!", "all")
        else:
            game_state.add_event("system", f"MVP tie: {', '.join(winners)} with {max_votes} votes each!", "all")
