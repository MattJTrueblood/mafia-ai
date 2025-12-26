"""
Step-based game processor.

This module provides a unified step processor that executes the game
one step at a time. Each step is a single atomic action (typically one LLM call).
The game can be paused between any two steps.
"""

import json
import random
from datetime import datetime
from typing import Dict, Any, Optional, Callable

from .game_state import GameState
from .win_conditions import check_win_conditions
from llm.openrouter_client import OpenRouterClient, LLMCancelledException
from llm.prompts import (
    build_night_prompt,
    build_day_discussion_prompt,
    build_interrupt_check_prompt,
    build_day_voting_prompt,
    build_mafia_vote_prompt,
)


# Structured output schemas
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": ["string", "null"]},
        "reasoning": {"type": "string"}
    },
    "required": ["target", "reasoning"]
}

VOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "vote": {"type": "string"},
        "explanation": {"type": "string"}
    },
    "required": ["vote", "explanation"]
}

INTERRUPT_SCHEMA = {
    "type": "object",
    "properties": {
        "wants_to_interrupt": {"type": "boolean"},
        "wants_to_pass": {"type": "boolean"}
    },
    "required": ["wants_to_interrupt", "wants_to_pass"]
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

    step = game_state.current_step
    index = game_state.step_index

    # =========================================================================
    # NIGHT PHASE STEPS
    # =========================================================================

    if step == GameState.STEP_NIGHT_START:
        # Initialize night phase
        game_state.phase_data = {
            "mafia_votes": [],
            "doctor_protection": None,
            "sheriff_investigation": None,
            "vigilante_kill": None,
            "protected_player": None,
        }
        game_state.add_event("phase_change", f"Night {game_state.day_number + 1} begins.", "all")
        emit_update()

        # Advance to mafia voting
        game_state.current_step = GameState.STEP_MAFIA_VOTE
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_MAFIA_VOTE:
        mafia_players = game_state.get_players_by_role("Mafia")

        if index >= len(mafia_players):
            # All mafia have voted, tally votes and move on
            _tally_mafia_votes(game_state)
            game_state.current_step = GameState.STEP_DOCTOR
            game_state.step_index = 0
            return StepResult()

        # Get this mafia member's vote
        mafia = mafia_players[index]
        previous_votes = game_state.phase_data.get("mafia_votes", [])

        result = _execute_mafia_vote(game_state, mafia, previous_votes, llm_client, cancel_event)
        game_state.phase_data["mafia_votes"].append(result)
        emit_update()

        # Advance to next mafia member
        game_state.step_index = index + 1
        return StepResult()

    elif step == GameState.STEP_DOCTOR:
        doctor_players = game_state.get_players_by_role("Doctor")

        if doctor_players:
            doctor = doctor_players[0]
            result = _execute_doctor_protect(game_state, doctor, llm_client, cancel_event)
            game_state.phase_data["doctor_protection"] = result
            if result.get("target"):
                game_state.phase_data["protected_player"] = result["target"]
            emit_update()

        # Move to sheriff
        game_state.current_step = GameState.STEP_SHERIFF
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_SHERIFF:
        sheriff_players = game_state.get_players_by_role("Sheriff")

        if sheriff_players:
            sheriff = sheriff_players[0]
            result = _execute_sheriff_investigate(game_state, sheriff, llm_client, cancel_event)
            game_state.phase_data["sheriff_investigation"] = result
            emit_update()

        # Move to vigilante
        game_state.current_step = GameState.STEP_VIGILANTE
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VIGILANTE:
        vigilante_players = game_state.get_players_by_role("Vigilante")

        if vigilante_players:
            vigilante = vigilante_players[0]
            if not vigilante.role.bullet_used:
                result = _execute_vigilante_kill(game_state, vigilante, llm_client, cancel_event)
                game_state.phase_data["vigilante_kill"] = result
                emit_update()

        # Move to resolution
        game_state.current_step = GameState.STEP_NIGHT_RESOLVE
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_NIGHT_RESOLVE:
        _resolve_night_actions(game_state)
        emit_update()

        # Check win conditions
        winner = check_win_conditions(game_state)
        if winner:
            game_state.winner = winner
            game_state.game_over = True
            game_state.add_event("system", f"Game over! {'Mafia' if winner == 'mafia' else 'Town'} wins!", "all")
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
        emit_update()
        emit_status("discussion_start", message_count=0, max_messages=10)

        # Move to discussion polling
        game_state.current_step = GameState.STEP_DISCUSSION_POLL
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_DISCUSSION_POLL:
        # Check if discussion should end
        messages = game_state.phase_data.get("discussion_messages", [])
        max_messages = 10
        consecutive_no_interrupt = game_state.phase_data.get("consecutive_no_interrupt_rounds", 0)
        alive_count = len(game_state.get_alive_players())

        if len(messages) >= max_messages:
            # Max messages reached
            emit_status("discussion_end")
            game_state.add_event("system", "Discussion phase ends.", "all")
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            emit_update()
            return StepResult()

        if consecutive_no_interrupt >= 2 and len(messages) >= alive_count:
            # No one has urgent info
            emit_status("discussion_end")
            game_state.add_event("system", "Discussion phase ends.", "all")
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            emit_update()
            return StepResult()

        # Get current speaker from round-robin
        speaker_order = game_state.phase_data.get("speaker_order", [])
        speaker_idx = game_state.phase_data.get("current_speaker_index", 0)

        if not speaker_order:
            # No speakers, move to voting
            game_state.current_step = GameState.STEP_VOTING
            game_state.step_index = 0
            return StepResult()

        current_speaker_name = speaker_order[speaker_idx % len(speaker_order)]
        current_speaker = game_state.get_player_by_name(current_speaker_name)

        # Skip dead speakers
        if not current_speaker or not current_speaker.alive:
            game_state.phase_data["current_speaker_index"] = speaker_idx + 1
            return StepResult()

        # Poll for interrupts and passes
        emit_status("interrupt_polling", waiting_player=None)
        interrupting, passing = _poll_for_interrupts(
            game_state, current_speaker_name, llm_client, cancel_event, emit_status
        )

        # Track players who passed this round
        round_passes = game_state.phase_data.get("round_passes", [])
        for passer in passing:
            if passer not in round_passes:
                round_passes.append(passer)
        game_state.phase_data["round_passes"] = round_passes

        # Emit status with passing players info
        emit_status("interrupt_result", interrupting_players=interrupting, passing_players=round_passes)

        if interrupting:
            # Someone wants to interrupt - pick randomly
            interrupter_name = random.choice(interrupting)
            game_state.phase_data["next_speaker"] = interrupter_name
            game_state.phase_data["is_interrupt"] = True
            game_state.phase_data["consecutive_no_interrupt_rounds"] = 0
        else:
            # Check if current speaker passed
            if current_speaker_name in round_passes:
                # Current speaker passed - advance to next speaker
                game_state.phase_data["current_speaker_index"] = speaker_idx + 1
                game_state.phase_data["consecutive_no_interrupt_rounds"] = consecutive_no_interrupt + 1
                # Don't set next_speaker, just loop back to poll
                return StepResult()

            # No interrupts and speaker didn't pass - current speaker goes
            game_state.phase_data["next_speaker"] = current_speaker_name
            game_state.phase_data["is_interrupt"] = False
            game_state.phase_data["consecutive_no_interrupt_rounds"] = consecutive_no_interrupt + 1

        # Move to getting the message
        game_state.current_step = GameState.STEP_DISCUSSION_MESSAGE
        game_state.step_index = len(messages)
        return StepResult()

    elif step == GameState.STEP_DISCUSSION_MESSAGE:
        speaker_name = game_state.phase_data.get("next_speaker")
        is_interrupt = game_state.phase_data.get("is_interrupt", False)

        if not speaker_name:
            # No speaker, go back to polling
            game_state.current_step = GameState.STEP_DISCUSSION_POLL
            return StepResult()

        speaker = game_state.get_player_by_name(speaker_name)
        if not speaker or not speaker.alive:
            game_state.current_step = GameState.STEP_DISCUSSION_POLL
            return StepResult()

        emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=is_interrupt)

        message = _get_discussion_message(game_state, speaker, is_interrupt, llm_client, cancel_event)

        if message:
            game_state.add_event("discussion", message, "public", player=speaker_name)
            game_state.phase_data["discussion_messages"].append({
                "player": speaker_name,
                "message": message,
                "is_interrupt": is_interrupt
            })

        # Advance speaker if this wasn't an interrupt
        if not is_interrupt:
            game_state.phase_data["current_speaker_index"] = (
                game_state.phase_data.get("current_speaker_index", 0) + 1
            )

        emit_update()

        # Go back to polling
        game_state.current_step = GameState.STEP_DISCUSSION_POLL
        game_state.step_index = 0
        return StepResult()

    elif step == GameState.STEP_VOTING:
        alive_players = game_state.get_alive_players()

        if index == 0:
            game_state.add_event("system", "Voting phase begins.", "all")
            emit_update()

        if index >= len(alive_players):
            # All votes cast, resolve
            game_state.current_step = GameState.STEP_VOTING_RESOLVE
            game_state.step_index = 0
            return StepResult()

        player = alive_players[index]
        vote_result = _execute_day_vote(game_state, player, llm_client, cancel_event)
        game_state.phase_data["votes"].append(vote_result)
        emit_update()

        game_state.step_index = index + 1
        return StepResult()

    elif step == GameState.STEP_VOTING_RESOLVE:
        _resolve_voting(game_state)
        emit_update()

        # Check win conditions
        winner = check_win_conditions(game_state)
        if winner:
            game_state.winner = winner
            game_state.game_over = True
            game_state.add_event("system", f"Game over! {'Mafia' if winner == 'mafia' else 'Town'} wins!", "all")
            emit_update()
            return StepResult()

        # Transition to night
        game_state.start_night_phase()
        emit_update()
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
    prev = [{"player": v["player"], "target": v.get("target"), "reasoning": v.get("reasoning", "")}
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

    target, reasoning = _parse_action_response(response)

    # Log mafia discussion
    if reasoning:
        if target:
            msg = f"[Mafia Discussion] I think we should target {target}. {reasoning}"
        else:
            msg = f"[Mafia Discussion] I'm not sure who to target. {reasoning}"
        game_state.add_event("mafia_chat", msg, "mafia", player=mafia.name, priority=7)

    return {"player": mafia.name, "target": target, "reasoning": reasoning}


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

    target, reasoning = _parse_action_response(response)

    # Check if same target as last night
    if target and doctor.role.last_protected == target:
        monologue = f"[Doctor's Thoughts] I wanted to protect {target} again, but I can't protect the same person twice."
        game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)
        target = None
    else:
        if target:
            doctor.role.last_protected = target
            monologue = f"[Doctor's Thoughts] I'll protect {target} tonight. {reasoning}"
        else:
            monologue = f"[Doctor's Thoughts] I'm choosing not to protect anyone. {reasoning}"
        if reasoning:
            game_state.add_event("role_action", monologue, "doctor", player=doctor.name, priority=6)

    return {"target": target, "reasoning": reasoning}


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

    target, reasoning = _parse_action_response(response)

    result = None
    if target:
        target_player = game_state.get_player_by_name(target)
        if target_player:
            result = "mafia" if target_player.team == "mafia" else "town"
            sheriff.role.investigations.append((target, result))

            if reasoning:
                game_state.add_event("role_action",
                    f"[Sheriff's Thoughts] I'm investigating {target}. {reasoning}",
                    "sheriff", player=sheriff.name, priority=6)
            game_state.add_event("role_action",
                f"Investigation result: {target} is {result.upper()}.",
                "sheriff", player=sheriff.name, priority=8,
                metadata={"target": target, "result": result})

    return {"target": target, "result": result, "reasoning": reasoning}


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

    target, reasoning = _parse_action_response(response)

    if target:
        vigilante.role.bullet_used = True
        if reasoning:
            game_state.add_event("role_action",
                f"[Vigilante's Thoughts] I'm using my bullet on {target}. {reasoning}",
                "vigilante", player=vigilante.name, priority=6)
    elif reasoning:
        game_state.add_event("role_action",
            f"[Vigilante's Thoughts] I'm saving my bullet. {reasoning}",
            "vigilante", player=vigilante.name, priority=6)

    return {"target": target, "reasoning": reasoning}


