"""Phase handlers for day and night phases."""

import json
import random
from datetime import datetime
from typing import List, Dict, Optional
from .game_state import GameState, Player
from .win_conditions import check_win_conditions
from llm.openrouter_client import OpenRouterClient
from llm.prompts import (
    build_night_prompt,
    build_day_discussion_prompt,
    build_day_discussion_priority_prompt,
    build_day_voting_prompt,
    build_mafia_vote_prompt,
    build_urgent_check_prompt,
)


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

DISCUSSION_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "integer"},
        "message": {"type": "string"},
        "accused": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Names of players you accused of being mafia"
        },
        "questioned": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Names of players you directly asked a question"
        }
    },
    "required": ["priority", "message"]
}

PRIORITY_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "integer"},
        "wants_to_speak": {"type": "boolean"}
    },
    "required": ["priority", "wants_to_speak"]
}

URGENT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "has_critical_info": {"type": "boolean"},
        "reason": {"type": "string"}
    },
    "required": ["has_critical_info"]
}


def handle_night_phase(game_state: GameState, llm_client: OpenRouterClient, game_id: str = None, emit_callback=None) -> Dict:
    """
    Handle a complete night phase.
    
    Args:
        game_state: Current game state
        llm_client: LLM client for making API calls
        game_id: Optional game ID for emitting real-time updates
        emit_callback: Optional callback function(game_id, game_state) to emit updates
    
    Returns:
        Dict with night action results
    """
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
        for mafia in mafia_players:
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
            response = llm_client.call_model(
                mafia.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "mafia_vote", "schema": ACTION_SCHEMA}},
                temperature=0.7
            )
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
                except:
                    pass
            
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
        response = llm_client.call_model(
            doctor.model,
            messages,
            response_format={"type": "json_schema", "json_schema": {"name": "doctor_action", "schema": ACTION_SCHEMA}},
            temperature=0.7
        )
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
            except:
                pass
        
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
        response = llm_client.call_model(
            sheriff.model,
            messages,
            response_format={"type": "json_schema", "json_schema": {"name": "sheriff_action", "schema": ACTION_SCHEMA}},
            temperature=0.7
        )
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
            except:
                pass
        
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
                vigilante.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "vigilante_action", "schema": ACTION_SCHEMA}},
                temperature=0.7
            )
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
                except:
                    pass
            
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


