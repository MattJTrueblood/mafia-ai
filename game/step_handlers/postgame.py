"""
Postgame phase step handlers.

Handlers for postgame: role reveal, discussion, MVP voting, and trashtalk.
"""

import logging
import random
import gevent

from . import register_handler, STEP_HANDLERS
from ..runner import StepResult, StepContext
from ..game_state import GameState
from ..llm_caller import call_llm, parse_text, parse_mvp_vote, parse_turn_poll, MVP_VOTE_SCHEMA, TURN_POLL_SCHEMA
from ..utils import (
    execute_parallel,
    select_speaker_by_recency,
    wait_for_human_input,
)
from llm.prompts import (
    build_postgame_discussion_prompt, build_mvp_vote_prompt,
    build_trashtalk_poll_prompt, build_trashtalk_message_prompt
)
from llm.openrouter_client import LLMCancelledException


# =============================================================================
# EXECUTOR HELPERS
# =============================================================================

def execute_postgame_discussion(ctx: StepContext, player) -> str:
    """Execute a player's postgame discussion message."""
    prompt = build_postgame_discussion_prompt(ctx.game_state, player)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = call_llm(
            player, ctx.llm_client, messages, "postgame_discussion", ctx.game_state,
            temperature=0.8, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        content = parse_text(response, player.name, max_length=500)
        return content if content else None
    except Exception as e:
        logging.error(f"Postgame discussion failed for {player.name}: {e}", exc_info=True)
        return None


def resolve_mvp_voting(game_state: GameState):
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


def poll_for_trashtalk_actions(ctx: StepContext, exclude_player: str) -> tuple:
    """Poll all AI players to see who wants to speak in trashtalk.

    Human players are excluded - they opt-in via interrupt button.
    All players (dead or alive) can participate.
    """
    all_players = ctx.game_state.players
    # Exclude the specified player AND human players
    players_to_poll = [p for p in all_players if p.name != exclude_player and not p.is_human]

    if not players_to_poll:
        return [], [], []

    results = [None] * len(players_to_poll)

    def check_single_player(idx: int, player):
        try:
            if ctx.is_cancelled():
                raise LLMCancelledException("Trashtalk poll cancelled")

            prompt = build_trashtalk_poll_prompt(ctx.game_state, player)
            messages = [{"role": "user", "content": prompt}]

            response = call_llm(
                player, ctx.llm_client, messages, "trashtalk_poll", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "turn_poll", "schema": TURN_POLL_SCHEMA}},
                temperature=0.3, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

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
            logging.error(f"Trashtalk poll failed for {player.name}: {e}", exc_info=True)
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
            elif result.get("wants_to_pass"):
                passing.append(result["player"])

    return interrupting, responding, passing


