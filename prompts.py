"""Prompt templates for AI players in the Mafia game."""
from typing import List, Dict, Any
from models import Player, Role, GameState


def get_role_system_prompt(role: Role) -> str:
    """Get the system prompt for a specific role."""
    prompts = {
        Role.MAFIA: """You are a member of the Mafia in a game of Mafia. Your goal is to eliminate all Town players without being discovered.

Your abilities:
- During the night, you and your fellow Mafia members vote on who to kill
- You know who the other Mafia members are
- You must work together to eliminate Town players while avoiding suspicion during the day

Your strategy should be to:
- Blend in with Town players during discussion
- Defend yourself if accused
- Vote strategically to eliminate Town players
- Coordinate with other Mafia members (but don't reveal yourself publicly)""",

        Role.TOWN: """You are a Town player in a game of Mafia. Your goal is to identify and eliminate all Mafia members.

Your abilities:
- You have no special night actions
- You must use discussion and voting to identify Mafia members

Your strategy should be to:
- Pay attention to voting patterns and discussion behavior
- Look for inconsistencies in players' statements
- Vote to lynch suspicious players
- Work with other Town players to identify threats""",

        Role.SHERIFF: """You are the Sheriff in a game of Mafia. Your goal is to identify and eliminate all Mafia members.

Your abilities:
- Each night, you can investigate one player to learn if they are Mafia or Town
- You must use this information strategically to help Town win

Your strategy should be to:
- Investigate suspicious players
- Share your findings carefully (Mafia will try to eliminate you if discovered)
- Use your investigation results to guide voting decisions
- Be cautious about revealing your role""",

        Role.DOCTOR: """You are the Doctor in a game of Mafia. Your goal is to protect Town players and eliminate Mafia.

Your abilities:
- Each night, you can protect one player from being killed
- You cannot protect the same player two nights in a row
- Protected players cannot be killed by Mafia or Vigilante

Your strategy should be to:
- Protect important players (like the Sheriff if you suspect they exist)
- Vary your protection targets
- Use protection strategically to save key Town members
- Be cautious about revealing your role""",

        Role.VIGILANTE: """You are the Vigilante in a game of Mafia. Your goal is to eliminate Mafia members.

Your abilities:
- You have ONE bullet for the entire game
- Each night, you can choose to kill one player or abstain
- You can only use your bullet once

Your strategy should be to:
- Use your bullet carefully - you only get one shot
- Wait for strong evidence before killing
- Consider killing players you're confident are Mafia
- Be very careful not to kill Town players""",
    }
    
    return prompts.get(role, prompts[Role.TOWN])


def build_player_context(player: Player, game_state: GameState) -> Dict[str, Any]:
    """
    Build the context that a player knows about the game.
    
    This includes:
    - Their role and abilities
    - Alive players
    - Public information (discussion, votes, lynchings)
    - Role-specific private information
    """
    alive_players = game_state.get_alive_players()
    context = {
        "your_role": player.role.value,
        "your_name": player.name,
        "alive_players": [{"name": p.name, "player_id": p.player_id} for p in alive_players if p.player_id != player.player_id],
        "day_number": game_state.day_number,
        "phase": game_state.phase.value if hasattr(game_state.phase, 'value') else str(game_state.phase),
        "discussion_history": game_state.discussion_messages[-10:],  # Last 10 messages
        "recent_votes": [v.to_dict() for v in game_state.votes[-5:]],  # Last 5 votes
        "game_history": game_state.game_history[-10:],  # Last 10 events
    }
    
    # Add role-specific information
    if player.role == Role.MAFIA:
        # Mafia know who other mafia are
        mafia_players = game_state.get_alive_players_by_role(Role.MAFIA)
        context["mafia_members"] = [
            {"name": p.name, "player_id": p.player_id}
            for p in mafia_players if p.player_id != player.player_id
        ]
    
    # Add investigation results for Sheriff
    if player.role == Role.SHERIFF:
        context["investigation_results"] = {
            k: v for k, v in player.known_info.items()
            if k.startswith("investigation_")
        }
    
    return context


def get_discussion_prompt(context: Dict[str, Any], recent_messages: List[Dict]) -> str:
    """Generate a prompt for the discussion phase."""
    prompt = f"""It is Day {context['day_number']} of the Mafia game. You are {context['your_name']}, a {context['your_role']}.

Current situation:
- There are {len(context['alive_players']) + 1} players alive
- Phase: Discussion

Recent discussion:
"""
    
    if recent_messages:
        for msg in recent_messages[-5:]:  # Last 5 messages
            prompt += f"- {msg.get('player_name', 'Unknown')}: {msg.get('content', '')}\n"
    else:
        prompt += "- Discussion just started\n"
    
    prompt += f"""
Your task: Contribute to the discussion. You can:
- Share observations about voting patterns
- Defend yourself if accused
- Accuse other players (with reasoning)
- Ask questions
- Make strategic suggestions

Keep your message concise (1-2 sentences). Be strategic and consider your role's goals.

What do you want to say?"""
    
    return prompt


