"""
Night phase step handlers.

All handlers for night-time actions: mafia discussion/vote, doctor, sheriff, vigilante.
"""

import logging
import random
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
    build_mafia_select_killer_prompt,
    build_mason_discussion_prompt,
    build_role_discussion_prompt,
    build_role_action_prompt,
    build_seance_response_prompt,
)


# =============================================================================
# VISIBILITY HELPERS
# =============================================================================

def get_mafia_visibility(game_state: GameState) -> List[str]:
    """Get list of mafia player names for event visibility.

    Includes all mafia team members: Mafia, Godfather, Consort, and Consigliere.
    All mafia know each other's identities.
    """
    return [p.name for p in game_state.players
            if p.role and p.role.name in ("Mafia", "Godfather", "Consort", "Consigliere")]


def get_mafia_discussion_visibility(game_state: GameState) -> List[str]:
    """Get list of mafia player names who participate in night discussions.

    Excludes unconverted Consigliere (they don't join mafia meetings until converted).
    """
    result = []
    for p in game_state.players:
        if not p.role:
            continue
        # Regular mafia roles participate
        if p.role.name in ("Mafia", "Godfather", "Consort"):
            result.append(p.name)
        # Consigliere only participates if converted
        elif p.role.name == "Consigliere" and p.role.has_converted:
            result.append(p.name)
    return result


def get_mason_visibility(game_state: GameState) -> List[str]:
    """Get list of mason player names for event visibility."""
    return [p.name for p in game_state.players if p.role and p.role.name == "Mason"]


