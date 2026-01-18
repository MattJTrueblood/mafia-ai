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
    build_mason_discussion_prompt,
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


def get_mason_visibility(game_state: GameState) -> List[str]:
    """Get list of mason player names for event visibility."""
    return [p.name for p in game_state.players if p.role and p.role.name == "Mason"]


def should_write_night_scratchpad(player) -> bool:
    """Determine if AI player should write scratchpad at night start.

    Human players don't write scratchpad notes.
    """
    if not player.alive:
        return False
    if player.is_human:
        return False
    role_name = player.role.name if player.role else None
    return role_name in ["Doctor", "Sheriff", "Vigilante", "Mafia", "Godfather", "Escort", "Tracker"]


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
    blocked_players = set(game_state.phase_data.get("blocked_players", []))

    # Build a map of who visited whom for tracker results
    visits = {}  # player_name -> target_name

    # Escort visits (escorts always visit, even if blocking someone)
    for p in game_state.players:
        if p.role and p.role.name == "Escort" and hasattr(p.role, 'block_history') and p.role.block_history:
            # Get this night's block target (last in history)
            visits[p.name] = p.role.block_history[-1]

    # Mafia visit (the designated killer) - only if not blocked
    mafia_target = game_state.phase_data.get("mafia_kill_target")
    mafia_killer = None  # Track who the designated killer is
    if mafia_target:
        mafia_votes = game_state.phase_data.get("mafia_votes", [])
        # The first mafia who voted for the target is considered the "visitor"
        for vote in mafia_votes:
            if vote.get("target") == mafia_target:
                mafia_killer = vote["player"]
                # Only record visit if not blocked
                if mafia_killer not in blocked_players:
                    visits[mafia_killer] = mafia_target
                break

    # Doctor visits - only if not blocked
    for doctor_data in game_state.phase_data.get("doctor_protections", []):
        if doctor_data.get("target"):
            doctor_name = doctor_data["doctor"]
            if doctor_name not in blocked_players:
                visits[doctor_name] = doctor_data["target"]
    # Also check simple protected_players list with doctor info
    for p in game_state.players:
        if p.role and p.role.name == "Doctor" and hasattr(p.role, 'last_protected') and p.role.last_protected:
            if p.name not in blocked_players:
                visits[p.name] = p.role.last_protected

    # Filter protected_players to remove protections from blocked doctors
    effective_protected = []
    for p in game_state.players:
        if p.role and p.role.name == "Doctor" and p.name not in blocked_players:
            if hasattr(p.role, 'last_protected') and p.role.last_protected:
                effective_protected.append(p.role.last_protected)

    # Vigilante visits - only if not blocked
    vigilante_kills = game_state.phase_data.get("vigilante_kills", [])
    for vig_data in vigilante_kills:
        if vig_data.get("target"):
            vig_name = vig_data["vigilante"]
            if vig_name not in blocked_players:
                visits[vig_name] = vig_data["target"]

    # Tracker visits - only if not blocked
    tracker_targets = game_state.phase_data.get("tracker_targets", [])
    for track_data in tracker_targets:
        tracker_name = track_data["tracker"]
        if tracker_name not in blocked_players:
            visits[tracker_name] = track_data["target"]

    # Process tracker results BEFORE kills (so tracker can see who visited even if they die)
    for track_data in tracker_targets:
        tracker_name = track_data["tracker"]
        tracked_player = track_data["target"]
        tracker_player = game_state.get_player_by_name(tracker_name)

        # If tracker is blocked, they learn nothing
        if tracker_name in blocked_players:
            if tracker_player and tracker_player.role:
                tracker_player.role.tracking_results.append((tracked_player, None))
            game_state.add_event("role_action",
                f"You were blocked and could not track anyone last night.",
                [tracker_name], player=tracker_name, priority=8,
                metadata={"blocked": True})
            continue

        # Who did the tracked player visit?
        visited = visits.get(tracked_player)

        # Store result on tracker role
        if tracker_player and tracker_player.role:
            tracker_player.role.tracking_results.append((tracked_player, visited))

        # Report result to tracker
        tracker_visibility = [tracker_name]
        if visited:
            game_state.add_event("role_action",
                f"Your target {tracked_player} visited {visited} last night.",
                tracker_visibility, player=tracker_name, priority=8,
                metadata={"tracked": tracked_player, "visited": visited})
        else:
            game_state.add_event("role_action",
                f"Your target {tracked_player} did not visit anyone last night.",
                tracker_visibility, player=tracker_name, priority=8,
                metadata={"tracked": tracked_player, "visited": None})

    # Find all Grandma players for immunity and visitor-killing
    grandma_names = set(p.name for p in game_state.players
                        if p.alive and p.role and p.role.name == "Grandma")

    # Collect all kills BEFORE applying any (truly simultaneous resolution)
    pending_kills = []
    pending_names = set()

    # Track who visits Grandma (for Grandma's kill ability)
    grandma_visitors = []  # List of (visitor_name, grandma_name)
    for visitor, visited in visits.items():
        if visited in grandma_names:
            grandma_visitors.append((visitor, visited))

    # Mafia kill - only if the designated killer is not blocked
    # Grandma is immune to night kills
    if mafia_target and mafia_target not in effective_protected:
        if mafia_killer and mafia_killer not in blocked_players:
            target_player = game_state.get_player_by_name(mafia_target)
            if target_player and target_player.alive:
                # Check if target is Grandma (immune to night kills)
                if target_player.role and target_player.role.name == "Grandma":
                    pass  # Grandma survives the attack
                else:
                    pending_kills.append((mafia_target, "mafia_kill"))
                    pending_names.add(mafia_target)

    # Vigilante kills - only if vigilante is not blocked
    # Grandma is immune to night kills
    for vig_data in vigilante_kills:
        vig_name = vig_data.get("vigilante")
        vig_target = vig_data.get("target")
        if vig_name in blocked_players:
            continue  # Blocked vigilante cannot kill
        if vig_target and vig_target not in effective_protected and vig_target not in pending_names:
            target_player = game_state.get_player_by_name(vig_target)
            if target_player and target_player.alive:
                # Check if target is Grandma (immune to night kills)
                if target_player.role and target_player.role.name == "Grandma":
                    pass  # Grandma survives the attack
                else:
                    pending_kills.append((vig_target, "vigilante_kill"))
                    pending_names.add(vig_target)

    # Grandma kills visitors (unless they're protected by doctor)
    for visitor, grandma_name in grandma_visitors:
        if visitor not in effective_protected and visitor not in pending_names:
            visitor_player = game_state.get_player_by_name(visitor)
            if visitor_player and visitor_player.alive:
                pending_kills.append((visitor, "grandma_kill"))
                pending_names.add(visitor)

    # Now apply all kills at once
    killed_names = set()
    for target_name, reason in pending_kills:
        target_player = game_state.get_player_by_name(target_name)
        target_player.alive = False
        killed_names.add(target_name)
        if reason == "grandma_kill":
            game_state.add_event("death", f"{target_name} visited Grandma and has been found dead!",
                                "all", metadata={"player": target_name, "reason": reason})
        else:
            game_state.add_event("death", f"{target_name} has been found dead, killed during the night!",
                                "all", metadata={"player": target_name, "reason": reason})

    # Check if any Executioner's target was killed (not lynched) - convert to fallback role
    if killed_names:
        from ..roles import ROLE_CLASSES
        rules = getattr(game_state, 'rules', None) or DEFAULT_RULES
        fallback_role_name = rules.executioner_becomes_on_target_death

        for p in game_state.players:
            if p.alive and p.role and p.role.name == "Executioner":
                if p.role.target in killed_names:
                    # Convert Executioner to fallback role
                    new_role_class = ROLE_CLASSES.get(fallback_role_name)
                    if new_role_class:
                        old_target = p.role.target
                        p.convert_to_role(new_role_class(), f"Target {old_target} died", game_state.day_number)
                        game_state.add_event("role_action",
                            f"Your target {old_target} has died. You are now a {fallback_role_name}.",
                            [p.name], player=p.name, priority=9)

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

    return StepResult(next_step="mason_discussion", next_index=0)