def _resolve_night_actions(game_state: GameState):
    """Resolve night actions and apply kills."""
    protected = game_state.phase_data.get("protected_player")
    kills = []

    # Mafia kill
    mafia_target = game_state.phase_data.get("mafia_kill_target")
    if mafia_target and mafia_target != protected:
        if game_state.kill_player(mafia_target, "Killed during the night."):
            kills.append(mafia_target)

    # Vigilante kill
    vig_data = game_state.phase_data.get("vigilante_kill")
    if vig_data and vig_data.get("target"):
        vig_target = vig_data["target"]
        if vig_target != protected:
            if game_state.kill_player(vig_target, "Killed during the night."):
                kills.append(vig_target)

    if not kills:
        game_state.add_event("system", "No one died during the night.", "all")


# =============================================================================
# DAY ACTION EXECUTORS
# =============================================================================

def _poll_for_interrupts(
    game_state: GameState,
    exclude_player: str,
    llm_client: OpenRouterClient,
    cancel_event,
    emit_status
) -> tuple:
    """Poll all players to see who wants to interrupt or pass.

    Returns:
        Tuple of (interrupting_players, passing_players)
    """
    interrupting = []
    passing = []
    alive = game_state.get_alive_players()

    for player in alive:
        if player.name == exclude_player:
            continue

        emit_status("waiting_interrupt", waiting_player=player.name,
                   interrupting_players=interrupting, passing_players=passing)

        prompt = build_interrupt_check_prompt(game_state, player)
        messages = [{"role": "user", "content": prompt}]

        try:
            player.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "interrupt_check",
                "phase": game_state.phase,
                "day": game_state.day_number
            }

            response = llm_client.call_model(
                player.model, messages,
                response_format={"type": "json_schema", "json_schema": {"name": "interrupt", "schema": INTERRUPT_SCHEMA}},
                temperature=0.3,
                max_tokens=100,
                cancel_event=cancel_event
            )
            player.last_llm_context["response"] = response

            wants_interrupt = False
            wants_pass = False

            if "structured_output" in response:
                wants_interrupt = response["structured_output"].get("wants_to_interrupt", False)
                wants_pass = response["structured_output"].get("wants_to_pass", False)
            else:
                try:
                    content = response.get("content", "")
                    idx = content.find("{")
                    if idx >= 0:
                        parsed = json.loads(content[idx:content.rfind("}")+1])
                        wants_interrupt = parsed.get("wants_to_interrupt", False)
                        wants_pass = parsed.get("wants_to_pass", False)
                except:
                    pass

            if wants_interrupt:
                interrupting.append(player.name)
            if wants_pass:
                passing.append(player.name)

        except LLMCancelledException:
            raise
        except Exception:
            continue

    return interrupting, passing