def should_write_night_scratchpad(player) -> bool:
    """Determine if AI player should write scratchpad at night start.

    Human players don't write scratchpad notes.
    Roles with night actions get scratchpads to plan their decisions.
    """
    if not player.alive:
        return False
    if player.is_human:
        return False
    role_name = player.role.name if player.role else None
    return role_name in [
        "Doctor", "Sheriff", "Vigilante", "Mafia", "Godfather",
        "Escort", "Tracker", "Medium", "Amnesiac", "Consort", "Consigliere"
    ]


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
    """Resolve night actions and apply kills simultaneously.

    This function is called after all role choices are submitted and after
    medium/amnesiac resolution (which require LLM calls).

    It handles:
    - Doctor protection
    - Grandma immunity
    - Executioner target death conversion
    - Simultaneous kill resolution
    - Tracker and sheriff result delivery

    The resolution follows this order:
    1. Build blocked_players set (escorts are immune to blocks)
    2. Build effective_protected set from unblocked doctors
    3. Build canonical visits map from all night actions
    4. Process tracker results (before kills, so they see visits even if they die)
    5. Process sheriff investigation results (before kills, respects roleblock)
    6. Collect pending kills (checking protection and grandma immunity)
    7. Apply all kills simultaneously
    8. Handle executioner conversion if target died
    9. Notify doctors if they saved someone (optional rule)
    """
    rules = getattr(game_state, 'rules', None) or DEFAULT_RULES

    # =============================================================================
    # PHASE 1: Build blocked_players set
    # Escorts and Consorts are immune to roleblocks by design (avoids blocking chains/deadlocks)
    # =============================================================================
    blocked_players = set(game_state.phase_data.get("blocked_players", []))

    # Remove escorts and consorts from blocked set - they are immune to roleblocks
    for p in game_state.players:
        if p.role and p.role.name in ("Escort", "Consort") and p.name in blocked_players:
            blocked_players.discard(p.name)

    # =============================================================================
    # PHASE 2: Build effective_protected set from unblocked doctors
    # Single authoritative source for protection checks
    # =============================================================================
    effective_protected = set()
    doctor_protections = {}  # Maps protected_player -> list of doctor_names (for save notifications)

    for p in game_state.players:
        if p.role and p.role.name == "Doctor" and p.name not in blocked_players:
            if hasattr(p.role, 'last_protected') and p.role.last_protected:
                effective_protected.add(p.role.last_protected)
                if p.role.last_protected not in doctor_protections:
                    doctor_protections[p.role.last_protected] = []
                doctor_protections[p.role.last_protected].append(p.name)

    # =============================================================================
    # PHASE 3: Build canonical visits map from resolved night actions
    # All visit assignments happen in this single pass
    # =============================================================================
    visits = {}  # player_name -> target_name

    # Escort visits (escorts always visit their target)
    for p in game_state.players:
        if p.role and p.role.name == "Escort" and hasattr(p.role, 'block_history') and p.role.block_history:
            visits[p.name] = p.role.block_history[-1]

    # Consort visits (consorts always visit their target)
    for p in game_state.players:
        if p.role and p.role.name == "Consort" and hasattr(p.role, 'block_history') and p.role.block_history:
            visits[p.name] = p.role.block_history[-1]

    # Doctor visits (only if not blocked)
    for p in game_state.players:
        if p.role and p.role.name == "Doctor" and p.name not in blocked_players:
            if hasattr(p.role, 'last_protected') and p.role.last_protected:
                visits[p.name] = p.role.last_protected

    # Mafia kill visit (designated killer, only if not blocked)
    mafia_target = game_state.phase_data.get("mafia_kill_target")
    mafia_killer = game_state.phase_data.get("designated_killer")

    if mafia_target and not mafia_killer:
        # Find any alive mafia who can perform kills
        # Includes: Mafia, Godfather, Consort, and converted Consigliere (role.name becomes "Mafia")
        # Excludes: unconverted Consigliere (they don't participate in mafia actions)
        alive_mafia = [p for p in game_state.players
                       if p.alive and p.role and p.role.name in ("Mafia", "Godfather", "Consort")]
        if alive_mafia:
            mafia_killer = random.choice(alive_mafia).name

    if mafia_target and mafia_killer and mafia_killer not in blocked_players:
        visits[mafia_killer] = mafia_target

    # Vigilante visits (only if not blocked)
    vigilante_kills = game_state.phase_data.get("vigilante_kills", [])
    for vig_data in vigilante_kills:
        vig_name = vig_data.get("vigilante")
        vig_target = vig_data.get("target")
        if vig_target and vig_name not in blocked_players:
            visits[vig_name] = vig_target

    # Tracker visits (only if not blocked)
    tracker_targets = game_state.phase_data.get("tracker_targets", [])
    for track_data in tracker_targets:
        tracker_name = track_data["tracker"]
        if tracker_name not in blocked_players:
            visits[tracker_name] = track_data["target"]

    # Sheriff visits (only if not blocked)
    sheriff_targets = game_state.phase_data.get("sheriff_targets", [])
    for sheriff_data in sheriff_targets:
        sheriff_name = sheriff_data["sheriff"]
        if sheriff_name not in blocked_players:
            visits[sheriff_name] = sheriff_data["target"]

    # =============================================================================
    # PHASE 4: Process tracker results (before kills, so tracker sees visits even if they die)
    # =============================================================================
    for track_data in tracker_targets:
        tracker_name = track_data["tracker"]
        tracked_player = track_data["target"]
        tracker_player = game_state.get_player_by_name(tracker_name)

        if tracker_name in blocked_players:
            if tracker_player and tracker_player.role:
                tracker_player.role.tracking_results.append((tracked_player, None))
            game_state.add_event("role_action",
                "You were blocked and could not track anyone last night.",
                [tracker_name], player=tracker_name, priority=8,
                metadata={"blocked": True})
            continue

        visited = visits.get(tracked_player)

        if tracker_player and tracker_player.role:
            tracker_player.role.tracking_results.append((tracked_player, visited))

        if visited:
            game_state.add_event("role_action",
                f"Your target {tracked_player} visited {visited} last night.",
                [tracker_name], player=tracker_name, priority=8,
                metadata={"tracked": tracked_player, "visited": visited})
        else:
            game_state.add_event("role_action",
                f"Your target {tracked_player} did not visit anyone last night.",
                [tracker_name], player=tracker_name, priority=8,
                metadata={"tracked": tracked_player, "visited": None})

    # =============================================================================
    # PHASE 5: Process sheriff investigation results (before kills, so sheriff gets results even if they die)
    # Sheriff results are resolved here so escort/roleblock can prevent investigation.
    # =============================================================================
    sheriff_targets = game_state.phase_data.get("sheriff_targets", [])

    # Track investigations this night for multi-sheriff immunity handling
    investigated_this_night = set()

    for sheriff_data in sheriff_targets:
        sheriff_name = sheriff_data["sheriff"]
        investigated_target = sheriff_data["target"]
        sheriff_player = game_state.get_player_by_name(sheriff_name)

        if sheriff_name in blocked_players:
            game_state.add_event("role_action",
                "You were blocked and could not investigate anyone last night.",
                [sheriff_name], player=sheriff_name, priority=8,
                metadata={"blocked": True})
            continue

        target_player = game_state.get_player_by_name(investigated_target)
        if not target_player:
            continue

        # Use investigation helper that handles Godfather/Miller special cases
        result, ability_triggered = get_investigation_result(
            rules, target_player, game_state
        )

        # Consume immunity/false-positive only once per night (even with multiple sheriffs)
        if ability_triggered and investigated_target not in investigated_this_night:
            investigated_this_night.add(investigated_target)
            if target_player.role.name == "Godfather":
                target_player.role.investigation_immunity_used = True
            elif target_player.role.name == "Miller":
                target_player.role.false_positive_used = True

        # Record result in sheriff's investigations
        if sheriff_player and sheriff_player.role:
            sheriff_player.role.investigations.append((investigated_target, result))

        # Emit result event
        game_state.add_event("role_action",
            f"{investigated_target} is {result.upper()}!",
            [sheriff_name], player=sheriff_name, priority=8,
            metadata={"target": investigated_target, "result": result})

    # =============================================================================
    # PHASE 6: Collect pending kills with centralized immunity checks
    # =============================================================================
    grandma_names = set(p.name for p in game_state.players
                        if p.alive and p.role and p.role.name == "Grandma")

    def is_immune_to_night_kill(target_name: str) -> bool:
        """Centralized check for night kill immunity (currently only Grandma)."""
        target = game_state.get_player_by_name(target_name)
        return target and target.role and target.role.name == "Grandma"

    pending_kills = []
    pending_names = set()
    protected_from_kill = {}  # Maps target -> kill_source for doctor save notifications

    # Grandma visitors (only count actual visits - blocked players don't visit)
    grandma_visitors = []
    grandmas_who_fired = set()
    for visitor, visited in visits.items():
        if visited in grandma_names:
            grandma_visitors.append((visitor, visited))
            grandmas_who_fired.add(visited)

    if rules.grandma_knows_shotgun_fired:
        for grandma_name in grandmas_who_fired:
            game_state.add_event("role_action",
                "Someone visited you last night. You heard your shotgun go off.",
                [grandma_name], player=grandma_name, priority=8)

    # Mafia kill
    if mafia_target and mafia_killer and mafia_killer not in blocked_players:
        target_player = game_state.get_player_by_name(mafia_target)
        if target_player and target_player.alive:
            if mafia_target in effective_protected:
                protected_from_kill[mafia_target] = "mafia"
            elif not is_immune_to_night_kill(mafia_target):
                pending_kills.append(mafia_target)
                pending_names.add(mafia_target)

    # Vigilante kills
    for vig_data in vigilante_kills:
        vig_name = vig_data.get("vigilante")
        vig_target = vig_data.get("target")
        if not vig_target or vig_name in blocked_players:
            continue
        if vig_target in pending_names:
            continue  # Already being killed

        target_player = game_state.get_player_by_name(vig_target)
        if target_player and target_player.alive:
            if vig_target in effective_protected:
                protected_from_kill[vig_target] = "vigilante"
            elif not is_immune_to_night_kill(vig_target):
                pending_kills.append(vig_target)
                pending_names.add(vig_target)

    # Grandma kills visitors
    for visitor, grandma_name in grandma_visitors:
        if visitor in pending_names:
            continue  # Already being killed

        visitor_player = game_state.get_player_by_name(visitor)
        if visitor_player and visitor_player.alive:
            if visitor in effective_protected:
                protected_from_kill[visitor] = "grandma"
            elif not is_immune_to_night_kill(visitor):
                pending_kills.append(visitor)
                pending_names.add(visitor)

    # =============================================================================
    # PHASE 7: Apply all kills simultaneously
    # =============================================================================
    killed_names = set()
    for target_name in pending_kills:
        target_player = game_state.get_player_by_name(target_name)
        target_player.alive = False
        killed_names.add(target_name)
        # Public death message - no kill reason exposed
        game_state.add_event("death",
            f"{target_name} has been found dead, killed during the night!",
            "all", metadata={"player": target_name})

    if not pending_kills:
        game_state.add_event("system", "Nobody was killed last night.", "all")

    # =============================================================================
    # PHASE 8: Handle executioner conversion if target died
    # Note: This handles night kills only. Lynch deaths are handled in day.py voting_resolve.
    # If a death can occur outside both locations, this logic must be moved to a central
    # death handler (e.g., GameState.kill_player or a post-death hook).
    # =============================================================================
    if killed_names:
        from ..roles import ROLE_CLASSES
        fallback_role_name = rules.executioner_becomes_on_target_death

        for p in game_state.players:
            if p.alive and p.role and p.role.name == "Executioner":
                if p.role.target in killed_names:
                    new_role_class = ROLE_CLASSES.get(fallback_role_name)
                    if new_role_class:
                        old_target = p.role.target
                        p.convert_to_role(new_role_class(), f"Target {old_target} died", game_state.day_number)
                        game_state.add_event("role_action",
                            f"Your target {old_target} has died. You are now a {fallback_role_name}.",
                            [p.name], player=p.name, priority=9)

    # =============================================================================
    # PHASE 9: Notify doctors if they saved someone (optional rule)
    # Multiple doctors protecting the same target all get notified
    # =============================================================================
    if rules.doctor_knows_if_saved and protected_from_kill:
        for protected_target, kill_source in protected_from_kill.items():
            doctor_names = doctor_protections.get(protected_target, [])
            for doctor_name in doctor_names:
                game_state.add_event("role_action",
                    "Your protection saved someone's life last night!",
                    [doctor_name], player=doctor_name, priority=9)


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

    return StepResult(next_step="consigliere_convert", next_index=0)