# =============================================================================
# MASON HANDLERS
# =============================================================================

@register_handler("mason_discussion")
def handle_mason_discussion(ctx: StepContext) -> StepResult:
    """Mason members discuss privately. Similar to mafia discussion but for town."""
    mason_players = ctx.get_players_by_role("Mason")
    mason_visibility = get_mason_visibility(ctx.game_state)
    index = ctx.step_index

    # Skip if no masons or only one mason (no one to talk to)
    if len(mason_players) < 2:
        return StepResult(next_step="escort_discuss", next_index=0)

    if index == 0:
        ctx.add_event("system", "Mason discussion phase begins.", mason_visibility)
        if "mason_discussion_messages" not in ctx.phase_data:
            ctx.phase_data["mason_discussion_messages"] = []

    # Allow 2 rounds of discussion
    if index >= len(mason_players) * 2:
        ctx.add_event("system", "Mason discussion phase ends.", mason_visibility)
        return StepResult(next_step="escort_discuss", next_index=0)

    mason = mason_players[index % len(mason_players)]
    previous_messages = ctx.phase_data.get("mason_discussion_messages", [])

    message = None

    # Check if this mason is human
    if mason.is_human:
        human_input = wait_for_human_input(ctx, "discussion", {"label": "Mason Discussion"})

        if human_input and human_input.get("type") == "discussion":
            message = human_input.get("message", "").strip()[:1000]
        if not message:
            message = "(says nothing)"
    else:
        from ..utils import execute_group_discussion
        message = execute_group_discussion(
            ctx, mason, "masons", previous_messages,
            build_mason_discussion_prompt, "mason_discussion"
        )

    ctx.phase_data["mason_discussion_messages"].append({
        "player": mason.name,
        "message": message
    })

    ctx.add_event("mason_chat", f"[Mason Discussion] {mason.name}: {message}",
                  mason_visibility, player=mason.name, priority=7)

    return StepResult(next_step="mason_discussion", next_index=index + 1)


