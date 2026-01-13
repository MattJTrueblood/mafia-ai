"""
Day phase step handlers.

All handlers for day-time actions: discussion, polling, voting.
"""

import json
import logging
import random
import gevent
from datetime import datetime
from typing import Dict, List, Optional

from . import register_handler, STEP_HANDLERS
from ..runner import StepResult, StepContext
from ..game_state import GameState
from ..rules import is_round_robin_day, is_no_lynch_day, get_majority_threshold, DEFAULT_RULES
from ..llm_caller import (
    call_llm, parse_text, parse_vote, parse_turn_poll,
    VOTE_SCHEMA, TURN_POLL_SCHEMA
)
from llm.prompts import (
    build_day_discussion_prompt,
    build_turn_poll_prompt,
    build_day_voting_prompt,
    build_introduction_prompt,
    build_scratchpad_prompt,
)
from llm.openrouter_client import LLMCancelledException


# =============================================================================
# EXECUTOR HELPERS
# =============================================================================

def execute_scratchpad_writing(ctx: StepContext, player, timing: str) -> str:
    """Execute scratchpad writing for a single player."""
    prompt = build_scratchpad_prompt(ctx.game_state, player, timing)
    messages = [{"role": "user", "content": prompt}]

    response = call_llm(
        player, ctx.llm_client, messages, f"scratchpad_{timing}", ctx.game_state,
        temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
    )

    note = parse_text(response, player.name)

    if note:
        player.scratchpad.append({
            "day": ctx.day_number,
            "phase": ctx.phase,
            "timing": timing,
            "note": note,
            "timestamp": datetime.now().isoformat()
        })

    return note