# =============================================================================
# CONSIGLIERE CONVERSION HANDLER
# =============================================================================

@register_handler("consigliere_convert")
def handle_consigliere_convert(ctx: StepContext) -> StepResult:
    """Consigliere may choose to convert to regular Mafia before mafia discussion."""
    # Find unconverted Consigliere players
    consigliere_players = [p for p in ctx.get_players_by_role("Consigliere")
                           if p.alive and not p.role.has_converted]

    if not consigliere_players:
        return StepResult(next_step="mafia_discussion", next_index=0)

    index = ctx.step_index
    if index >= len(consigliere_players):
        return StepResult(next_step="mafia_discussion", next_index=0)

    consigliere = consigliere_players[index]
    consigliere_visibility = [consigliere.name]
    mafia_visibility = get_mafia_visibility(ctx.game_state)

    if index == 0:
        all_consigliere_names = [p.name for p in consigliere_players]
        ctx.add_event("system", "Consigliere conversion phase begins.", all_consigliere_names)

    convert = False

    # Check if consigliere is human
    if consigliere.is_human:
        human_input = wait_for_human_input(ctx, "role_action",
            {"options": ["Convert to Mafia", "Stay Undercover"],
             "label": "Convert to regular Mafia? (Permanent, irreversible)"})

        if human_input and human_input.get("type") == "role_action":
            choice = human_input.get("target")
            convert = (choice == "Convert to Mafia")
    else:
        # AI decides whether to convert
        convert = execute_consigliere_conversion_decision(ctx, consigliere)

    if convert:
        # Convert to regular Mafia
        from ..roles import ROLE_CLASSES
        new_role = ROLE_CLASSES["Mafia"]()
        consigliere.convert_to_role(new_role, "Converted from Consigliere", ctx.day_number)

        ctx.add_event("role_action",
            "You have converted to a regular Mafia member. You now participate in mafia discussions but are no longer immune to investigation.",
            consigliere_visibility, player=consigliere.name, priority=8)

        # Notify other mafia (but not the public)
        other_mafia = [n for n in mafia_visibility if n != consigliere.name]
        if other_mafia:
            ctx.add_event("mafia_chat",
                f"[Mafia Notice] {consigliere.name} (Consigliere) has converted and will now join your discussions.",
                other_mafia, priority=7)
    else:
        ctx.add_event("role_action",
            "You remain undercover. You will not participate in tonight's mafia discussion.",
            consigliere_visibility, player=consigliere.name, priority=8)

    return StepResult(next_step="consigliere_convert", next_index=index + 1)