def get_trashtalk_message(ctx: StepContext, player, is_interrupt: bool, is_respond: bool) -> str:
    """Get a trashtalk message from an AI player."""
    prompt = build_trashtalk_message_prompt(ctx.game_state, player, is_interrupt, is_respond)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = call_llm(
            player, ctx.llm_client, messages, "trashtalk_message", ctx.game_state,
            temperature=0.9, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        content = parse_text(response, player.name, max_length=500)
        return content if content else None
    except Exception as e:
        logging.error(f"Trashtalk message failed for {player.name}: {e}", exc_info=True)
        return None


# =============================================================================
# POSTGAME HANDLERS
# =============================================================================

@register_handler("postgame_reveal")
def handle_postgame_reveal(ctx: StepContext) -> StepResult:
    """Reveal all player roles."""
    winner = ctx.game_state.winner
    if winner == "town":
        winner_text = "TOWN WINS!"
    elif winner == "mafia":
        winner_text = "MAFIA WINS!"
    elif winner == "jester":
        jester_name = getattr(ctx.game_state, 'winning_jester', 'JESTER')
        winner_text = f"{jester_name} (JESTER) WINS! Everyone else loses."
    else:
        winner_text = f"{winner.upper()} WINS!" if winner else "UNKNOWN WINS!"
    ctx.add_event("system", winner_text)
    ctx.add_event("system", "")  # Empty line
    ctx.add_event("system", "ROLE REVEAL:")

    for player in ctx.game_state.players:
        role_text = player.role.name.lower()
        ctx.add_event("system", f"{player.name}: {role_text}")

    # Human games: go straight to trashtalk (no round-robin discussion, no MVP vote)
    if ctx.game_state.has_human_player():
        ctx.add_event("system", "Postgame trashtalk begins!")
        ctx.phase_data["trashtalk_messages"] = []
        ctx.phase_data["player_last_message_index"] = {}
        return StepResult(next_step="trashtalk_poll", next_index=0)

    # AI-only games: round-robin discussion then MVP vote
    ctx.add_event("system", "Postgame discussion phase begins.")
    ctx.phase_data["postgame_messages"] = []
    ctx.phase_data["mvp_votes"] = []

    return StepResult(next_step="postgame_discussion", next_index=0)


@register_handler("postgame_discussion")
def handle_postgame_discussion(ctx: StepContext) -> StepResult:
    """Each player shares postgame thoughts (sequential)."""
    all_players = ctx.game_state.players
    index = ctx.step_index

    if index >= len(all_players):
        ctx.add_event("system", "Postgame discussion phase ends.")
        ctx.add_event("system", "MVP voting phase begins.")
        return StepResult(next_step="mvp_voting", next_index=0)

    player = all_players[index]
    message = None

    # Check if this is a human player
    if player.is_human:
        logging.info(f"Human player {player.name} - waiting for postgame input")

        if ctx.emit_status:
            ctx.emit_status("waiting_message", waiting_player=player.name, is_interrupt=False, is_respond=False)

        human_input = wait_for_human_input(ctx, "discussion", {"label": "Share your postgame thoughts"})
        logging.info(f"Human postgame input received for {player.name}: {human_input}")

        if human_input and human_input.get("type") == "discussion":
            message = human_input.get("message", "").strip()[:500]
        else:
            logging.warning(f"No valid human input for {player.name}, human_input={human_input}")
    else:
        message = execute_postgame_discussion(ctx, player)

    if message:
        ctx.add_event("discussion", message, "all", player=player.name)
        ctx.phase_data["postgame_messages"].append({
            "player": player.name,
            "message": message
        })

    return StepResult(next_step="postgame_discussion", next_index=index + 1)


@register_handler("mvp_voting")
def handle_mvp_voting(ctx: StepContext) -> StepResult:
    """All players vote for MVP (parallel for AI, sequential for human)."""
    all_players = ctx.game_state.players
    all_names = [p.name for p in all_players]

    results = []

    # Check if there's a human player who needs to vote
    human_player = ctx.game_state.get_human_player()
    if human_player:
        others = [p.name for p in all_players if p.name != human_player.name]

        logging.info(f"Human player {human_player.name} - waiting for MVP vote")

        if ctx.emit_status:
            ctx.emit_status("waiting_message", waiting_player=human_player.name, is_interrupt=False, is_respond=False)

        human_input = wait_for_human_input(ctx, "mvp_vote", {"options": others})
        logging.info(f"Human MVP vote received for {human_player.name}: {human_input}")

        if human_input and human_input.get("type") == "mvp_vote":
            target = human_input.get("target")
            reason = human_input.get("reason", "Good game.")

            # Validate: can't vote for self, must be valid player
            if target == human_player.name or (target and target not in all_names):
                target = random.choice(others) if others else None

            if not target:
                target = random.choice(others) if others else None

            ctx.add_event("vote", f"I vote {target}. {reason}", "all", player=human_player.name)
            results.append({"player": human_player.name, "target": target, "reason": reason})

    # Now get AI player votes in parallel
    ai_players = [p for p in all_players if not p.is_human]

    def mvp_vote_func(player):
        prompt = build_mvp_vote_prompt(ctx.game_state, player)
        messages = [{"role": "user", "content": prompt}]

        try:
            response = call_llm(
                player, ctx.llm_client, messages, "mvp_vote", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "mvp_vote", "schema": MVP_VOTE_SCHEMA}},
                temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

            target, reason = parse_mvp_vote(response)

            # Validate: must be a valid player (self-votes allowed)
            if target and target not in all_names:
                others = [p.name for p in all_players if p.name != player.name]
                target = random.choice(others) if others else None
                reason = reason or "Good game."

            if not target:
                others = [p.name for p in all_players if p.name != player.name]
                target = random.choice(others) if others else None
                reason = reason or "Good game."

            ctx.add_event("vote", f"I vote {target}. {reason}", "all", player=player.name)

            return {"player": player.name, "target": target, "reason": reason}

        except LLMCancelledException:
            raise
        except Exception:
            others = [p.name for p in all_players if p.name != player.name]
            target = random.choice(others) if others else None
            ctx.add_event("vote", f"I vote {target}. Good game.", "all", player=player.name)
            return {"player": player.name, "target": target, "reason": "Good game."}

    ai_results = execute_parallel(ai_players, mvp_vote_func, ctx)
    results.extend(ai_results)
    ctx.phase_data["mvp_votes"] = results

    resolve_mvp_voting(ctx.game_state)
    ctx.game_state.game_over = True

    return StepResult(next_step="game_end", next_index=0)