def get_introduction_message(ctx: StepContext, player) -> Optional[str]:
    """Get an introduction message from a player on Day 1."""
    prompt = build_introduction_prompt(ctx.game_state, player)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = call_llm(
            player, ctx.llm_client, messages, "introduction_message", ctx.game_state,
            temperature=0.9, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        content = parse_text(response, player.name)
        return content if content else None

    except LLMCancelledException:
        raise
    except Exception as e:
        logging.error(f"Introduction message failed for {player.name}: {e}", exc_info=True)
        return None


def get_discussion_message(ctx: StepContext, player, is_interrupt: bool, is_respond: bool) -> Optional[str]:
    """Get a discussion message from a player."""
    prompt = build_day_discussion_prompt(ctx.game_state, player, is_interrupt=is_interrupt, is_respond=is_respond)
    messages = [{"role": "user", "content": prompt}]

    if is_interrupt:
        action_type = "discussion_message_interrupt"
    elif is_respond:
        action_type = "discussion_message_respond"
    else:
        action_type = "discussion_message"

    try:
        response = call_llm(
            player, ctx.llm_client, messages, action_type, ctx.game_state,
            temperature=0.8, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        content = parse_text(response, player.name, max_length=500)

        # Clean up JSON wrapper if LLM returned structured format
        if content and content.startswith("{") and "message" in content:
            try:
                parsed = json.loads(content)
                content = parsed.get("message", content)
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        return content if content else None

    except LLMCancelledException:
        raise
    except Exception:
        return None


def poll_for_turn_actions(ctx: StepContext, exclude_player: str) -> tuple:
    """Poll all AI players to see who wants to interrupt, respond, or pass.

    Human players are excluded from polling - they opt-in via interrupt button.
    """
    alive = ctx.get_alive_players()
    # Exclude both the specified player AND any human players (they opt-in via interrupt)
    players_to_poll = [p for p in alive if p.name != exclude_player and not p.is_human]

    if not players_to_poll:
        return [], [], []

    results = [None] * len(players_to_poll)

    def check_single_player(idx: int, player):
        try:
            if ctx.is_cancelled():
                raise LLMCancelledException("Turn poll cancelled")

            prompt = build_turn_poll_prompt(ctx.game_state, player)
            messages = [{"role": "user", "content": prompt}]

            response = call_llm(
                player, ctx.llm_client, messages, "turn_poll", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "turn_poll", "schema": TURN_POLL_SCHEMA}},
                temperature=0.3, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

            content = response.get("content", "")
            if not content and "structured_output" not in response:
                logging.warning(f"Empty result from turn_poll for {player.name}")

            wants_interrupt, wants_respond, wants_pass = parse_turn_poll(response)

            results[idx] = {
                "player": player.name,
                "wants_to_interrupt": wants_interrupt,
                "wants_to_respond": wants_respond,
                "wants_to_pass": wants_pass
            }

        except LLMCancelledException:
            raise
        except Exception as e:
            logging.error(f"Turn poll failed for {player.name}: {e}", exc_info=True)
            results[idx] = {
                "player": player.name,
                "wants_to_interrupt": False,
                "wants_to_respond": False,
                "wants_to_pass": False,
                "error": True
            }

    greenlets = []
    for idx, player in enumerate(players_to_poll):
        g = gevent.spawn(check_single_player, idx, player)
        greenlets.append(g)

    gevent.joinall(greenlets, raise_error=True)

    interrupting = []
    responding = []
    passing = []
    for result in results:
        if result:
            if result.get("wants_to_interrupt"):
                interrupting.append(result["player"])
            elif result.get("wants_to_respond"):
                responding.append(result["player"])
            if result.get("wants_to_pass"):
                passing.append(result["player"])

    return interrupting, responding, passing


def select_speaker_by_recency(candidates: List[str], game_state: GameState) -> str:
    """Select candidate whose last message was least recent."""
    if len(candidates) == 1:
        return candidates[0]

    last_indices = game_state.phase_data.get("player_last_message_index", {})

    def recency_key(name):
        return last_indices.get(name, -1)

    min_index = min(recency_key(c) for c in candidates)
    tied = [c for c in candidates if recency_key(c) == min_index]
    return random.choice(tied)


def resolve_voting(game_state: GameState):
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
    majority_threshold = get_majority_threshold(alive_count)

    lynch_target = None
    lynch_votes = 0
    for name, count in vote_counts.items():
        if name != "abstain" and count >= majority_threshold:
            lynch_target = name
            lynch_votes = count
            break

    if lynch_target:
        target_player = game_state.get_player_by_name(lynch_target)
        game_state.kill_player(lynch_target, f"Lynched by vote ({lynch_votes} votes).")
        if target_player:
            # Check for Jester win - sets winner, postgame handles the rest
            if (target_player.role.name == "Jester"):
                game_state.winner = "jester"
                game_state.winning_jester = lynch_target  # Track which Jester won
                game_state.add_event("system", f"{lynch_target} was the JESTER! {lynch_target} wins!", "all")
            else:
                role_flip = "MAFIA" if target_player.team == "mafia" else "TOWN"
                game_state.add_event("system", f"{lynch_target} was {role_flip}.", "all")
    else:
        game_state.add_event("vote_result",
            "Nobody died, as no player received a majority of votes.", "all")


def execute_parallel(players, func, ctx: StepContext):
    """Execute a function for multiple players in parallel."""
    from gevent import Greenlet

    results = []
    greenlets = []

    for player in players:
        def worker(p=player):
            if ctx.is_cancelled():
                return None
            result = func(p)
            return result

        g = Greenlet(worker)
        greenlets.append(g)

    for g in greenlets:
        g.start()

    gevent.joinall(greenlets, raise_error=True)

    for g in greenlets:
        if g.value is not None:
            results.append(g.value)

    return results


# =============================================================================
# DAY START HANDLERS
# =============================================================================

@register_handler("day_start")
def handle_day_start(ctx: StepContext) -> StepResult:
    """Initialize day phase."""
    ctx.add_event("phase_change", f"Day {ctx.day_number} begins.")

    alive = ctx.get_alive_players()
    names = ", ".join([p.name for p in alive])
    ctx.add_event("system", f"Remaining players ({len(alive)}): {names}")

    if is_round_robin_day(ctx.rules, ctx.day_number):
        ctx.add_event("system", "Introduction phase begins. Each player will introduce themselves.")
        if ctx.emit_status:
            ctx.emit_status("introduction_start", message_count=0, max_messages=len(alive))
        return StepResult(next_step="introduction_message", next_index=0)
    else:
        ctx.add_event("system", f"Day {ctx.day_number} discussion phase begins.")
        if ctx.emit_status:
            ctx.emit_status("discussion_start", message_count=0, max_messages=DEFAULT_RULES.max_discussion_messages)
        return StepResult(next_step="scratchpad_day_start", next_index=0)


@register_handler("scratchpad_day_start")
def handle_scratchpad_day_start(ctx: StepContext) -> StepResult:
    """All AI players write private strategic notes at day start."""
    if is_round_robin_day(ctx.rules, ctx.day_number):
        # Safety guard - shouldn't reach here on round-robin days
        return StepResult(next_step="discussion_poll", next_index=0)

    # Only AI players write scratchpad notes
    ai_players = [p for p in ctx.get_alive_players() if not p.is_human]

    def scratchpad_func(player):
        return execute_scratchpad_writing(ctx, player, "day_start")

    if ai_players:
        execute_parallel(ai_players, scratchpad_func, ctx)
        ctx.add_event("system", "Players wrote strategic notes.")

    return StepResult(next_step="discussion_poll", next_index=0)


@register_handler("introduction_message")
def handle_introduction_message(ctx: StepContext) -> StepResult:
    """Simple round-robin introductions for Day 1."""
    speaker_order = ctx.phase_data.get("speaker_order", [])
    current_idx = ctx.step_index

    if current_idx >= len(speaker_order):
        ctx.add_event("system", "Introduction phase complete.")
        if ctx.emit_status:
            ctx.emit_status("turn_polling", waiting_player=None)
        # Check if this day has voting
        if is_no_lynch_day(ctx.rules, ctx.day_number):
            return StepResult(next_step="night_start", next_index=0)
        return StepResult(next_step="scratchpad_pre_vote", next_index=0)

    speaker_name = speaker_order[current_idx]
    speaker = ctx.get_player_by_name(speaker_name)

    if not speaker or not speaker.alive:
        return StepResult(next_step="introduction_message", next_index=current_idx + 1)

    logging.info(f"Introduction turn for {speaker_name}, is_human={speaker.is_human}")

    if ctx.emit_status:
        ctx.emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=False, is_respond=False)

    introduction = None

    # Check if this is a human player
    if speaker.is_human:
        logging.info(f"Human player {speaker_name} - waiting for input")

        ctx.game_state.set_waiting_for_human("discussion", {"label": "Introduce yourself"})
        if ctx.emit_game_state:
            ctx.emit_game_state()
        gevent.sleep(0.05)  # Yield to allow socket to send before blocking

        human_input = ctx.wait_for_human() if ctx.wait_for_human else None
        logging.info(f"Human input received for {speaker_name}: {human_input}")
        ctx.game_state.clear_waiting_for_human()

        if human_input and human_input.get("type") == "discussion":
            introduction = human_input.get("message", "").strip()[:500]
        else:
            logging.warning(f"No valid human input for {speaker_name}, human_input={human_input}")
    else:
        introduction = get_introduction_message(ctx, speaker)

    if introduction:
        ctx.add_event("discussion", introduction, "public", player=speaker_name,
                     metadata={"turn_type": "introduction"})

        if "discussion_messages" not in ctx.phase_data:
            ctx.phase_data["discussion_messages"] = []
        ctx.phase_data["discussion_messages"].append({
            "player": speaker_name,
            "message": introduction,
            "is_interrupt": False,
            "is_respond": False
        })

    return StepResult(next_step="introduction_message", next_index=current_idx + 1)