def execute_consigliere_conversion_decision(ctx: StepContext, consigliere) -> bool:
    """AI Consigliere decides whether to convert.

    Returns True if they want to convert, False to stay undercover.
    """
    from llm.prompts import build_consigliere_convert_prompt

    prompt = build_consigliere_convert_prompt(ctx.game_state, consigliere)
    messages = [{"role": "user", "content": prompt}]

    # Simple yes/no schema
    schema = {
        "type": "object",
        "properties": {
            "convert": {
                "type": "boolean",
                "description": "True to convert to regular Mafia, False to stay undercover"
            },
            "reasoning": {
                "type": "string",
                "description": "Brief reasoning for the decision"
            }
        },
        "required": ["convert"],
        "additionalProperties": False
    }

    try:
        response = call_llm(
            consigliere, ctx.llm_client, messages, "consigliere_convert", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "consigliere_convert", "schema": schema}},
            temperature=0.5, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        # Extract from structured_output or fallback to parsing content
        if "structured_output" in response:
            data = response["structured_output"]
        else:
            import json
            content = response.get("content", "")
            idx = content.find("{")
            if idx >= 0:
                data = json.loads(content[idx:content.rfind("}")+1])
            else:
                return False

        return data.get("convert", False)
    except Exception as e:
        logging.warning(f"Error in consigliere conversion decision for {consigliere.name}: {e}")
        return False  # Default to staying undercover


# =============================================================================
# MAFIA HANDLERS
# =============================================================================

def get_mafia_discussion_participants(ctx: StepContext) -> list:
    """Get list of mafia players who participate in night discussions.

    Includes Mafia, Godfather, Consort, and converted Consigliere.
    Excludes unconverted Consigliere.
    """
    participants = []
    participants.extend(ctx.get_players_by_role("Mafia"))
    participants.extend(ctx.get_players_by_role("Godfather"))
    participants.extend(ctx.get_players_by_role("Consort"))
    # Only include converted Consigliere
    for p in ctx.get_players_by_role("Consigliere"):
        if p.role.has_converted:
            participants.append(p)
    return participants


