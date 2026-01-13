"""
Postgame phase step handlers.

Handlers for postgame: role reveal, discussion, MVP voting.
"""

import logging
import random
import gevent
from gevent import Greenlet

from . import register_handler, STEP_HANDLERS
from ..runner import StepResult, StepContext
from ..game_state import GameState
from ..llm_caller import call_llm, parse_text, parse_mvp_vote, MVP_VOTE_SCHEMA
from llm.prompts import build_postgame_discussion_prompt, build_mvp_vote_prompt
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


def execute_parallel(players, func, ctx: StepContext):
    """Execute a function for multiple players in parallel."""
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
        role_text = "mafia" if player.team == "mafia" else player.role.name.lower()
        ctx.add_event("system", f"{player.name}: {role_text}")

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

        ctx.game_state.set_waiting_for_human("discussion", {"label": "Share your postgame thoughts"})
        if ctx.emit_game_state:
            ctx.emit_game_state()
        gevent.sleep(0.05)  # Yield to allow socket to send before blocking

        human_input = ctx.wait_for_human() if ctx.wait_for_human else None
        logging.info(f"Human postgame input received for {player.name}: {human_input}")
        ctx.game_state.clear_waiting_for_human()

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

        ctx.game_state.set_waiting_for_human("mvp_vote", {"options": others})
        if ctx.emit_game_state:
            ctx.emit_game_state()
        gevent.sleep(0.05)  # Yield to allow socket to send before blocking

        human_input = ctx.wait_for_human() if ctx.wait_for_human else None
        logging.info(f"Human MVP vote received for {human_player.name}: {human_input}")
        ctx.game_state.clear_waiting_for_human()

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

            # Validate: can't vote for self, must be valid player
            if target == player.name or (target and target not in all_names):
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