# =============================================================================
# DISCUSSION HANDLERS
# =============================================================================

@register_handler("discussion_poll")
def handle_discussion_poll(ctx: StepContext) -> StepResult:
    """Poll players to see who wants to speak."""
    messages = ctx.phase_data.get("discussion_messages", [])
    max_messages = DEFAULT_RULES.max_discussion_messages

    logging.info(f"[POLL] Messages so far: {len(messages)}/{max_messages}")

    if len(messages) >= max_messages:
        logging.info(f"[POLL] Max messages reached")
        if ctx.emit_status:
            ctx.emit_status("discussion_end")
        ctx.add_event("system", f"Day {ctx.day_number} discussion phase ends.")
        return StepResult(next_step="scratchpad_pre_vote", next_index=0)

    # Check if human has requested interrupt - they get priority
    if ctx.game_state.human_interrupt_requested and ctx.game_state.is_human_alive():
        ctx.game_state.human_interrupt_requested = False  # Clear the request
        human_name = ctx.game_state.human_player_name
        ctx.phase_data["next_speaker"] = human_name
        ctx.phase_data["is_interrupt"] = True
        ctx.phase_data["is_respond"] = False
        logging.info(f"[POLL] Human interrupt priority: {human_name}")
        return StepResult(next_step="discussion_message", next_index=len(messages))

    speaker_order = ctx.phase_data.get("speaker_order", [])
    speaker_idx = ctx.phase_data.get("current_speaker_index", 0)

    if not speaker_order:
        if ctx.emit_status:
            ctx.emit_status("turn_polling", waiting_player=None)
        return StepResult(next_step="voting", next_index=0)

    last_speaker = ctx.phase_data.get("last_speaker", None)

    # Find next valid speaker (excluding human - they opt-in via interrupt)
    current_speaker_name = None
    current_speaker = None
    attempts = 0
    while attempts < len(speaker_order):
        candidate_name = speaker_order[speaker_idx % len(speaker_order)]
        candidate = ctx.get_player_by_name(candidate_name)

        # Skip human players - they opt-in via interrupt button, not rotation
        if candidate and candidate.alive and candidate_name != last_speaker and not candidate.is_human:
            current_speaker_name = candidate_name
            current_speaker = candidate
            break

        speaker_idx += 1
        ctx.phase_data["current_speaker_index"] = speaker_idx
        attempts += 1

    if not current_speaker:
        logging.info(f"[POLL] No valid speaker found")
        if ctx.emit_status:
            ctx.emit_status("turn_polling", waiting_player=None)
        return StepResult(next_step="voting", next_index=0)

    last_was_respond = ctx.phase_data.get("last_was_respond", False)

    if ctx.emit_status:
        ctx.emit_status("turn_polling", waiting_player=None)

    # Poll AI players only (exclude human from polling)
    interrupting, responding, passing = poll_for_turn_actions(ctx, last_speaker)

    # Filter out human player from poll results (they shouldn't be there anyway, but safety check)
    human_name = ctx.game_state.human_player_name
    if human_name:
        interrupting = [p for p in interrupting if p != human_name]
        responding = [p for p in responding if p != human_name]
        passing = [p for p in passing if p != human_name]

    logging.info(f"[POLL] Results: interrupting={interrupting}, responding={responding}, passing={passing}")

    if last_was_respond:
        responding = []

    round_passes = ctx.phase_data.get("round_passes", [])
    for passer in passing:
        if passer not in round_passes:
            round_passes.append(passer)
    ctx.phase_data["round_passes"] = round_passes

    if ctx.emit_status:
        ctx.emit_status("turn_poll_result",
            interrupting_players=interrupting,
            responding_players=responding,
            passing_players=round_passes)

    if interrupting:
        interrupter_name = select_speaker_by_recency(interrupting, ctx.game_state)
        ctx.phase_data["next_speaker"] = interrupter_name
        ctx.phase_data["is_interrupt"] = True
        ctx.phase_data["is_respond"] = False
    elif responding:
        responder_name = select_speaker_by_recency(responding, ctx.game_state)
        ctx.phase_data["next_speaker"] = responder_name
        ctx.phase_data["is_interrupt"] = False
        ctx.phase_data["is_respond"] = True
    else:
        chosen_speaker = None
        search_attempts = 0
        search_idx = speaker_idx

        while search_attempts < len(speaker_order):
            candidate_name = speaker_order[search_idx % len(speaker_order)]
            candidate = ctx.get_player_by_name(candidate_name)

            # Skip human players in rotation
            if candidate and candidate.alive and candidate_name != last_speaker and not candidate.is_human:
                if candidate_name not in round_passes:
                    chosen_speaker = candidate_name
                    ctx.phase_data["current_speaker_index"] = search_idx
                    break

            search_idx += 1
            search_attempts += 1

        if not chosen_speaker:
            chosen_speaker = current_speaker_name

        ctx.phase_data["next_speaker"] = chosen_speaker
        ctx.phase_data["is_interrupt"] = False
        ctx.phase_data["is_respond"] = False

    return StepResult(next_step="discussion_message", next_index=len(messages))