@register_handler("mafia_discussion")
def handle_mafia_discussion(ctx: StepContext) -> StepResult:
    """Mafia members discuss who to kill. Waits for human input if mafia member is human."""
    mafia_players = get_mafia_discussion_participants(ctx)
    mafia_visibility = get_mafia_discussion_visibility(ctx.game_state)
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
    mafia_players = get_mafia_discussion_participants(ctx)
    mafia_visibility = get_mafia_discussion_visibility(ctx.game_state)
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

    # If select_killer rule is enabled and there's a target, go to killer selection
    rules = ctx.game_state.rules
    if rules.mafia_select_killer and target and len(mafia_players) > 1:
        ctx.add_event("system", f"Mafia has chosen {target} as the target. Now selecting who performs the kill.", mafia_visibility)
        return StepResult(next_step="mafia_select_killer", next_index=0)

    # Otherwise, use implicit killer (first voter) and end mafia phase
    if target:
        ctx.add_event("system", f"Mafia has chosen to kill {target}.", mafia_visibility)
    ctx.add_event("system", "Mafia night actions end.", mafia_visibility)

    return StepResult(next_step="mason_discussion", next_index=0)


@register_handler("mafia_select_killer")
def handle_mafia_select_killer(ctx: StepContext) -> StepResult:
    """Mafia selects which member performs the kill. All mafia vote, majority wins."""
    mafia_players = get_mafia_discussion_participants(ctx)
    mafia_visibility = get_mafia_discussion_visibility(ctx.game_state)
    target = ctx.phase_data.get("mafia_kill_target")
    discussion_messages = ctx.phase_data.get("mafia_discussion_messages", [])

    # Get list of alive mafia who can perform the kill
    alive_mafia = [m for m in mafia_players if m.alive]
    mafia_names = [m.name for m in alive_mafia]

    if len(alive_mafia) <= 1:
        # Only one mafia, they're automatically the killer
        if alive_mafia:
            ctx.phase_data["designated_killer"] = alive_mafia[0].name
            ctx.add_event("system", f"{alive_mafia[0].name} will perform the kill.", mafia_visibility)
        ctx.add_event("system", "Mafia night actions end.", mafia_visibility)
        return StepResult(next_step="mason_discussion", next_index=0)

    results = []

    # Check if any mafia member is human - they vote first
    human_mafia = None
    for mafia in alive_mafia:
        if mafia.is_human:
            human_mafia = mafia
            break

    if human_mafia:
        # Human mafia votes first (their vote counts the same as others)
        human_input = wait_for_human_input(
            ctx, "role_action",
            {"options": mafia_names, "label": f"Nominate who performs the kill on {target}"}
        )

        choice = None
        if human_input and human_input.get("type") == "role_action":
            choice = human_input.get("target")
            if choice and choice not in mafia_names:
                choice = None

        if not choice:
            choice = human_mafia.name  # Default to self if no valid selection

        ctx.add_event("mafia_chat", f"[Mafia] {human_mafia.name} nominates {choice} to perform the kill.",
                      mafia_visibility, player=human_mafia.name, priority=7)
        results.append({"player": human_mafia.name, "choice": choice})

    # AI mafia vote in parallel
    ai_mafia = [m for m in alive_mafia if not m.is_human]

    def vote_for_killer(mafia):
        prompt = build_mafia_select_killer_prompt(
            ctx.game_state, mafia, target, mafia_names,
            discussion_messages, results  # Pass previous votes (human's if any)
        )
        messages = [{"role": "user", "content": prompt}]
        killer_schema = build_target_schema(mafia_names, allow_abstain=False)

        response = call_llm(
            mafia, ctx.llm_client, messages, "select_killer", ctx.game_state,
            response_format={"type": "json_schema", "json_schema": {"name": "select_killer", "schema": killer_schema}},
            temperature=0.5, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
        )

        choice = parse_target(response)
        if choice not in mafia_names:
            choice = mafia.name  # Default to self if invalid

        ctx.add_event("mafia_chat", f"[Mafia] {mafia.name} nominates {choice} to perform the kill.",
                      mafia_visibility, player=mafia.name, priority=7)

        return {"player": mafia.name, "choice": choice}

    if ai_mafia:
        ai_results = execute_parallel(ai_mafia, vote_for_killer, ctx)
        results.extend(ai_results)

    # Tally votes
    killer_votes = {}
    for result in results:
        choice = result["choice"]
        killer_votes[choice] = killer_votes.get(choice, 0) + 1

    # Winner is the one with most votes (ties go to first in vote order)
    if killer_votes:
        selected_killer = max(killer_votes.items(), key=lambda x: x[1])[0]
    else:
        selected_killer = alive_mafia[0].name

    ctx.phase_data["designated_killer"] = selected_killer
    ctx.add_event("system", f"{selected_killer} will perform the kill on {target}.", mafia_visibility)
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
        return StepResult(next_step="consort_discuss", next_index=0)

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
# CONSORT HANDLERS
# =============================================================================

