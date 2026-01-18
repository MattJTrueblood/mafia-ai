"""
Night phase step handlers.

All handlers for night-time actions: mafia discussion/vote, doctor, sheriff, vigilante.
"""

import logging
import gevent
from typing import List

from . import register_handler, STEP_HANDLERS
from ..runner import StepResult, StepContext
from ..game_state import GameState
from ..rules import can_doctor_protect, get_investigation_result, DEFAULT_RULES
from ..llm_caller import (
    call_llm, parse_target, parse_text, build_target_schema
)
from ..utils import (
    execute_parallel,
    execute_scratchpad_writing,
    wait_for_human_input,
)
from llm.prompts import (
    build_mafia_discussion_prompt,
    build_mafia_vote_prompt,
    build_role_discussion_prompt,
    build_role_action_prompt,
    build_sheriff_post_investigation_prompt,
)


# =============================================================================
# VISIBILITY HELPERS
# =============================================================================

def get_mafia_visibility(game_state: GameState) -> List[str]:
    """Get list of mafia player names for event visibility."""
    return [p.name for p in game_state.players if p.role and p.role.name in ("Mafia", "Godfather")]


def should_write_night_scratchpad(player) -> bool:
    """Determine if AI player should write scratchpad at night start.

    Human players don't write scratchpad notes.
    """
    if not player.alive:
        return False
    if player.is_human:
        return False
    role_name = player.role.name if player.role else None
    return role_name in ["Doctor", "Sheriff", "Vigilante", "Mafia", "Godfather"]


# =============================================================================
# EXECUTOR HELPERS
# =============================================================================

def execute_mafia_discussion(ctx: StepContext, mafia, previous_messages: list) -> str:
    """Execute a mafia member's discussion message."""
    prompt = build_mafia_discussion_prompt(ctx.game_state, mafia, previous_messages)
    messages = [{"role": "user", "content": prompt}]

    response = call_llm(
        mafia, ctx.llm_client, messages, "mafia_discussion", ctx.game_state,
        temperature=0.8, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
    )

    content = parse_text(response, mafia.name, max_length=1000)
    return content if content else "No comment."


def execute_role_discussion(ctx: StepContext, player, role_type: str) -> str:
    """Execute a role's discussion/thinking phase."""
    alive_names = [p.name for p in ctx.get_alive_players()]
    prompt = build_role_discussion_prompt(ctx.game_state, player, role_type, alive_names)
    messages = [{"role": "user", "content": prompt}]

    response = call_llm(
        player, ctx.llm_client, messages, f"{role_type}_discussion", ctx.game_state,
        temperature=0.8, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
    )

    content = parse_text(response, player.name, max_length=1000)
    return content if content else "No comment."