@register_handler("discussion_message")
def handle_discussion_message(ctx: StepContext) -> StepResult:
    """Get a discussion message from the selected speaker."""
    speaker_name = ctx.phase_data.get("next_speaker")
    is_interrupt = ctx.phase_data.get("is_interrupt", False)
    is_respond = ctx.phase_data.get("is_respond", False)

    logging.info(f"[MESSAGE] Getting message from {speaker_name}")

    if not speaker_name:
        return StepResult(next_step="discussion_poll", next_index=0)

    speaker = ctx.get_player_by_name(speaker_name)
    if not speaker or not speaker.alive:
        return StepResult(next_step="discussion_poll", next_index=0)

    if ctx.emit_status:
        ctx.emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=is_interrupt, is_respond=is_respond)

    message = None

    # Check if this is a human player
    if speaker.is_human:
        # Wait for human input
        ctx.game_state.set_waiting_for_human("discussion", {"is_interrupt": is_interrupt})
        if ctx.emit_game_state:
            ctx.emit_game_state()
        gevent.sleep(0.05)  # Yield to allow socket to send before blocking

        human_input = ctx.wait_for_human() if ctx.wait_for_human else None
        ctx.game_state.clear_waiting_for_human()

        if human_input and human_input.get("type") == "discussion":
            message = human_input.get("message", "").strip()[:500]
    else:
        message = get_discussion_message(ctx, speaker, is_interrupt, is_respond)

    if message:
        msg_index = len(ctx.phase_data["discussion_messages"])
        ctx.phase_data["player_last_message_index"][speaker_name] = msg_index

        if is_interrupt:
            turn_type = "interrupt"
        elif is_respond:
            turn_type = "respond"
        else:
            turn_type = "regular"

        ctx.add_event("discussion", message, "public", player=speaker_name,
                     metadata={"turn_type": turn_type})
        ctx.phase_data["discussion_messages"].append({
            "player": speaker_name,
            "message": message,
            "is_interrupt": is_interrupt,
            "is_respond": is_respond
        })

        ctx.phase_data["last_speaker"] = speaker_name
        ctx.phase_data["last_was_respond"] = is_respond
        ctx.phase_data["round_passes"] = []

        speaker_order = ctx.phase_data.get("speaker_order", [])
        if speaker_name in speaker_order:
            speaker_order.remove(speaker_name)
            speaker_order.append(speaker_name)
            ctx.phase_data["speaker_order"] = speaker_order
            ctx.phase_data["current_speaker_index"] = 0

    return StepResult(next_step="discussion_poll", next_index=0)