@register_handler("consort_discuss")
def handle_consort_discuss(ctx: StepContext) -> StepResult:
    """Consort thinks through blocking options. Skips discussion for human players."""
    consort_players = [p for p in ctx.get_players_by_role("Consort") if p.alive]
    index = ctx.step_index

    if not consort_players:
        return StepResult(next_step="doctor_discuss", next_index=0)

    if index >= len(consort_players):
        return StepResult(next_step="consort_act", next_index=0)

    consort = consort_players[index]
    consort_visibility = [consort.name]

    if index == 0:
        all_consort_names = [p.name for p in consort_players]
        ctx.add_event("system", "Consort night phase begins.", all_consort_names)

    # Skip discussion for human players
    if not consort.is_human:
        discussion = execute_role_discussion(ctx, consort, "consort")
        ctx.add_event("role_action", f"[Consort Discussion] {consort.name}: {discussion}",
                      consort_visibility, player=consort.name, priority=6)

    return StepResult(next_step="consort_discuss", next_index=index + 1)


@register_handler("consort_act")
def handle_consort_act(ctx: StepContext) -> StepResult:
    """Consort chooses who to block. Waits for human input if consort is human."""
    consort_players = [p for p in ctx.get_players_by_role("Consort") if p.alive]
    index = ctx.step_index

    if index >= len(consort_players):
        if consort_players:
            all_consort_names = [p.name for p in consort_players]
            ctx.add_event("system", "Consort night phase ends.", all_consort_names)
        return StepResult(next_step="doctor_discuss", next_index=0)

    consort = consort_players[index]
    consort_visibility = [consort.name]
    alive_names = [p.name for p in ctx.get_alive_players()]

    target = None

    # Check if consort is human
    if consort.is_human:
        human_input = wait_for_human_input(ctx, "role_action", {"options": alive_names, "label": "Block Someone"})

        if human_input and human_input.get("type") == "role_action":
            target = human_input.get("target")
            if target == "ABSTAIN":
                target = None
            elif target and target not in alive_names:
                target = None
    else:
        target = execute_role_action(ctx, consort, "consort")

    if target:
        # Store the blocked target
        if "blocked_players" not in ctx.phase_data:
            ctx.phase_data["blocked_players"] = []
        ctx.phase_data["blocked_players"].append(target)

        # Record in consort's history
        consort.role.block_history.append(target)

        ctx.add_event("role_action", f"Consort {consort.name} visits {target} tonight.",
                     consort_visibility, player=consort.name, priority=7)

    return StepResult(next_step="consort_act", next_index=index + 1)


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
    """Sheriff chooses investigation target. Result determined at night_resolve."""
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
        # Store the investigation target - result will be determined at night_resolve
        if "sheriff_targets" not in ctx.phase_data:
            ctx.phase_data["sheriff_targets"] = []
        ctx.phase_data["sheriff_targets"].append({
            "sheriff": sheriff.name,
            "target": target
        })

        ctx.add_event("role_action", f"Sheriff {sheriff.name} investigates {target} tonight.",
                     sheriff_visibility, player=sheriff.name, priority=7)

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
        # Initialize storage for amnesiac discussions
        if "amnesiac_discussions" not in ctx.phase_data:
            ctx.phase_data["amnesiac_discussions"] = {}

    # Skip discussion for human players
    if not amnesiac.is_human:
        discussion = execute_role_discussion(ctx, amnesiac, "amnesiac")
        ctx.add_event("role_action", f"[Amnesiac Discussion] {amnesiac.name}: {discussion}",
                      amnesiac_visibility, player=amnesiac.name, priority=6)
        # Store discussion for use in the action phase
        ctx.phase_data["amnesiac_discussions"][amnesiac.name] = discussion

    return StepResult(next_step="amnesiac_discuss", next_index=index + 1)


@register_handler("amnesiac_act")
def handle_amnesiac_act(ctx: StepContext) -> StepResult:
    """Amnesiac chooses a dead player to remember. Role change occurs at night_resolve."""
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
        target = execute_amnesiac_action(ctx, amnesiac, dead_names)

    if target:
        # Store the remember request - role change will occur at night_resolve
        if "amnesiac_remembers" not in ctx.phase_data:
            ctx.phase_data["amnesiac_remembers"] = []
        ctx.phase_data["amnesiac_remembers"].append({
            "amnesiac": amnesiac.name,
            "target": target
        })

        ctx.add_event("role_action", f"Amnesiac {amnesiac.name} focuses on remembering {target}'s identity.",
                     amnesiac_visibility, player=amnesiac.name, priority=7)
    else:
        ctx.add_event("role_action", f"Amnesiac {amnesiac.name} chooses not to remember anyone tonight.",
                     amnesiac_visibility, player=amnesiac.name, priority=7)

    return StepResult(next_step="amnesiac_act", next_index=index + 1)