def handle_day_phase(game_state: GameState, llm_client: OpenRouterClient, game_id: str = None, emit_callback=None, emit_status_callback=None) -> Dict:
    """
    Handle a complete day phase.

    Args:
        game_state: Current game state
        llm_client: LLM client for making API calls
        game_id: Optional game ID for emitting real-time updates
        emit_callback: Optional callback function(game_id, game_state) to emit updates
        emit_status_callback: Optional callback for discussion status updates

    Returns:
        Dict with day phase results
    """
    game_state.phase = "day"
    game_state.day_number += 1
    game_state.add_event("phase_change", f"Day {game_state.day_number} begins.", "all")
    
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

    # 1. Discussion phase - real-time with queue-based priority system
    discussion_messages = []
    max_discussion_messages = 10  # Max total messages before cutoff

    # Queue tracking for accused/questioned players
    accused_queue = set()      # Players who were accused of being mafia
    questioned_queue = set()   # Players who were asked a direct question
    players_spoken_this_phase = set()  # Track who has spoken at all this phase

    # Helper to emit discussion status for UI visibility
    def emit_status(action: str, waiting_player: str = None, extra: dict = None):
        if emit_status_callback and game_id:
            status = {
                "action": action,
                "waiting_player": waiting_player,
                "accused_queue": list(accused_queue),
                "questioned_queue": list(questioned_queue),
                "message_count": len(discussion_messages),
                "max_messages": max_discussion_messages,
            }
            if extra:
                status.update(extra)
            emit_status_callback(game_id, status)

    # Helper function to get priority with queue boost
    def get_boosted_priority(player_name: str, base_priority: int) -> int:
        if player_name in accused_queue:
            return max(base_priority, 9)  # Accused players get priority 9+
        elif player_name in questioned_queue:
            return max(base_priority, 8)  # Questioned players get priority 8+
        return base_priority

    # Helper function to parse accused/questioned from response
    def extract_queue_updates(response_data: dict, speaker_name: str, alive_names: list):
        accused = []
        questioned = []

        if "structured_output" in response_data:
            accused = response_data["structured_output"].get("accused", [])
            questioned = response_data["structured_output"].get("questioned", [])
        else:
            try:
                content = response_data.get("content", "")
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start >= 0:
                    parsed = json.loads(content[json_start:json_end])
                    accused = parsed.get("accused", [])
                    questioned = parsed.get("questioned", [])
            except:
                pass

        # Validate names - only include alive players, not the speaker
        valid_accused = [n for n in accused if n in alive_names and n != speaker_name]
        valid_questioned = [n for n in questioned if n in alive_names and n != speaker_name]

        return valid_accused, valid_questioned

    # Helper function to check urgent escape hatch
    def check_urgent_escape_hatch(players_to_check: list) -> list:
        urgent_players = []
        for player in players_to_check:
            prompt = build_urgent_check_prompt(game_state, player)
            messages = [{"role": "user", "content": prompt}]

            try:
                player.last_llm_context = {
                    "messages": messages,
                    "timestamp": datetime.now().isoformat(),
                    "action_type": "urgent_check",
                    "phase": game_state.phase,
                    "day": game_state.day_number
                }
                response = llm_client.call_model(
                    player.model,
                    messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "urgent", "schema": URGENT_CHECK_SCHEMA}},
                    temperature=0.3,
                    max_tokens=100
                )
                player.last_llm_context["response"] = response

                has_critical = False
                if "structured_output" in response:
                    has_critical = response["structured_output"].get("has_critical_info", False)
                else:
                    try:
                        content = response.get("content", "")
                        json_start = content.find("{")
                        json_end = content.rfind("}") + 1
                        if json_start >= 0:
                            parsed = json.loads(content[json_start:json_end])
                            has_critical = parsed.get("has_critical_info", False)
                    except:
                        pass

                if has_critical:
                    urgent_players.append(player)
            except:
                continue

        return urgent_players

    alive_names = [p.name for p in alive_players]

    # Emit initial discussion status
    emit_status("discussion_start")

    # Main discussion loop
    while len(discussion_messages) < max_discussion_messages:
        # Phase 1: Get priorities from all players who haven't spoken recently
        emit_status("priority_polling")
        player_priorities = []

        for player in alive_players:
            # Allow players to speak multiple times, but not consecutively
            last_speaker = discussion_messages[-1]["player"] if discussion_messages else None
            if player.name == last_speaker:
                continue

            prompt = build_day_discussion_priority_prompt(
                game_state, player,
                accused_queue=accused_queue,
                questioned_queue=questioned_queue
            )
            messages = [{"role": "user", "content": prompt}]

            try:
                # Emit waiting status for this player
                emit_status("waiting_priority", waiting_player=player.name)

                player.last_llm_context = {
                    "messages": messages,
                    "timestamp": datetime.now().isoformat(),
                    "action_type": "discussion_priority",
                    "phase": game_state.phase,
                    "day": game_state.day_number
                }
                response = llm_client.call_model(
                    player.model,
                    messages,
                    response_format={"type": "json_schema", "json_schema": {"name": "priority", "schema": PRIORITY_ONLY_SCHEMA}},
                    temperature=0.7,
                    max_tokens=50
                )
                player.last_llm_context["response"] = response

                priority = 5
                wants_to_speak = True

                if "structured_output" in response:
                    priority = response["structured_output"].get("priority", 5)
                    wants_to_speak = response["structured_output"].get("wants_to_speak", True)
                else:
                    try:
                        content = response["content"]
                        json_start = content.find("{")
                        json_end = content.rfind("}") + 1
                        if json_start >= 0:
                            parsed = json.loads(content[json_start:json_end])
                            priority = parsed.get("priority", 5)
                            wants_to_speak = parsed.get("wants_to_speak", True)
                    except:
                        pass

                if wants_to_speak:
                    # Apply queue boost
                    boosted_priority = get_boosted_priority(player.name, priority)
                    player_priorities.append({
                        "player": player.name,
                        "priority": boosted_priority,
                        "base_priority": priority
                    })
            except Exception as e:
                continue

        # Phase 2: If no one wants to speak, check urgent escape hatch
        if not player_priorities:
            # Only check players who haven't spoken or are in a queue
            players_to_check = [
                p for p in alive_players
                if p.name not in players_spoken_this_phase or p.name in accused_queue or p.name in questioned_queue
            ]

            if players_to_check:
                emit_status("urgent_check", extra={"checking_players": [p.name for p in players_to_check]})
                urgent_players = check_urgent_escape_hatch(players_to_check)

                if urgent_players:
                    # Let all urgent players speak
                    for urgent_player in urgent_players:
                        if len(discussion_messages) >= max_discussion_messages:
                            break

                        prompt = build_day_discussion_prompt(game_state, urgent_player)
                        messages = [{"role": "user", "content": prompt}]

                        try:
                            urgent_player.last_llm_context = {
                                "messages": messages,
                                "timestamp": datetime.now().isoformat(),
                                "action_type": "discussion_message_urgent",
                                "phase": game_state.phase,
                                "day": game_state.day_number
                            }
                            response = llm_client.call_model(
                                urgent_player.model,
                                messages,
                                response_format={"type": "json_schema", "json_schema": {"name": "discussion", "schema": DISCUSSION_SCHEMA}},
                                temperature=0.8,
                                max_tokens=200
                            )
                            urgent_player.last_llm_context["response"] = response

                            message = ""
                            priority = 10

                            if "structured_output" in response:
                                message = response["structured_output"].get("message", "")
                                priority = response["structured_output"].get("priority", 10)
                            else:
                                try:
                                    content = response["content"]
                                    json_start = content.find("{")
                                    json_end = content.rfind("}") + 1
                                    if json_start >= 0:
                                        parsed = json.loads(content[json_start:json_end])
                                        message = parsed.get("message", "")
                                        priority = parsed.get("priority", 10)
                                except:
                                    message = response.get("content", "")[:200]

                            # Always mark as spoken and remove from queues
                            players_spoken_this_phase.add(urgent_player.name)
                            accused_queue.discard(urgent_player.name)
                            questioned_queue.discard(urgent_player.name)

                            if message:
                                game_state.add_event("discussion", message, "public", player=urgent_player.name, priority=priority)
                                discussion_messages.append({
                                    "player": urgent_player.name,
                                    "priority": priority,
                                    "message": message
                                })
                                day_results["discussion"].append({
                                    "player": urgent_player.name,
                                    "priority": priority,
                                    "message": message
                                })

                                # Extract queue updates
                                new_accused, new_questioned = extract_queue_updates(response, urgent_player.name, alive_names)
                                for name in new_accused:
                                    accused_queue.add(name)
                                for name in new_questioned:
                                    questioned_queue.add(name)

                                if emit_callback and game_id:
                                    emit_callback(game_id, game_state)
                        except:
                            # Even on exception, mark as attempted
                            players_spoken_this_phase.add(urgent_player.name)
                            accused_queue.discard(urgent_player.name)
                            questioned_queue.discard(urgent_player.name)
                            continue

                    # After urgent speakers, continue the loop
                    continue

            # No one wants to speak and no urgent info - end discussion
            break

        # Phase 3: Highest priority player speaks
        player_priorities.sort(key=lambda x: x["priority"], reverse=True)
        next_speaker_name = player_priorities[0]["player"]
        next_speaker = next((p for p in alive_players if p.name == next_speaker_name), None)

        if not next_speaker:
            break

        # Phase 4: Get the full message from the highest priority player
        emit_status("waiting_message", waiting_player=next_speaker.name, extra={"selected_priority": player_priorities[0]["priority"]})

        prompt = build_day_discussion_prompt(game_state, next_speaker)
        messages = [{"role": "user", "content": prompt}]

        try:
            next_speaker.last_llm_context = {
                "messages": messages,
                "timestamp": datetime.now().isoformat(),
                "action_type": "discussion_message",
                "phase": game_state.phase,
                "day": game_state.day_number
            }
            response = llm_client.call_model(
                next_speaker.model,
                messages,
                response_format={"type": "json_schema", "json_schema": {"name": "discussion", "schema": DISCUSSION_SCHEMA}},
                temperature=0.8,
                max_tokens=200
            )
            next_speaker.last_llm_context["response"] = response

            priority = player_priorities[0]["priority"]
            message = ""

            if "structured_output" in response:
                message = response["structured_output"].get("message", "")
                if "priority" in response["structured_output"]:
                    priority = response["structured_output"].get("priority", priority)
            else:
                try:
                    content = response["content"]
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0:
                        parsed = json.loads(content[json_start:json_end])
                        message = parsed.get("message", "")
                        if "priority" in parsed:
                            priority = parsed.get("priority", priority)
                except:
                    message = response.get("content", "")[:200]

            # Always mark the player as having spoken and remove from queues
            # This prevents infinite loops when message extraction fails
            players_spoken_this_phase.add(next_speaker.name)
            accused_queue.discard(next_speaker.name)
            questioned_queue.discard(next_speaker.name)

            if message:
                # Add the message to discussion
                game_state.add_event("discussion", message, "public", player=next_speaker.name, priority=priority)
                discussion_messages.append({
                    "player": next_speaker.name,
                    "priority": priority,
                    "message": message
                })
                day_results["discussion"].append({
                    "player": next_speaker.name,
                    "priority": priority,
                    "message": message
                })

                # Extract queue updates from self-reported accusations/questions
                new_accused, new_questioned = extract_queue_updates(response, next_speaker.name, alive_names)
                for name in new_accused:
                    accused_queue.add(name)
                for name in new_questioned:
                    questioned_queue.add(name)

                # Emit real-time update after each message
                if emit_callback and game_id:
                    emit_callback(game_id, game_state)
        except Exception as e:
            # Even on exception, mark player as having attempted to speak
            players_spoken_this_phase.add(next_speaker.name)
            accused_queue.discard(next_speaker.name)
            questioned_queue.discard(next_speaker.name)
            continue

    emit_status("discussion_end")
    game_state.add_event("system", "Discussion phase ends.", "all")

    # 3. Voting phase - real-time, one player at a time
    votes = []
    alive_names = [p.name for p in alive_players]
    
    for player in alive_players:
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
                temperature=0.7
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
                except:
                    pass
            
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