@register_handler("game_end")
def handle_game_end(ctx: StepContext) -> StepResult:
    """Game is fully over."""
    return StepResult()


# =============================================================================
# TRASHTALK HANDLERS (Human games only - infinite discussion)
# =============================================================================

@register_handler("trashtalk_poll")
def handle_trashtalk_poll(ctx: StepContext) -> StepResult:
    """Poll players to see who wants to trashtalk. Runs until human ends it."""

    # Check if human requested to end trashtalk
    if ctx.game_state.end_trashtalk_requested:
        ctx.game_state.end_trashtalk_requested = False
        ctx.add_event("system", "Postgame discussion ends.")
        ctx.game_state.game_over = True
        return StepResult(next_step="game_end", next_index=0)

    messages = ctx.phase_data.get("trashtalk_messages", [])

    logging.info(f"[TRASHTALK POLL] Messages so far: {len(messages)}")

    # Check if human has requested interrupt - they get priority
    if ctx.game_state.human_interrupt_requested:
        ctx.game_state.human_interrupt_requested = False
        human_name = ctx.game_state.human_player_name
        ctx.phase_data["next_speaker"] = human_name
        ctx.phase_data["is_interrupt"] = True
        ctx.phase_data["is_respond"] = False
        logging.info(f"[TRASHTALK POLL] Human interrupt priority: {human_name}")
        return StepResult(next_step="trashtalk_message", next_index=len(messages))

    # Build speaker order from all players if not already set
    if "speaker_order" not in ctx.phase_data:
        all_players = ctx.game_state.players[:]
        random.shuffle(all_players)
        ctx.phase_data["speaker_order"] = [p.name for p in all_players]
        ctx.phase_data["current_speaker_index"] = 0

    speaker_order = ctx.phase_data.get("speaker_order", [])
    speaker_idx = ctx.phase_data.get("current_speaker_index", 0)

    if not speaker_order:
        # No speakers, wait a bit and poll again
        gevent.sleep(0.5)
        return StepResult(next_step="trashtalk_poll", next_index=0)

    last_speaker = ctx.phase_data.get("last_speaker", None)

    if ctx.emit_status:
        ctx.emit_status("turn_polling", waiting_player=None)

    # Poll AI players
    interrupting, responding, passing = poll_for_trashtalk_actions(ctx, last_speaker)

    # Filter out human from results (safety check)
    human_name = ctx.game_state.human_player_name
    if human_name:
        interrupting = [p for p in interrupting if p != human_name]
        responding = [p for p in responding if p != human_name]
        passing = [p for p in passing if p != human_name]

    logging.info(f"[TRASHTALK POLL] Results: interrupting={interrupting}, responding={responding}, passing={passing}")

    last_was_respond = ctx.phase_data.get("last_was_respond", False)
    if last_was_respond:
        responding = []

    # Track passes for this round
    round_passes = ctx.phase_data.get("round_passes", [])
    for passer in passing:
        if passer not in round_passes:
            round_passes.append(passer)
    ctx.phase_data["round_passes"] = round_passes

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
        # Find next speaker in rotation (excluding human)
        chosen_speaker = None
        search_attempts = 0
        search_idx = speaker_idx

        while search_attempts < len(speaker_order):
            candidate_name = speaker_order[search_idx % len(speaker_order)]
            candidate = ctx.get_player_by_name(candidate_name)

            # Skip human players in rotation
            if candidate and not candidate.is_human:
                if candidate_name not in round_passes:
                    chosen_speaker = candidate_name
                    ctx.phase_data["current_speaker_index"] = search_idx
                    break

            search_idx += 1
            search_attempts += 1

        if not chosen_speaker:
            # Everyone passed, reset and continue
            ctx.phase_data["round_passes"] = []
            ctx.phase_data["current_speaker_index"] = 0
            gevent.sleep(0.5)  # Small delay before next round
            return StepResult(next_step="trashtalk_poll", next_index=0)

        ctx.phase_data["next_speaker"] = chosen_speaker
        ctx.phase_data["is_interrupt"] = False
        ctx.phase_data["is_respond"] = False

    return StepResult(next_step="trashtalk_message", next_index=len(messages))