@register_handler("scratchpad_pre_vote")
def handle_scratchpad_pre_vote(ctx: StepContext) -> StepResult:
    """AI players write strategic notes before voting."""
    # Only AI players write scratchpad notes
    ai_players = [p for p in ctx.get_alive_players() if not p.is_human]

    def scratchpad_func(player):
        return execute_scratchpad_writing(ctx, player, "pre_vote")

    if ai_players:
        execute_parallel(ai_players, scratchpad_func, ctx)
        ctx.add_event("system", "Players wrote strategic notes.")

    if ctx.emit_status:
        ctx.emit_status("turn_polling", waiting_player=None)

    return StepResult(next_step="voting", next_index=0)


# =============================================================================
# VOTING HANDLERS
# =============================================================================

@register_handler("voting")
def handle_voting(ctx: StepContext) -> StepResult:
    """All players vote on who to lynch. Human votes first, then AI in parallel."""
    alive_players = ctx.get_alive_players()
    alive_names = [p.name for p in alive_players]

    ctx.add_event("system", f"Day {ctx.day_number} voting phase begins.")

    results = []

    # Check if there's a human player who needs to vote
    human_player = ctx.game_state.get_human_player()
    if human_player and human_player.alive:
        # Wait for human vote first
        ctx.game_state.set_waiting_for_human("vote", {"options": alive_names})
        if ctx.emit_game_state:
            ctx.emit_game_state()
        gevent.sleep(0.05)  # Yield to allow socket to send before blocking

        human_input = ctx.wait_for_human() if ctx.wait_for_human else None
        ctx.game_state.clear_waiting_for_human()

        if human_input and human_input.get("type") == "vote":
            vote_target = human_input.get("target", "abstain")
            explanation = human_input.get("explanation", "")

            if vote_target != "abstain" and vote_target not in alive_names:
                vote_target = "abstain"

            if vote_target != "abstain":
                msg = f"I vote to lynch {vote_target}."
            else:
                msg = "I abstain from voting."
            if explanation:
                msg += f" {explanation}"

            ctx.add_event("vote", msg, "all", player=human_player.name, priority=8,
                         metadata={"target": vote_target})

            results.append({"player": human_player.name, "vote": vote_target, "explanation": explanation})

    # Now get AI player votes in parallel
    ai_players = [p for p in alive_players if not p.is_human]

    def vote_func(player):
        prompt = build_day_voting_prompt(ctx.game_state, player)
        messages = [{"role": "user", "content": prompt}]

        response = call_llm(
            player, ctx.llm_client, messages, "day_vote", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "vote", "schema": VOTE_SCHEMA}},
            temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        vote_target, explanation = parse_vote(response)

        if vote_target != "abstain" and vote_target not in alive_names:
            vote_target = "abstain"

        if vote_target != "abstain":
            msg = f"I vote to lynch {vote_target}."
        else:
            msg = "I abstain from voting."
        if explanation:
            msg += f" {explanation}"

        ctx.add_event("vote", msg, "all", player=player.name, priority=8,
                     metadata={"target": vote_target})

        return {"player": player.name, "vote": vote_target, "explanation": explanation}

    ai_results = execute_parallel(ai_players, vote_func, ctx)
    results.extend(ai_results)
    ctx.phase_data["votes"] = results

    return StepResult(next_step="voting_resolve", next_index=0)


@register_handler("voting_resolve")
def handle_voting_resolve(ctx: StepContext) -> StepResult:
    """Tally votes and apply lynch if majority."""
    from ..win_conditions import check_win_conditions

    resolve_voting(ctx.game_state)
    ctx.add_event("system", f"Day {ctx.day_number} voting phase ends.")
    ctx.add_event("system", f"Day {ctx.day_number} ends.")

    # Check for Jester win (set during resolve_voting) or normal win
    if ctx.game_state.winner:
        return StepResult(next_step="postgame_reveal", next_index=0)

    winner = check_win_conditions(ctx.game_state)
    if winner:
        ctx.game_state.winner = winner
        return StepResult(next_step="postgame_reveal", next_index=0)

    ctx.game_state.start_night_phase()
    return StepResult(next_step="night_start", next_index=0)