def execute_amnesiac_action(ctx: StepContext, amnesiac, dead_names: list) -> str:
    """Execute amnesiac's selection of dead player to remember."""
    from llm.prompts import build_amnesiac_action_prompt
    from game.llm_caller import build_target_schema, parse_target

    # Get this amnesiac's discussion from the stored discussions
    discussions = ctx.phase_data.get("amnesiac_discussions", {})
    discussion = discussions.get(amnesiac.name, "")
    prompt = build_amnesiac_action_prompt(ctx.game_state, amnesiac, dead_names, discussion)

    # Build schema with dead players as options (plus ABSTAIN)
    target_schema = build_target_schema(dead_names, allow_abstain=True)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = call_llm(
                amnesiac, ctx.llm_client, [{"role": "user", "content": prompt}],
                "amnesiac_action", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "amnesiac_action", "schema": target_schema}},
                temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

            target = parse_target(response, allow_abstain=True)

            if target and target not in dead_names:
                raise ValueError(f"Invalid target: {target} not in dead players")

            return target
        except Exception as e:
            logging.warning(f"Amnesiac action attempt {attempt + 1}/{max_retries} failed for {amnesiac.name}: {e}")
            if attempt == max_retries - 1:
                logging.error(f"Error executing amnesiac action for {amnesiac.name}: {e}", exc_info=True)
                return None


# =============================================================================
# MEDIUM HANDLERS
# =============================================================================

def execute_medium_question(ctx: StepContext, medium, dead_names: list) -> tuple:
    """Execute medium's selection of dead player and question."""
    from llm.prompts import build_medium_action_prompt

    # Get this medium's discussion from the stored discussions
    discussions = ctx.phase_data.get("medium_discussions", {})
    discussion = discussions.get(medium.name, "")
    prompt = build_medium_action_prompt(ctx.game_state, medium, dead_names, discussion)

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

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = call_llm(
                medium, ctx.llm_client, [{"role": "user", "content": prompt}],
                "medium_action", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "medium_action", "schema": schema}},
                temperature=0.7, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

            # Extract from structured_output or fallback to parsing content
            if "structured_output" in response:
                data = response["structured_output"]
            else:
                import json
                content = response.get("content", "")
                idx = content.find("{")
                if idx >= 0:
                    data = json.loads(content[idx:content.rfind("}")+1])
                else:
                    raise ValueError("No JSON found in response content")

            target = data.get("target")
            question = data.get("question", "")

            if not target or not question:
                raise ValueError(f"Missing target or question in response: {data}")

            if target == "ABSTAIN" or target not in dead_names:
                return None, None

            return target, question[:500]  # Limit question length
        except Exception as e:
            logging.warning(f"Medium action attempt {attempt + 1}/{max_retries} failed for {medium.name}: {e}")
            if attempt == max_retries - 1:
                logging.error(f"Error executing medium action for {medium.name}: {e}", exc_info=True)
                return None, None


def execute_dead_player_response(ctx: StepContext, dead_player, question: str) -> str:
    """Get a dead player's response to the medium's question."""
    prompt = build_seance_response_prompt(ctx.game_state, dead_player, question)

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

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = call_llm(
                dead_player, ctx.llm_client, [{"role": "user", "content": prompt}],
                "seance_response", ctx.game_state,
                response_format={"type": "json_schema", "json_schema": {"name": "seance_response", "schema": schema}},
                temperature=0.3, cancel_event=ctx.cancel_event, emit_player_status=ctx.emit_player_status
            )

            # Extract from structured_output or fallback to parsing content
            if "structured_output" in response:
                data = response["structured_output"]
            else:
                import json
                content = response.get("content", "")
                idx = content.find("{")
                if idx >= 0:
                    data = json.loads(content[idx:content.rfind("}")+1])
                else:
                    raise ValueError("No JSON found in response content")

            answer = data.get("answer", "unknown")
            if answer not in ["yes", "no", "unknown"]:
                raise ValueError(f"Invalid answer: {answer}")
            return answer
        except Exception as e:
            logging.warning(f"Seance response attempt {attempt + 1}/{max_retries} failed for {dead_player.name}: {e}")
            if attempt == max_retries - 1:
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
        # Initialize storage for medium discussions
        if "medium_discussions" not in ctx.phase_data:
            ctx.phase_data["medium_discussions"] = {}

    # Skip discussion for human players
    if not medium.is_human:
        discussion = execute_role_discussion(ctx, medium, "medium")
        ctx.add_event("role_action", f"[Medium Discussion] {medium.name}: {discussion}",
                      medium_visibility, player=medium.name, priority=6)
        # Store discussion for use in the action phase
        ctx.phase_data["medium_discussions"][medium.name] = discussion

    return StepResult(next_step="medium_discuss", next_index=index + 1)