# =============================================================================
# ESCORT HANDLERS
# =============================================================================

@register_handler("escort_discuss")
def handle_escort_discuss(ctx: StepContext) -> StepResult:
    """Escort thinks through blocking options. Skips discussion for human players."""
    escort_players = [p for p in ctx.get_players_by_role("Escort") if p.alive]
    index = ctx.step_index

    if not escort_players:
        return StepResult(next_step="doctor_discuss", next_index=0)

    if index >= len(escort_players):
        return StepResult(next_step="escort_act", next_index=0)

    escort = escort_players[index]
    escort_visibility = [escort.name]

    if index == 0:
        all_escort_names = [p.name for p in escort_players]
        ctx.add_event("system", "Escort night phase begins.", all_escort_names)

    # Skip discussion for human players
    if not escort.is_human:
        discussion = execute_role_discussion(ctx, escort, "escort")
        ctx.add_event("role_action", f"[Escort Discussion] {escort.name}: {discussion}",
                      escort_visibility, player=escort.name, priority=6)

    return StepResult(next_step="escort_discuss", next_index=index + 1)


@register_handler("escort_act")
def handle_escort_act(ctx: StepContext) -> StepResult:
    """Escort chooses who to block. Waits for human input if escort is human."""
    escort_players = [p for p in ctx.get_players_by_role("Escort") if p.alive]
    index = ctx.step_index

    if index >= len(escort_players):
        if escort_players:
            all_escort_names = [p.name for p in escort_players]
            ctx.add_event("system", "Escort night phase ends.", all_escort_names)
        return StepResult(next_step="doctor_discuss", next_index=0)

    escort = escort_players[index]
    escort_visibility = [escort.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if escort is human
    if escort.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Block Someone"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, escort, "escort")

    if target:
        # Store the blocked target
        if "blocked_players" not in ctx.phase_data:
            ctx.phase_data["blocked_players"] = []
        ctx.phase_data["blocked_players"].append(target)

        # Record in escort's history
        escort.role.block_history.append(target)

        ctx.add_event("role_action", f"Escort {escort.name} visits {target} tonight.",
                     escort_visibility, player=escort.name, priority=7)

    return StepResult(next_step="escort_act", next_index=index + 1)


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
        return StepResult(next_step="tracker_discuss", next_index=0)

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
        return StepResult(next_step="tracker_discuss", next_index=0)

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
# TRACKER HANDLERS
# =============================================================================

@register_handler("tracker_discuss")
def handle_tracker_discuss(ctx: StepContext) -> StepResult:
    """Tracker thinks through tracking options. Skips discussion for human players."""
    tracker_players = [p for p in ctx.get_players_by_role("Tracker") if p.alive]
    index = ctx.step_index

    if not tracker_players:
        return StepResult(next_step="vigilante_discuss", next_index=0)

    if index >= len(tracker_players):
        return StepResult(next_step="tracker_act", next_index=0)

    tracker = tracker_players[index]
    tracker_visibility = [tracker.name]

    if index == 0:
        all_tracker_names = [p.name for p in tracker_players]
        ctx.add_event("system", "Tracker night phase begins.", all_tracker_names)

    # Skip discussion for human players
    if not tracker.is_human:
        discussion = execute_role_discussion(ctx, tracker, "tracker")
        ctx.add_event("role_action", f"[Tracker Discussion] {tracker.name}: {discussion}",
                      tracker_visibility, player=tracker.name, priority=6)

    return StepResult(next_step="tracker_discuss", next_index=index + 1)


@register_handler("tracker_act")
def handle_tracker_act(ctx: StepContext) -> StepResult:
    """Tracker chooses who to track. Waits for human input if tracker is human."""
    tracker_players = [p for p in ctx.get_players_by_role("Tracker") if p.alive]
    index = ctx.step_index

    if index >= len(tracker_players):
        if tracker_players:
            all_tracker_names = [p.name for p in tracker_players]
            ctx.add_event("system", "Tracker night phase ends.", all_tracker_names)
        return StepResult(next_step="vigilante_discuss", next_index=0)

    tracker = tracker_players[index]
    tracker_visibility = [tracker.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if tracker is human
    if tracker.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Track Someone"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, tracker, "tracker")

    if target:
        # Store the tracking target - result will be determined at night_resolve
        if "tracker_targets" not in ctx.phase_data:
            ctx.phase_data["tracker_targets"] = []
        ctx.phase_data["tracker_targets"].append({
            "tracker": tracker.name,
            "target": target
        })

        ctx.add_event("role_action", f"Tracker {tracker.name} is watching {target} tonight.",
                     tracker_visibility, player=tracker.name, priority=7)

    return StepResult(next_step="tracker_act", next_index=index + 1)


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
        return StepResult(next_step="medium_discuss", next_index=0)

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
        return StepResult(next_step="medium_discuss", next_index=0)

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
# AMNESIAC HANDLERS
# =============================================================================

@register_handler("amnesiac_discuss")
def handle_amnesiac_discuss(ctx: StepContext) -> StepResult:
    """Amnesiac thinks through options. Skips discussion for human players."""
    # Only amnesiac who hasn't remembered yet
    amnesiac_players = [p for p in ctx.get_players_by_role("Amnesiac")
                        if p.alive and not p.role.has_remembered]
    index = ctx.step_index

    if not amnesiac_players:
        return StepResult(next_step="night_resolve", next_index=0)

    if index >= len(amnesiac_players):
        return StepResult(next_step="amnesiac_act", next_index=0)

    amnesiac = amnesiac_players[index]
    amnesiac_visibility = [amnesiac.name]

    if index == 0:
        all_amnesiac_names = [p.name for p in amnesiac_players]
        ctx.add_event("system", "Amnesiac night phase begins.", all_amnesiac_names)

    # Skip discussion for human players
    if not amnesiac.is_human:
        discussion = execute_role_discussion(ctx, amnesiac, "amnesiac")
        ctx.add_event("role_action", f"[Amnesiac Discussion] {amnesiac.name}: {discussion}",
                      amnesiac_visibility, player=amnesiac.name, priority=6)

    return StepResult(next_step="amnesiac_discuss", next_index=index + 1)


@register_handler("amnesiac_act")
def handle_amnesiac_act(ctx: StepContext) -> StepResult:
    """Amnesiac chooses a dead player to remember. Waits for human input if amnesiac is human."""
    from ..roles import ROLE_CLASSES

    amnesiac_players = [p for p in ctx.get_players_by_role("Amnesiac")
                        if p.alive and not p.role.has_remembered]
    index = ctx.step_index

    if index >= len(amnesiac_players):
        if amnesiac_players:
            all_amnesiac_names = [p.name for p in amnesiac_players]
            ctx.add_event("system", "Amnesiac night phase ends.", all_amnesiac_names)
        return StepResult(next_step="night_resolve", next_index=0)

    amnesiac = amnesiac_players[index]
    amnesiac_visibility = [amnesiac.name]

    # Get dead players as options
    dead_players = [p for p in ctx.game_state.players if not p.alive]
    dead_names = [p.name for p in dead_players]

    target = None

    if not dead_names:
        # No dead players to remember
        ctx.add_event("role_action", f"Amnesiac {amnesiac.name} has no one to remember yet.",
                     amnesiac_visibility, player=amnesiac.name, priority=7)
        return StepResult(next_step="amnesiac_act", next_index=index + 1)

    # Check if amnesiac is human
    if amnesiac.is_human:
        human_input = wait_for_human_input(ctx, "role_action",
            {"options": dead_names, "label": "Remember a dead player's role (or Pass)"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in dead_names:
                target = None
    else:
        # AI amnesiac selects a dead player
        target = execute_role_action(ctx, amnesiac, "amnesiac")
        # Validate target is a dead player
        if target and target not in dead_names:
            target = None

    if target:
        # Find the dead player and their role
        target_player = ctx.get_player_by_name(target)
        if target_player and target_player.role:
            # Create a new instance of their role
            role_class = ROLE_CLASSES.get(target_player.role.name)
            if role_class:
                new_role = role_class()
                old_role_name = target_player.role.name

                # Convert amnesiac to the new role
                amnesiac.convert_to_role(new_role, f"Remembered {target}", ctx.day_number)

                ctx.add_event("role_action",
                    f"You have remembered {target}'s role. You are now a {old_role_name}!",
                    amnesiac_visibility, player=amnesiac.name, priority=8)

                # Optionally announce publicly
                rules = getattr(ctx.game_state, 'rules', None) or DEFAULT_RULES
                if rules.amnesiac_announce_remember:
                    ctx.add_event("system",
                        f"The Amnesiac has remembered who they were!",
                        "all", priority=9)
    else:
        ctx.add_event("role_action", f"Amnesiac {amnesiac.name} chooses not to remember anyone tonight.",
                     amnesiac_visibility, player=amnesiac.name, priority=7)

    return StepResult(next_step="amnesiac_act", next_index=index + 1)


# =============================================================================
# MEDIUM HANDLERS
# =============================================================================

def execute_medium_question(ctx: StepContext, medium, dead_names: list) -> tuple:
    """Execute medium's selection of dead player and question."""
    from llm.prompts import build_role_action_prompt

    alive_names = [p.name for p in ctx.get_alive_players()]
    prompt = build_role_action_prompt(ctx.game_state, medium, "medium", alive_names, "")

    # Custom schema for medium - select target and ask question
    schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": dead_names + ["ABSTAIN"],
                "description": "The dead player to contact"
            },
            "question": {
                "type": "string",
                "description": "A yes/no question to ask the dead player"
            }
        },
        "required": ["target", "question"],
        "additionalProperties": False
    }

    try:
        response = call_llm(
            medium, ctx.llm_client, [{"role": "user", "content": prompt}],
            "medium_action", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "medium_action", "schema": schema}},
            temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        import json
        data = json.loads(response)
        target = data.get("target")
        question = data.get("question", "")

        if target == "ABSTAIN" or target not in dead_names:
            return None, None

        return target, question[:500]  # Limit question length
    except Exception as e:
        logging.error(f"Error executing medium action for {medium.name}: {e}", exc_info=True)
        return None, None