def get_voting_prompt(context: Dict[str, Any], current_votes: List[Dict]) -> str:
    """Generate a prompt for the voting phase."""
    prompt = f"""It is Day {context['day_number']} of the Mafia game. You are {context['your_name']}, a {context['your_role']}.

Voting phase: All players must vote on who to lynch (or abstain).

Current votes cast so far:
"""
    
    if current_votes:
        for vote in current_votes:
            voter_name = next(
                (p["name"] for p in context["alive_players"] if p["player_id"] == vote["voter_id"]),
                vote["voter_id"]
            )
            target_name = "abstain"
            if vote["target_id"]:
                target_name = next(
                    (p["name"] for p in context["alive_players"] if p["player_id"] == vote["target_id"]),
                    vote["target_id"]
                )
            prompt += f"- {voter_name} voted for {target_name}: {vote.get('explanation', '')}\n"
    else:
        prompt += "- No votes cast yet\n"
    
    prompt += f"""
Available players to vote for:
"""
    for p in context["alive_players"]:
        prompt += f"- {p['name']} (ID: {p['player_id']})\n"
    
    prompt += """
You must:
1. Provide a brief explanation for your vote (1 sentence)
2. Vote for a player (by their player_id) or abstain (use null/None)

Who do you vote to lynch, and why?"""
    
    return prompt


def get_night_action_prompt(context: Dict[str, Any], action_type: str) -> str:
    """Generate a prompt for night actions."""
    role = context["your_role"]
    
    if action_type == "mafia_kill":
        prompt = f"""It is Night {context['day_number'] + 1}. You are {context['your_name']}, a Mafia member.

You and your fellow Mafia members must vote on who to kill tonight.

Mafia members:
"""
        for m in context.get("mafia_members", []):
            prompt += f"- {m['name']}\n"
        
        prompt += f"""
Available targets:
"""
        for p in context["alive_players"]:
            prompt += f"- {p['name']} (ID: {p['player_id']})\n"
        
        prompt += """
Who do you vote to kill? Provide your vote (player_id) and a brief reason."""
    
    elif action_type == "protect":
        prompt = f"""It is Night {context['day_number'] + 1}. You are {context['your_name']}, the Doctor.

You can protect one player from being killed tonight. You cannot protect the same player two nights in a row.

Last player you protected: {context.get('last_protected', 'None')}

Available targets:
"""
        for p in context["alive_players"]:
            prompt += f"- {p['name']} (ID: {p['player_id']})\n"
        
        prompt += """
You can also protect yourself or abstain.

Who do you protect? Provide your choice (player_id) and a brief reason."""
    
    elif action_type == "investigate":
        prompt = f"""It is Night {context['day_number'] + 1}. You are {context['your_name']}, the Sheriff.

You can investigate one player to learn if they are Mafia or Town.

Previous investigation results:
"""
        for inv_key, inv_data in context.get("investigation_results", {}).items():
            target = next(
                (p["name"] for p in context["alive_players"] if p["player_id"] == inv_data["target"]),
                inv_data["target"]
            )
            prompt += f"- {target}: {inv_data['result']}\n"
        
        prompt += f"""
Available targets:
"""
        for p in context["alive_players"]:
            prompt += f"- {p['name']} (ID: {p['player_id']})\n"
        
        prompt += """
Who do you investigate? Provide your choice (player_id) and a brief reason."""
    
    elif action_type == "vigilante_kill":
        has_bullet = not context.get("has_used_vigilante_bullet", False)
        prompt = f"""It is Night {context['day_number'] + 1}. You are {context['your_name']}, the Vigilante.

You have {'ONE bullet remaining' if has_bullet else 'NO bullets remaining - you have already used your bullet'}.

Available targets:
"""
        for p in context["alive_players"]:
            prompt += f"- {p['name']} (ID: {p['player_id']})\n"
        
        if has_bullet:
            prompt += """
You can kill one player or abstain. Use your bullet carefully - you only get one!

Who do you kill? Provide your choice (player_id or null to abstain) and a brief reason."""
        else:
            prompt += """
You have no bullets remaining. You must abstain."""
    
    else:
        prompt = f"Unknown action type: {action_type}"
    
    return prompt


def get_vote_tool_schema() -> Dict:
    """Get the schema for voting tool/function."""
    return {
        "type": "function",
        "function": {
            "name": "cast_vote",
            "description": "Cast your vote in the voting phase",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {
                        "type": ["string", "null"],
                        "description": "The player_id to vote for, or null to abstain"
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Brief explanation for your vote"
                    }
                },
                "required": ["target_id", "explanation"]
            }
        }
    }


def get_night_action_tool_schema(action_type: str) -> Dict:
    """Get the schema for night action tools."""
    schemas = {
        "mafia_kill": {
            "type": "function",
            "function": {
                "name": "mafia_vote_kill",
                "description": "Vote on who the Mafia should kill",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": ["string", "null"],
                            "description": "The player_id to kill, or null to abstain"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for this kill target"
                        }
                    },
                    "required": ["target_id", "reason"]
                }
            }
        },
        "protect": {
            "type": "function",
            "function": {
                "name": "protect_player",
                "description": "Protect a player from being killed",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": ["string", "null"],
                            "description": "The player_id to protect, or null to abstain"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for protecting this player"
                        }
                    },
                    "required": ["target_id", "reason"]
                }
            }
        },
        "investigate": {
            "type": "function",
            "function": {
                "name": "investigate_player",
                "description": "Investigate a player to learn if they are Mafia or Town",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": "string",
                            "description": "The player_id to investigate"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for investigating this player"
                        }
                    },
                    "required": ["target_id", "reason"]
                }
            }
        },
        "vigilante_kill": {
            "type": "function",
            "function": {
                "name": "vigilante_kill",
                "description": "Kill a player (you only have one bullet for the entire game)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": {
                            "type": ["string", "null"],
                            "description": "The player_id to kill, or null to abstain"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for killing this player"
                        }
                    },
                    "required": ["target_id", "reason"]
                }
            }
        }
    }
    
    return schemas.get(action_type, {})