def _get_discussion_message(
    game_state: GameState,
    player,
    is_interrupt: bool,
    llm_client: OpenRouterClient,
    cancel_event
) -> Optional[str]:
    """Get a discussion message from a player."""
    prompt = build_day_discussion_prompt(game_state, player, is_interrupt=is_interrupt)
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
            player.model, messages,
            temperature=0.8,
            max_tokens=300,
            cancel_event=cancel_event
        )
        player.last_llm_context["response"] = response

        content = response.get("content", "").strip()

        # Clean up JSON wrapper if present
        if content.startswith("{") and "message" in content:
            try:
                parsed = json.loads(content)
                content = parsed.get("message", content)
            except:
                pass

        return content[:500] if content else None

    except LLMCancelledException:
        raise
    except Exception:
        return None


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
            except:
                pass

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
    """Resolve voting and apply lynch."""
    votes = game_state.phase_data.get("votes", [])
    vote_counts = {}

    for v in votes:
        target = v.get("vote", "abstain")
        vote_counts[target] = vote_counts.get(target, 0) + 1

    if not vote_counts:
        game_state.add_event("vote_result", "No votes were cast.", "all")
        return

    max_votes = max(vote_counts.values())
    candidates = [name for name, count in vote_counts.items() if count == max_votes]

    if len(candidates) > 1:
        game_state.add_event("vote_result",
            f"Tie in voting between {', '.join(candidates)}. No one was lynched.", "all")
    elif candidates[0] == "abstain":
        game_state.add_event("vote_result",
            f"No one was lynched. Abstain received the most votes ({vote_counts['abstain']}).", "all")
    else:
        target = candidates[0]
        game_state.kill_player(target, f"Lynched by vote ({vote_counts[target]} votes).")


# =============================================================================
# HELPERS
# =============================================================================

def _parse_action_response(response: Dict) -> tuple:
    """Parse target and reasoning from an action response."""
    target = None
    reasoning = ""

    if "structured_output" in response:
        target = response["structured_output"].get("target")
        reasoning = response["structured_output"].get("reasoning", "")
    else:
        try:
            content = response.get("content", "")
            idx = content.find("{")
            if idx >= 0:
                parsed = json.loads(content[idx:content.rfind("}")+1])
                target = parsed.get("target")
                reasoning = parsed.get("reasoning", "")
        except:
            pass

    return target, reasoning