def execute_dead_player_response(ctx: StepContext, dead_player, question: str) -> str:
    """Get a dead player's response to the medium's question."""
    from llm.prompts import ContextBuilder

    # Build context for the dead player
    builder = ContextBuilder(ctx.game_state)
    context = builder.build_context(dead_player)

    prompt = f"""{context['game_rules']}

{context['game_log']}

{context['private_info']}

=== SEANCE ===

You are dead, but a Medium is contacting you from beyond the grave.
They have asked you a YES or NO question.

QUESTION: {question}

You must respond with ONLY one of these three options:
- "yes" - if you believe the answer is yes
- "no" - if you believe the answer is no
- "unknown" - if you don't know or the question cannot be answered with yes/no

Consider what you know from your time alive and any information you gathered.
Remember your goal was to help your team win, even from beyond the grave.

Respond with just the single word: yes, no, or unknown."""

    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "enum": ["yes", "no", "unknown"]
            }
        },
        "required": ["answer"],
        "additionalProperties": False
    }

    try:
        response = call_llm(
            dead_player, ctx.llm_client, [{"role": "user", "content": prompt}],
            "seance_response", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "seance_response", "schema": schema}},
            temperature=0.3, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        import json
        data = json.loads(response)
        return data.get("answer", "unknown")
    except Exception as e:
        logging.error(f"Error getting seance response from {dead_player.name}: {e}", exc_info=True)
        return "unknown"