@register_handler("trashtalk_message")
def handle_trashtalk_message(ctx: StepContext) -> StepResult:
    """Get a trashtalk message from the next speaker."""

    # Check if human requested to end (in case it came during message generation)
    if ctx.game_state.end_trashtalk_requested:
        ctx.game_state.end_trashtalk_requested = False
        ctx.add_event("system", "Postgame discussion ends.")
        ctx.game_state.game_over = True
        return StepResult(next_step="game_end", next_index=0)

    speaker_name = ctx.phase_data.get("next_speaker")
    is_interrupt = ctx.phase_data.get("is_interrupt", False)
    is_respond = ctx.phase_data.get("is_respond", False)

    logging.info(f"[TRASHTALK MESSAGE] Getting message from {speaker_name}")

    if not speaker_name:
        return StepResult(next_step="trashtalk_poll", next_index=0)

    speaker = ctx.get_player_by_name(speaker_name)
    if not speaker:
        return StepResult(next_step="trashtalk_poll", next_index=0)

    if ctx.emit_status:
        ctx.emit_status("waiting_message", waiting_player=speaker_name, is_interrupt=is_interrupt, is_respond=is_respond)

    message = None

    # Check if this is a human player
    if speaker.is_human:
        human_input = wait_for_human_input(ctx, "discussion", {"label": "Trashtalk!", "is_interrupt": is_interrupt})

        if human_input and human_input.get("type") == "discussion":
            message = human_input.get("message", "").strip()[:500]
    else:
        message = get_trashtalk_message(ctx, speaker, is_interrupt, is_respond)

    if message:
        msg_index = len(ctx.phase_data.get("trashtalk_messages", []))
        if "player_last_message_index" not in ctx.phase_data:
            ctx.phase_data["player_last_message_index"] = {}
        ctx.phase_data["player_last_message_index"][speaker_name] = msg_index

        if is_interrupt:
            turn_type = "interrupt"
        elif is_respond:
            turn_type = "respond"
        else:
            turn_type = "regular"

        ctx.add_event("discussion", message, "all", player=speaker_name,
                     metadata={"turn_type": turn_type})

        if "trashtalk_messages" not in ctx.phase_data:
            ctx.phase_data["trashtalk_messages"] = []
        ctx.phase_data["trashtalk_messages"].append({
            "player": speaker_name,
            "message": message,
            "is_interrupt": is_interrupt,
            "is_respond": is_respond
        })

        ctx.phase_data["last_speaker"] = speaker_name
        ctx.phase_data["last_was_respond"] = is_respond
        ctx.phase_data["round_passes"] = []

        # Move speaker to end of order
        speaker_order = ctx.phase_data.get("speaker_order", [])
        if speaker_name in speaker_order:
            speaker_order.remove(speaker_name)
            speaker_order.append(speaker_name)
            ctx.phase_data["speaker_order"] = speaker_order
            ctx.phase_data["current_speaker_index"] = 0

    return StepResult(next_step="trashtalk_poll", next_index=0)