@register_handler("medium_act")
def handle_medium_act(ctx: StepContext) -> StepResult:
    """Medium chooses a dead player and question. Result determined at night_resolve."""
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
        # Store the seance request - result will be determined at night_resolve
        if "medium_seances" not in ctx.phase_data:
            ctx.phase_data["medium_seances"] = []
        ctx.phase_data["medium_seances"].append({
            "medium": medium.name,
            "target": target,
            "question": question
        })

        ctx.add_event("role_action", f"Medium {medium.name} attempts to contact {target} tonight.",
                     medium_visibility, player=medium.name, priority=7)
    else:
        ctx.add_event("role_action", f"Medium {medium.name} does not contact anyone tonight.",
                     medium_visibility, player=medium.name, priority=7)

    return StepResult(next_step="medium_act", next_index=index + 1)


# =============================================================================
# NIGHT RESOLVE
# =============================================================================

def resolve_medium_seances(ctx: StepContext, blocked_players: set):
    """Resolve medium seances after all night actions are submitted.

    Medium seances require LLM calls for dead player responses, so they
    must be handled in the context-aware resolve step.
    """
    medium_seances = ctx.phase_data.get("medium_seances", [])

    for seance_data in medium_seances:
        medium_name = seance_data["medium"]
        target = seance_data["target"]
        question = seance_data["question"]
        medium_player = ctx.get_player_by_name(medium_name)

        if medium_name in blocked_players:
            ctx.add_event("role_action",
                "You were blocked and could not contact the spirits last night.",
                [medium_name], player=medium_name, priority=8,
                metadata={"blocked": True})
            continue

        # Get the dead player's response
        dead_player = ctx.get_player_by_name(target)
        if not dead_player:
            continue

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
        if medium_player and medium_player.role:
            medium_player.role.seance_history.append((target, question, answer))

        ctx.add_event("role_action",
            f"You asked {target}: \"{question}\"",
            [medium_name], player=medium_name, priority=8)
        ctx.add_event("role_action",
            f"The spirit of {target} responds: {answer.upper()}",
            [medium_name], player=medium_name, priority=8)


def resolve_amnesiac_remembers(ctx: StepContext, blocked_players: set):
    """Resolve amnesiac role changes after all night actions are submitted."""
    from ..roles import ROLE_CLASSES

    amnesiac_remembers = ctx.phase_data.get("amnesiac_remembers", [])
    rules = getattr(ctx.game_state, 'rules', None) or DEFAULT_RULES

    for remember_data in amnesiac_remembers:
        amnesiac_name = remember_data["amnesiac"]
        target = remember_data["target"]
        amnesiac_player = ctx.get_player_by_name(amnesiac_name)

        if amnesiac_name in blocked_players:
            ctx.add_event("role_action",
                "You were blocked and could not remember your identity last night.",
                [amnesiac_name], player=amnesiac_name, priority=8,
                metadata={"blocked": True})
            continue

        # Find the dead player and their role
        target_player = ctx.get_player_by_name(target)
        if not target_player or not target_player.role:
            continue

        # Create a new instance of their role
        role_class = ROLE_CLASSES.get(target_player.role.name)
        if role_class and amnesiac_player:
            new_role = role_class()
            old_role_name = target_player.role.name

            # Convert amnesiac to the new role
            amnesiac_player.convert_to_role(new_role, f"Remembered {target}", ctx.day_number)

            ctx.add_event("role_action",
                f"You have remembered {target}'s role. You are now a {old_role_name}!",
                [amnesiac_name], player=amnesiac_name, priority=8)

            # Optionally announce publicly
            if rules.amnesiac_announce_remember:
                ctx.add_event("system",
                    f"The Amnesiac has remembered who they were!",
                    "all", priority=9)


@register_handler("night_resolve")
def handle_night_resolve(ctx: StepContext) -> StepResult:
    """Resolve all night actions and transition to day.

    Night resolution order:
    1. Compute blocked players (escorts are immune to blocks)
    2. Resolve medium seances (requires LLM calls for dead player responses)
    3. Resolve amnesiac remembering (role conversions)
    4. Call resolve_night_actions for tracker/sheriff results and kills
    """
    from ..win_conditions import check_win_conditions

    # Compute blocked players (same logic as in resolve_night_actions)
    # Needed here for medium/amnesiac resolution which requires ctx for LLM calls
    blocked_players = set(ctx.phase_data.get("blocked_players", []))

    # Remove escorts and consorts from blocked set - they are immune to roleblocks
    for p in ctx.game_state.players:
        if p.role and p.role.name in ("Escort", "Consort") and p.name in blocked_players:
            blocked_players.discard(p.name)

    # Resolve medium seances (requires LLM calls)
    resolve_medium_seances(ctx, blocked_players)

    # Resolve amnesiac remembering
    resolve_amnesiac_remembers(ctx, blocked_players)

    # Resolve everything else (tracker, sheriff, kills, etc.)
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