def execute_role_action(ctx: StepContext, player, role_type: str) -> str:
    """Execute a role's action (target only)."""
    alive_names = [p.name for p in ctx.get_alive_players()]
    discussion = ctx.phase_data.get(f"{role_type}_discussion", "")
    prompt = build_role_action_prompt(ctx.game_state, player, role_type, alive_names, discussion)
    messages = [{"role": "user", "content": prompt}]

    allow_abstain = (role_type == "vigilante" and DEFAULT_RULES.vigilante_can_abstain)
    target_schema = build_target_schema(alive_names, allow_abstain=allow_abstain)

    try:
        response = call_llm(
            player, ctx.llm_client, messages, f"{role_type}_action", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": f"{role_type}_action", "schema": target_schema}},
            temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        target = parse_target(response, allow_abstain=allow_abstain)

        if target and target not in alive_names:
            logging.warning(f"{role_type.capitalize()} {player.name} selected invalid target: {target}")
            target = None

        return target
    except Exception as e:
        logging.error(f"Error executing {role_type} action for {player.name}: {e}", exc_info=True)
        return None


def execute_sheriff_post_investigation(ctx: StepContext, sheriff, target: str, result: str) -> str:
    """Execute sheriff's reaction after seeing investigation result."""
    prompt = build_sheriff_post_investigation_prompt(ctx.game_state, sheriff, target, result)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = call_llm(
            sheriff, ctx.llm_client, messages, "sheriff_post_investigation", ctx.game_state,
            temperature=0.8, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        content = parse_text(response, sheriff.name, max_length=800)
        return content if content else None
    except Exception as e:
        logging.error(f"Sheriff post-investigation failed for {sheriff.name}: {e}", exc_info=True)
        return None


# =============================================================================
# RESOLUTION HELPERS
# =============================================================================

def tally_mafia_votes(game_state: GameState):
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


def resolve_night_actions(game_state: GameState):
    """Resolve night actions and apply kills simultaneously."""
    protected_players = game_state.phase_data.get("protected_players", [])

    # Collect all kills BEFORE applying any (truly simultaneous resolution)
    pending_kills = []
    pending_names = set()

    # Mafia kill
    mafia_target = game_state.phase_data.get("mafia_kill_target")
    if mafia_target and mafia_target not in protected_players:
        target_player = game_state.get_player_by_name(mafia_target)
        if target_player and target_player.alive:
            pending_kills.append((mafia_target, "mafia_kill"))
            pending_names.add(mafia_target)

    # Vigilante kills
    vigilante_kills = game_state.phase_data.get("vigilante_kills", [])
    for vig_data in vigilante_kills:
        vig_target = vig_data.get("target")
        if vig_target and vig_target not in protected_players and vig_target not in pending_names:
            target_player = game_state.get_player_by_name(vig_target)
            if target_player and target_player.alive:
                pending_kills.append((vig_target, "vigilante_kill"))
                pending_names.add(vig_target)

    # Now apply all kills at once
    for target_name, reason in pending_kills:
        target_player = game_state.get_player_by_name(target_name)
        target_player.alive = False
        game_state.add_event("death", f"{target_name} has been found dead, killed during the night!",
                            "all", metadata={"player": target_name, "reason": reason})

    if not pending_kills:
        game_state.add_event("system", "Nobody was killed last night.", "all")


# =============================================================================
# NIGHT START HANDLERS
# =============================================================================

@register_handler("night_start")
def handle_night_start(ctx: StepContext) -> StepResult:
    """Initialize night phase."""
    mafia_visibility = get_mafia_visibility(ctx.game_state)

    ctx.game_state.phase_data = {
        "mafia_discussion_messages": [],
        "mafia_votes": [],
        "protected_players": [],
        "vigilante_kills": [],
    }

    ctx.add_event("phase_change", f"Night {ctx.day_number} begins.")
    ctx.add_event("system", "Mafia night actions begin.", mafia_visibility)

    if ctx.emit_status:
        ctx.emit_status("night_start")

    return StepResult(next_step="scratchpad_night_start", next_index=0)


@register_handler("scratchpad_night_start")
def handle_scratchpad_night_start(ctx: StepContext) -> StepResult:
    """Special roles write private strategic notes at night start."""
    eligible_players = [p for p in ctx.get_alive_players() if should_write_night_scratchpad(p)]

    if eligible_players:
        def scratchpad_func(player):
            return execute_scratchpad_writing(ctx, player, "night_start")

        execute_parallel(eligible_players, scratchpad_func, ctx)

    return StepResult(next_step="mafia_discussion", next_index=0)


# =============================================================================
# MAFIA HANDLERS
# =============================================================================

@register_handler("mafia_discussion")
def handle_mafia_discussion(ctx: StepContext) -> StepResult:
    """Mafia members discuss who to kill. Waits for human input if mafia member is human."""
    mafia_players = ctx.get_players_by_role("Mafia") + ctx.get_players_by_role("Godfather")
    mafia_visibility = get_mafia_visibility(ctx.game_state)
    index = ctx.step_index

    if index == 0:
        ctx.add_event("system", "Mafia Discussion phase begins.", mafia_visibility)

    if index >= len(mafia_players) * 2: # allow 2 rounds of discussion
        ctx.add_event("system", "Mafia Discussion phase ends.", mafia_visibility)
        ctx.add_event("system", "Mafia vote phase begins.", mafia_visibility)
        return StepResult(next_step="mafia_vote", next_index=0)

    mafia = mafia_players[index % len(mafia_players)]
    previous_messages = ctx.phase_data.get("mafia_discussion_messages", [])

    message = None

    # Check if this mafia member is human
    if mafia.is_human:
        human_input = wait_for_human_input(ctx, "discussion", {"label": "Mafia Discussion"})

        if human_input and human_input.get("type") == "discussion":
            message = human_input.get("message", "").strip()[:1000]
        if not message:
            message = "(says nothing)"
    else:
        message = execute_mafia_discussion(ctx, mafia, previous_messages)

    ctx.phase_data["mafia_discussion_messages"].append({
        "player": mafia.name,
        "message": message
    })

    ctx.add_event("mafia_chat", f"[Mafia Discussion] {mafia.name}: {message}",
                  mafia_visibility, player=mafia.name, priority=7)

    return StepResult(next_step="mafia_discussion", next_index=index + 1)


@register_handler("mafia_vote")
def handle_mafia_vote(ctx: StepContext) -> StepResult:
    """Mafia members vote on kill target. Human mafia votes first, then AI in parallel."""
    mafia_players = ctx.get_players_by_role("Mafia") + ctx.get_players_by_role("Godfather")
    mafia_visibility = get_mafia_visibility(ctx.game_state)
    discussion_messages = ctx.phase_data.get("mafia_discussion_messages", [])
    alive_names = [p.name for p in ctx.get_alive_players()]

    results = []

    # Check if any mafia member is human
    human_mafia = None
    for mafia in mafia_players:
        if mafia.is_human:
            human_mafia = mafia
            break

    if human_mafia:
        # Wait for human mafia vote first
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Vote to Kill"})

        target = None
        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None

        vote_msg = f"[Mafia Vote] {human_mafia.name} votes to kill {target}" if target else f"[Mafia Vote] {human_mafia.name} abstains"
        ctx.add_event("mafia_chat", vote_msg, mafia_visibility, player=human_mafia.name, priority=7)

        results.append({"player": human_mafia.name, "target": target})

    # AI mafia vote in parallel
    ai_mafia = [m for m in mafia_players if not m.is_human]

    def vote_func(mafia):
        prompt = build_mafia_vote_prompt(ctx.game_state, mafia, [], discussion_messages)
        messages = [{"role": "user", "content": prompt}]
        target_schema = build_target_schema(alive_names, allow_abstain=True)

        response = call_llm(
            mafia, ctx.llm_client, messages, "mafia_vote", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": target_schema}},
            temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        target = parse_target(response)
        if target and target not in alive_names:
            target = None

        vote_msg = f"[Mafia Vote] {mafia.name} votes to kill {target}" if target else f"[Mafia Vote] {mafia.name} abstains"
        ctx.add_event("mafia_chat", vote_msg, mafia_visibility, player=mafia.name, priority=7)

        return {"player": mafia.name, "target": target}

    if ai_mafia:
        ai_results = execute_parallel(ai_mafia, vote_func, ctx)
        results.extend(ai_results)

    ctx.phase_data["mafia_votes"] = results

    tally_mafia_votes(ctx.game_state)
    target = ctx.phase_data.get("mafia_kill_target")
    if target:
        ctx.add_event("system", f"Mafia has chosen to kill {target}.", mafia_visibility)
    ctx.add_event("system", "Mafia night actions end.", mafia_visibility)

    return StepResult(next_step="doctor_discuss", next_index=0)


# =============================================================================
# DOCTOR HANDLERS
# =============================================================================

@register_handler("doctor_discuss")
def handle_doctor_discuss(ctx: StepContext) -> StepResult:
    """Doctor thinks through protection options. Skips discussion for human players."""
    doctor_players = [p for p in ctx.get_players_by_role("Doctor") if p.alive]
    index = ctx.step_index

    if not doctor_players:
        return StepResult(next_step="sheriff_discuss", next_index=0)

    if index >= len(doctor_players):
        return StepResult(next_step="doctor_act", next_index=0)

    doctor = doctor_players[index]
    doctor_visibility = [doctor.name]

    if index == 0:
        all_doctor_names = [p.name for p in doctor_players]
        ctx.add_event("system", "Doctor night phase begins.", all_doctor_names)

    # Skip discussion for human players (they don't need to think out loud)
    if not doctor.is_human:
        discussion = execute_role_discussion(ctx, doctor, "doctor")
        ctx.add_event("role_action", f"[Doctor Discussion] {doctor.name}: {discussion}",
                      doctor_visibility, player=doctor.name, priority=6)

    return StepResult(next_step="doctor_discuss", next_index=index + 1)


@register_handler("doctor_act")
def handle_doctor_act(ctx: StepContext) -> StepResult:
    """Doctor chooses who to protect. Waits for human input if doctor is human."""
    doctor_players = [p for p in ctx.get_players_by_role("Doctor") if p.alive]
    index = ctx.step_index

    if index >= len(doctor_players):
        if doctor_players:
            all_doctor_names = [p.name for p in doctor_players]
            ctx.add_event("system", "Doctor night phase ends.", all_doctor_names)
        return StepResult(next_step="sheriff_discuss", next_index=0)

    doctor = doctor_players[index]
    doctor_visibility = [doctor.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if doctor is human
    if doctor.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Protect Someone"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, doctor, "doctor")

    if target:
        can_protect, reason = can_doctor_protect(DEFAULT_RULES, doctor.role, target)
        if not can_protect:
            ctx.add_event("role_action", f"Doctor {doctor.name}: {reason}.",
                         doctor_visibility, player=doctor.name, priority=7)
            target = None

    if target:
        doctor.role.last_protected = target
        if "protected_players" not in ctx.phase_data:
            ctx.phase_data["protected_players"] = []
        ctx.phase_data["protected_players"].append(target)
        ctx.add_event("role_action", f"Doctor {doctor.name} protects {target}.",
                     doctor_visibility, player=doctor.name, priority=7)

    return StepResult(next_step="doctor_act", next_index=index + 1)


# =============================================================================
# SHERIFF HANDLERS
# =============================================================================

@register_handler("sheriff_discuss")
def handle_sheriff_discuss(ctx: StepContext) -> StepResult:
    """Sheriff thinks through investigation options. Skips discussion for human players."""
    sheriff_players = [p for p in ctx.get_players_by_role("Sheriff") if p.alive]
    index = ctx.step_index

    if not sheriff_players:
        return StepResult(next_step="vigilante_discuss", next_index=0)

    if index >= len(sheriff_players):
        return StepResult(next_step="sheriff_act", next_index=0)

    sheriff = sheriff_players[index]
    sheriff_visibility = [sheriff.name]

    if index == 0:
        all_sheriff_names = [p.name for p in sheriff_players]
        ctx.add_event("system", "Sheriff night phase begins.", all_sheriff_names)

    # Skip discussion for human players
    if not sheriff.is_human:
        discussion = execute_role_discussion(ctx, sheriff, "sheriff")
        ctx.add_event("role_action", f"[Sheriff Discussion] {sheriff.name}: {discussion}",
                      sheriff_visibility, player=sheriff.name, priority=6)

    return StepResult(next_step="sheriff_discuss", next_index=index + 1)


@register_handler("sheriff_act")
def handle_sheriff_act(ctx: StepContext) -> StepResult:
    """Sheriff investigates a player. Waits for human input if sheriff is human."""
    sheriff_players = [p for p in ctx.get_players_by_role("Sheriff") if p.alive]
    index = ctx.step_index

    if index >= len(sheriff_players):
        if sheriff_players:
            all_sheriff_names = [p.name for p in sheriff_players]
            ctx.add_event("system", "Sheriff night phase ends.", all_sheriff_names)
        return StepResult(next_step="vigilante_discuss", next_index=0)

    sheriff = sheriff_players[index]
    sheriff_visibility = [sheriff.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if sheriff is human
    if sheriff.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Investigate Someone"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, sheriff, "sheriff")

    if target:
        target_player = ctx.get_player_by_name(target)
        if target_player:
            # Use investigation helper that handles Godfather/Miller special cases
            result, ability_triggered = get_investigation_result(
                ctx.rules, target_player, ctx.game_state
            )

            # Track investigations this night for multi-sheriff immunity handling
            night_key = f"night_{ctx.day_number}_investigated"
            if night_key not in ctx.phase_data:
                ctx.phase_data[night_key] = set()

            # Consume immunity/false-positive only once per night (even with multiple sheriffs)
            if ability_triggered and target not in ctx.phase_data[night_key]:
                ctx.phase_data[night_key].add(target)
                if target_player.role.name == "Godfather":
                    target_player.role.investigation_immunity_used = True
                elif target_player.role.name == "Miller":
                    target_player.role.false_positive_used = True

            sheriff.role.investigations.append((target, result))

            ctx.add_event("role_action", f"Sheriff {sheriff.name} investigates {target}.",
                         sheriff_visibility, player=sheriff.name, priority=7)
            ctx.add_event("role_action", f"{target} is {result.upper()}!",
                         sheriff_visibility, player=sheriff.name, priority=8,
                         metadata={"target": target, "result": result})

            # Only AI sheriff gets post-investigation reaction
            if not sheriff.is_human:
                reaction = execute_sheriff_post_investigation(ctx, sheriff, target, result)
                if reaction:
                    ctx.add_event("role_action", f"[Sheriff Discussion] {sheriff.name}: {reaction}",
                                 sheriff_visibility, player=sheriff.name, priority=9)

    return StepResult(next_step="sheriff_act", next_index=index + 1)


# =============================================================================
# VIGILANTE HANDLERS
# =============================================================================

@register_handler("vigilante_discuss")
def handle_vigilante_discuss(ctx: StepContext) -> StepResult:
    """Vigilante thinks through options. Skips discussion for human players."""
    # Cache eligible vigilantes at start of phase
    if "vigilante_eligible" not in ctx.phase_data:
        ctx.phase_data["vigilante_eligible"] = [
            p.name for p in ctx.get_players_by_role("Vigilante")
            if p.alive and not p.role.bullet_used
        ]

    eligible_names = ctx.phase_data["vigilante_eligible"]
    vigilante_players = [ctx.get_player_by_name(n) for n in eligible_names]
    index = ctx.step_index

    if not vigilante_players:
        return StepResult(next_step="night_resolve", next_index=0)

    if index >= len(vigilante_players):
        return StepResult(next_step="vigilante_act", next_index=0)

    vigilante = vigilante_players[index]
    vigilante_visibility = [vigilante.name]

    if index == 0:
        all_vig_names = [p.name for p in vigilante_players]
        ctx.add_event("system", "Vigilante night phase begins.", all_vig_names)

    # Skip discussion for human players
    if not vigilante.is_human:
        discussion = execute_role_discussion(ctx, vigilante, "vigilante")
        ctx.add_event("role_action", f"[Vigilante Discussion] {vigilante.name}: {discussion}",
                      vigilante_visibility, player=vigilante.name, priority=6)

    return StepResult(next_step="vigilante_discuss", next_index=index + 1)


@register_handler("vigilante_act")
def handle_vigilante_act(ctx: StepContext) -> StepResult:
    """Vigilante decides whether to shoot. Waits for human input if vigilante is human."""
    if "vigilante_eligible" not in ctx.phase_data:
        ctx.phase_data["vigilante_eligible"] = [
            p.name for p in ctx.get_players_by_role("Vigilante")
            if p.alive and not p.role.bullet_used
        ]

    eligible_names = ctx.phase_data["vigilante_eligible"]
    vigilante_players = [ctx.get_player_by_name(n) for n in eligible_names]
    index = ctx.step_index

    if index >= len(vigilante_players):
        if vigilante_players:
            all_vig_names = [p.name for p in vigilante_players]
            ctx.add_event("system", "Vigilante night phase ends.", all_vig_names)
        return StepResult(next_step="night_resolve", next_index=0)

    vigilante = vigilante_players[index]
    vigilante_visibility = [vigilante.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if vigilante is human
    if vigilante.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Shoot Someone (or Pass)"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, vigilante, "vigilante")

    if target:
        vigilante.role.bullet_used = True
        if "vigilante_kills" not in ctx.phase_data:
            ctx.phase_data["vigilante_kills"] = []
        ctx.phase_data["vigilante_kills"].append({"vigilante": vigilante.name, "target": target})
        ctx.add_event("role_action", f"Vigilante shoots {target} tonight.",
                     vigilante_visibility, player=vigilante.name, priority=7)
    else:
        ctx.add_event("role_action", f"{vigilante.name} chooses not to shoot tonight.",
                     vigilante_visibility, player=vigilante.name, priority=7)

    return StepResult(next_step="vigilante_act", next_index=index + 1)


# =============================================================================
# NIGHT RESOLVE
# =============================================================================

@register_handler("night_resolve")
def handle_night_resolve(ctx: StepContext) -> StepResult:
    """Resolve all night actions and transition to day."""
    from ..win_conditions import check_win_conditions

    resolve_night_actions(ctx.game_state)
    ctx.add_event("phase_change", f"Night {ctx.day_number} ends.")

    # Check win conditions
    winner = check_win_conditions(ctx.game_state)
    if winner:
        ctx.game_state.winner = winner
        ctx.game_state.start_postgame_phase()
        return StepResult(next_step="postgame_reveal", next_index=0)

    # Transition to day
    ctx.game_state.start_day_phase()
    return StepResult(next_step="day_start", next_index=0)
