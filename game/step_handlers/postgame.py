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
    winner_text = "TOWN" if ctx.game_state.winner == "town" else "MAFIA"
    ctx.add_event("system", f"{winner_text} WINS!")
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
    """All players vote for MVP (parallel)."""
    all_players = ctx.game_state.players
    all_names = [p.name for p in all_players]

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

    results = execute_parallel(all_players, mvp_vote_func, ctx)
    ctx.phase_data["mvp_votes"] = results

    resolve_mvp_voting(ctx.game_state)
    ctx.game_state.game_over = True

    return StepResult(next_step="game_end", next_index=0)


@register_handler("game_end")
def handle_game_end(ctx: StepContext) -> StepResult:
    """Game is fully over."""
    return StepResult()