@register_handler("medium_discuss")
def handle_medium_discuss(ctx: StepContext) -> StepResult:
    """Medium thinks through options. Skips discussion for human players."""
    medium_players = [p for p in ctx.get_players_by_role("Medium") if p.alive]
    index = ctx.step_index

    if not medium_players:
        return StepResult(next_step="amnesiac_discuss", next_index=0)

    if index >= len(medium_players):
        return StepResult(next_step="medium_act", next_index=0)

    medium = medium_players[index]
    medium_visibility = [medium.name]

    if index == 0:
        all_medium_names = [p.name for p in medium_players]
        ctx.add_event("system", "Medium night phase begins.", all_medium_names)

    # Skip discussion for human players
    if not medium.is_human:
        discussion = execute_role_discussion(ctx, medium, "medium")
        ctx.add_event("role_action", f"[Medium Discussion] {medium.name}: {discussion}",
                      medium_visibility, player=medium.name, priority=6)

    return StepResult(next_step="medium_discuss", next_index=index + 1)


@register_handler("medium_act")
def handle_medium_act(ctx: StepContext) -> StepResult:
    """Medium contacts a dead player and asks a question."""
    medium_players = [p for p in ctx.get_players_by_role("Medium") if p.alive]
    index = ctx.step_index

    if index >= len(medium_players):
        if medium_players:
            all_medium_names = [p.name for p in medium_players]
            ctx.add_event("system", "Medium night phase ends.", all_medium_names)
        return StepResult(next_step="amnesiac_discuss", next_index=0)

    medium = medium_players[index]
    medium_visibility = [medium.name]

    # Get dead players as options
    dead_players = [p for p in ctx.game_state.players if not p.alive]
    dead_names = [p.name for p in dead_players]

    if not dead_names:
        ctx.add_event("role_action", f"Medium {medium.name} has no spirits to contact yet.",
                     medium_visibility, player=medium.name, priority=7)
        return StepResult(next_step="medium_act", next_index=index + 1)

    target = None
    question = None

    # Check if medium is human
    if medium.is_human:
        # First get target selection
        human_input = wait_for_human_input(ctx, "role_action",
            {"options": dead_names, "label": "Contact a dead player (or Pass)"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in dead_names:
                target = None

        # If target selected, get the question
        if target:
            question_input = wait_for_human_input(ctx, "discussion",
                {"label": "Ask a yes/no question"})
            if question_input and question_input.get("type") == "discussion":
                question = question_input.get("message", "").strip()[:500]
    else:
        target, question = execute_medium_question(ctx, medium, dead_names)

    if target and question:
        # Get the dead player's response
        dead_player = ctx.get_player_by_name(target)

        if dead_player:
            if dead_player.is_human:
                # Human dead player responds
                response_input = wait_for_human_input(ctx, "role_action",
                    {"options": ["yes", "no", "unknown"],
                     "label": f"Seance question from Medium: {question}"})
                if response_input and response_input.get("type") == "role_action":
                    answer = response_input.get("target", "unknown")
                    if answer not in ["yes", "no", "unknown"]:
                        answer = "unknown"
                else:
                    answer = "unknown"
            else:
                # AI dead player responds
                answer = execute_dead_player_response(ctx, dead_player, question)

            # Record the seance
            medium.role.seance_history.append((target, question, answer))

            ctx.add_event("role_action",
                f"Medium {medium.name} contacts {target} and asks: \"{question}\"",
                medium_visibility, player=medium.name, priority=7)
            ctx.add_event("role_action",
                f"The spirit of {target} responds: {answer.upper()}",
                medium_visibility, player=medium.name, priority=8)
    else:
        ctx.add_event("role_action", f"Medium {medium.name} does not contact anyone tonight.",
                     medium_visibility, player=medium.name, priority=7)

    return StepResult(next_step="medium_act", next_index=index + 1)


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
