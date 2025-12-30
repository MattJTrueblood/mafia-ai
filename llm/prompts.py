"""Prompt templates for different game phases and roles."""

from typing import List, Dict


def build_game_rules() -> str:
    """
    Build the game rules section that all players should know.
    
    Returns:
        String containing the rules of Mafia
    """
    return """GAME RULES - MAFIA:

This is a game of Mafia. The game alternates between night and day phases.

FACTIONS:
There are TWO factions in this game:
- MAFIA FACTION: Members of the mafia team. They know each other's identities. They vote together each night on who to kill. Goal: Eliminate all town faction members.
- TOWN FACTION: All players aligned with the town. This includes both regular town members and special town roles (Sheriff, Doctor, Vigilante). Goal: Identify and eliminate all mafia members.

PHASES:
- Night Phase: Special roles take actions secretly. The mafia votes on who to kill, the Doctor protects someone, the Sheriff investigates someone, and the Vigilante may kill someone.
- Day Phase: All players discuss what happened, then vote on who to lynch. The player with the most votes is eliminated. If there is a tie, no one is lynched.

ROLES:
- Mafia: Members of the mafia faction. They know each other's identities. They vote together each night on who to kill.
- Town: Regular town faction members with no special abilities.
- Sheriff: A town faction member with special ability. Can investigate one player each night to learn their FACTION (mafia or town). Note: The Sheriff learns faction alignment, but NOT the specific role.
- Doctor: A town faction member with special ability. Can protect one player each night from being killed. Cannot protect the same person two nights in a row.
- Vigilante: A town faction member with special ability. Has one bullet for the entire game. Can kill one player during any night phase.

IMPORTANT CLARIFICATIONS:
- Sheriff, Doctor, and Vigilante are all TOWN FACTION members. They are NOT separate factions.
- When the Sheriff investigates someone, they learn "mafia" or "town" (faction alignment), NOT the specific role name.
- All town faction members (regular Town, Sheriff, Doctor, Vigilante) win together when all mafia are eliminated.
- All mafia faction members win together when mafia equals or outnumbers town.

WIN CONDITIONS:
- Mafia faction wins if the number of mafia members equals or exceeds the number of town faction members (including all special roles).
- Town faction wins if all mafia members are eliminated.

IMPORTANT RULES:
- Roles are secret - players do not know each other's roles unless revealed through gameplay.
- During night phase, actions happen simultaneously but are resolved in order: Mafia kill, Doctor protection, Sheriff investigation, Vigilante kill.
- If a player is protected by the Doctor, they cannot be killed that night (by mafia or vigilante). Protection is hidden: no one is told a save occurred - only that no one died.
- During day phase, all players can see who died and discuss. Then everyone votes publicly on who to lynch.  Ties or majority-abstain votes result in no lynch for that day.
- Dead players cannot participate in discussion or voting.
- The game starts with Night 0 (before Day 1).

"""


def build_town_strategic_guidance() -> str:
    """
    Build strategic guidance for town-aligned players (Town, Sheriff, Doctor, Vigilante).
    
    Returns:
        String containing strategic advice for town players
    """
    return """STRATEGIC CONSIDERATIONS FOR TOWN PLAYERS:
- Your goal is to identify and eliminate all mafia members before they outnumber you.
- Pay attention to voting patterns: Mafia may coordinate votes or avoid voting for each other.
- Watch for contradictions: Players who say one thing but vote another may be mafia.
- Observe defensive behavior: Mafia members may overreact when accused or try to shift suspicion.
- Share information strategically: Revealing your role too early can make you a target, but sharing investigation results can help.
- Consider who the mafia might target: Active players, suspected special roles, or those who seem to know too much.
- During discussion, look for players who are deflecting, contradicting themselves, or seem to be avoiding certain topics.
- Trust but verify: Don't blindly follow claims without evidence.
- Remember: Mafia wins when they equal or outnumber town, so every elimination matters.
"""


def build_mafia_strategic_guidance() -> str:
    """
    Build strategic guidance for mafia players.

    Returns:
        String containing strategic advice for mafia players
    """
    return """STRATEGIC CONSIDERATIONS FOR MAFIA:
- Your goal is to eliminate all town members before they identify you.
- You know your fellow mafia members - coordinate with them but don't make it obvious.
- Target threats: Sheriff, Doctor, and active town members who might identify you.
- Blend in: Act like a town member during discussions. Ask questions, participate, and deceive the others.
- Avoid patterns: Don't always vote together or always vote for the same players.
- Create confusion: Cast suspicion on town members, but do it subtly. Consider impersonating a special role if you think it will give you an advantage.
- Protect your identity: If you're accused, defend yourself naturally, but don't overreact.
- Consider who to kill: Eliminate players who are investigating you, protecting others, or gathering information.
- Remember: You win when mafia equals or outnumbers town, so focus on numbers.

CRITICAL MAFIA HEURISTICS (especially Days 1-2):
- NEVER attack or vote for your teammates unless absolutely necessary to avoid suspicion.
- Your PUBLIC reasoning must sound like genuine town logic - reference only PUBLIC_FACTS.
- Never say anything publicly that relies on knowledge from mafia night chat.
- If pushing a lynch, use observable public behaviors: "Their vote seemed random", "They contradicted themselves", "They're deflecting".
- Early game: blend in by making reasonable town-sounding observations, don't try too hard to lead.
- If a teammate is under suspicion, subtly defend them OR stay neutral - never pile on unless cover is blown.
"""


def build_sheriff_strategic_guidance() -> str:
    """
    Build strategic guidance specific to the Sheriff role.
    
    Returns:
        String containing strategic advice for the Sheriff
    """
    return """STRATEGIC CONSIDERATIONS FOR THE SHERIFF:
- You are a TOWN FACTION member with a special ability.
- You can investigate one player each night to learn their FACTION alignment: "mafia" or "town".
- IMPORTANT: Your investigation reveals FACTION, not specific role. If you investigate a Doctor, Vigilante, or regular Town member, you will learn "town" - you will NOT learn they are a Doctor, Vigilante, etc.
- Use your investigations wisely: Early investigations can help identify mafia, but you have limited time.
- Consider investigating suspicious players first, or players who haven't been investigated yet.
- CRITICAL - REVEALING INFORMATION:
  * If you reveal that you investigated someone and know their faction, this is an IRREVERSIBLE PUBLIC CLAIM of being the Sheriff.
  * Once you claim Sheriff and reveal investigation results, you become a prime target for the mafia.
  * Do NOT casually drop hints about investigations or what you know. Be very deliberate about when and how you reveal information.
  * Only reveal investigation results when the timing is strategically sound (e.g., when it will help town significantly, or when you're about to be lynched).
  * If you reveal too early, the mafia will kill you. If you reveal too late, town might lose before you can help.
  * Consider: Is this the right moment? Will revealing help more than it hurts?
- You might want to investigate players who are being accused, or players who seem to be avoiding suspicion.
- Keep track of your results: Knowing who is confirmed town faction can help narrow down suspects.
- Consider the game state: If town is losing, you may need to reveal your results. If you're ahead, you might keep them secret longer.
"""


def build_doctor_strategic_guidance() -> str:
    """
    Build strategic guidance specific to the Doctor role.
    
    Returns:
        String containing strategic advice for the Doctor
    """
    return """STRATEGIC CONSIDERATIONS FOR THE DOCTOR:
- You are a TOWN FACTION member with a special ability.
- You can protect one player each night from being killed (by mafia or vigilante).
- You cannot protect the same person two nights in a row.
- Your protection is HIDDEN: You will NOT be told whether your protection was successful. If the mafia targeted someone else (or abstained), you won't know. If you saved someone, the only public result is "no one died" - no one is told a save occurred.
- Protect strategically: Consider who the mafia is likely to target (active players, suspected special roles, those revealing information).
- Early game: You might protect yourself or random players since you have no information.
- Later game: Use information from discussions and voting patterns to predict mafia targets.
- Consider protecting confirmed town members, the Sheriff (if revealed), or players who seem to be gathering information.
- Rotate your protection: Since you can't protect the same person twice in a row, plan ahead.
- Your protection is powerful: A well-timed protection can save a key player and swing the game.
- CRITICAL - REVEALING INFORMATION:
  * If you reveal that you protected someone or claim to be the Doctor, this is an IRREVERSIBLE PUBLIC CLAIM.
  * Once you claim Doctor, you become a prime target for the mafia.
  * Do NOT casually drop hints about who you protected or that you have protection abilities.
  * Only reveal your role when the timing is strategically sound (e.g., to save a confirmed town member, or when you're about to be lynched).
  * Consider: Is revealing worth making yourself a target? Can you help town more by staying hidden?
"""


def build_vigilante_strategic_guidance() -> str:
    """
    Build strategic guidance specific to the Vigilante role.
    
    Returns:
        String containing strategic advice for the Vigilante
    """
    return """STRATEGIC CONSIDERATIONS FOR THE VIGILANTE:
- You have only ONE bullet for the entire game. Once used, you cannot kill anyone else.
- Consider the information available: Do you have evidence suggesting someone is mafia, or are you guessing?
- Early in the game (especially Night 0), you typically have no information about player roles or behavior patterns.
- Information that might help identify mafia includes: voting patterns, discussion behavior, contradictions, defensive reactions, or information revealed by other players.
- Using your bullet on a town member wastes your only ability and helps the mafia.
- Waiting for more information is often wise, but if you have strong evidence, acting can be valuable.
- Consider whether the target could be eliminated through day phase voting instead, saving your bullet for a more critical moment.
- The decision is yours - evaluate the situation and make the choice you believe is best for the town.
"""


def get_visible_events(game_state, viewing_player=None) -> list:
    """
    Get all events visible to a specific player, in chronological order.
    """
    if viewing_player is None:
        # No player context - return only public events
        return [e for e in game_state.events if e.get("visibility") in ("all", "public")]

    role_name = viewing_player.role.name if viewing_player.role else None
    visible = []

    for event in game_state.events:
        visibility = event.get("visibility", "all")

        # Everyone sees "all" and "public" events
        if visibility in ("all", "public"):
            visible.append(event)
        # Role-specific visibility
        elif visibility == "mafia" and role_name == "Mafia":
            visible.append(event)
        elif visibility == "sheriff" and role_name == "Sheriff":
            visible.append(event)
        elif visibility == "doctor" and role_name == "Doctor":
            visible.append(event)
        elif visibility == "vigilante" and role_name == "Vigilante":
            visible.append(event)

    return visible


def format_event_for_prompt(event) -> str:
    """Format a single event for display in a prompt."""
    player = event.get("player")
    message = event.get("message", "")
    event_type = event.get("type", "")

    if player and event_type in ("discussion", "vote", "mafia_chat", "role_action"):
        return f"{player}: {message}"
    else:
        return message


def build_public_facts(game_state, viewing_player=None) -> str:
    """
    Build PUBLIC_FACTS block - information visible to this player.
    Shows a unified chronological game log including both public events and
    any private events this player can see.
    """
    alive_players = game_state.get_alive_players()
    alive_names = [p.name for p in alive_players]

    public_facts = "=== GAME STATE ===\n"
    public_facts += f"Day {game_state.day_number}, {game_state.phase} phase\n"
    public_facts += f"Alive players: {', '.join(alive_names)}\n"

    # Get all events visible to this player
    visible_events = get_visible_events(game_state, viewing_player)

    if visible_events:
        public_facts += "\nGame log (chronological):\n"
        for event in visible_events:
            formatted = format_event_for_prompt(event)
            public_facts += f"- {formatted}\n"

    public_facts += "=== END GAME STATE ===\n"
    return public_facts


def build_private_notes(game_state, viewing_player) -> str:
    """
    Build PRIVATE_NOTES block - static role information only.
    Event history is now shown in the unified game log above.
    This section only contains role identity and key status info.
    """
    if not viewing_player or not viewing_player.role:
        return ""

    private_notes = "\n=== YOUR ROLE (secret - DO NOT reveal in public unless strategic) ===\n"
    role_name = viewing_player.role.name

    if role_name == "Mafia":
        mafia_players = game_state.get_players_by_role("Mafia")
        mafia_names = [p.name for p in mafia_players]
        private_notes += f"You are MAFIA.\n"
        private_notes += f"Fellow mafia members: {', '.join(mafia_names)}\n"

    elif role_name == "Sheriff":
        private_notes += f"You are the SHERIFF (TOWN faction).\n"
        private_notes += "You can investigate one player each night to learn their faction.\n"

    elif role_name == "Doctor":
        private_notes += f"You are the DOCTOR (TOWN faction).\n"
        private_notes += "You can protect one player each night from being killed.\n"
        if hasattr(viewing_player.role, 'last_protected') and viewing_player.role.last_protected:
            private_notes += f"You cannot protect {viewing_player.role.last_protected} again (protected last night).\n"

    elif role_name == "Vigilante":
        private_notes += f"You are the VIGILANTE (TOWN faction).\n"
        if hasattr(viewing_player.role, 'bullet_used') and viewing_player.role.bullet_used:
            private_notes += "Bullet status: USED (you have no more kills).\n"
        else:
            private_notes += "Bullet status: AVAILABLE (one kill remaining).\n"

    elif role_name == "Town":
        private_notes += f"You are TOWN (no special abilities).\n"

    private_notes += "=== END YOUR ROLE ===\n"
    return private_notes


def build_game_context(game_state, viewing_player=None) -> str:
    """
    Build full context string combining game state and role info.
    The game state shows all events visible to this player in chronological order.
    """
    context = build_public_facts(game_state, viewing_player)
    if viewing_player:
        context += build_private_notes(game_state, viewing_player)
    return context


def build_night_prompt(
    game_state,
    player: "Player",
    action_type: str,
    available_targets: List[str]
) -> str:
    """
    Build prompt for night phase actions.
    
    Args:
        game_state: Current game state
        player: The player taking action
        action_type: "mafia_vote", "doctor_protect", "sheriff_investigate", "vigilante_kill"
        available_targets: List of player names that can be targeted
    """
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()
    
    if action_type == "mafia_vote":
        mafia_players = game_state.get_players_by_role("Mafia")
        mafia_names = [p.name for p in mafia_players if p.name != player.name]
        mafia_guidance = build_mafia_strategic_guidance()
        
        prompt = f"""{rules}You are {player.name}, a member of the Mafia. Your goal is to eliminate all town members.

{mafia_guidance}
{context}

You are currently in the night phase. You and your fellow mafia members ({', '.join(mafia_names)}) must decide who to kill tonight.

Available targets (alive players): {', '.join(available_targets)}

You must vote on who to kill. Consider:
- Who is a threat to the mafia?
- Who might be the Sheriff or Doctor?
- Who has been acting suspiciously?

Respond with a JSON object containing:
- "target": the name of the player to kill, or null to abstain
- "reasoning": a brief explanation of your choice

Example: {{"target": "Alice", "reasoning": "She has been asking too many questions and might be the Sheriff."}}"""

    elif action_type == "doctor_protect":
        last_protected = None
        if hasattr(player.role, 'last_protected'):
            last_protected = player.role.last_protected
        
        cannot_protect = f" You cannot protect {last_protected} again (you protected them last night)." if last_protected else ""
        town_guidance = build_town_strategic_guidance()
        doctor_guidance = build_doctor_strategic_guidance()
        
        prompt = f"""{rules}You are {player.name}, the Doctor. You are a TOWN FACTION member with a special ability. Your goal is to protect town faction members from being killed.

{town_guidance}
{doctor_guidance}
{context}

You are currently in the night phase. You must decide who to protect tonight. The person you protect cannot be killed this night (by mafia or vigilante).{cannot_protect}

Available targets (alive players): {', '.join(available_targets)}
You can also protect yourself or abstain.

Respond with a JSON object containing:
- "target": the name of the player to protect, or null to abstain
- "reasoning": a brief explanation of your choice

Example: {{"target": "Bob", "reasoning": "I think the mafia might target him because he's been vocal."}}"""

    elif action_type == "sheriff_investigate":
        town_guidance = build_town_strategic_guidance()
        sheriff_guidance = build_sheriff_strategic_guidance()
        
        # First night specific guidance
        day0_rules = ""
        if game_state.day_number == 0:
            day0_rules = """
        NIGHT 1 GUIDELINES - Limited information available:
        - This is the very start of the game.  You have no information on any of the players except yourself.
        - Do make assumptions or guess prior context.  There is no prior context.
        - It's ok to choose a first night investigation target randomly.
        """

        prompt = f"""{rules}You are {player.name}, the Sheriff. You are a TOWN FACTION member with a special ability. Your goal is to find and eliminate the mafia.

{town_guidance}
{sheriff_guidance}
{context}
{day0_rules}

You are currently in the night phase. You must decide who to investigate tonight.
Your previous investigation results (if any) are shown in the game log above.

IMPORTANT: When you investigate someone, you will learn their FACTION alignment ("mafia" or "town"), NOT their specific role. For example:
- Investigating a Mafia member → "mafia"
- Investigating a regular Town member → "town"
- Investigating a Doctor → "town" (you learn they're town faction, but NOT that they're a Doctor)
- Investigating a Vigilante → "town" (you learn they're town faction, but NOT that they're a Vigilante)
- Investigating another Sheriff → "town" (you learn they're town faction, but NOT that they're a Sheriff)

You will learn if they are mafia or town faction.

Available targets (alive players): {', '.join(available_targets)}

Respond with a JSON object containing:
- "target": the name of the player to investigate, or null to abstain
- "reasoning": a brief explanation of your choice

Example: {{"target": "Charlie", "reasoning": "Their behavior has been suspicious."}}"""

    elif action_type == "vigilante_kill":
        bullet_used = hasattr(player.role, 'bullet_used') and player.role.bullet_used
        
        if bullet_used:
            prompt = f"""{rules}You are {player.name}, the Vigilante. You have already used your one bullet and cannot kill anyone else.

{context}

You must abstain from killing this night."""
        else:
            town_guidance = build_town_strategic_guidance()
            vigilante_guidance = build_vigilante_strategic_guidance()
            
            prompt = f"""{rules}You are {player.name}, the Vigilante. You are a TOWN FACTION member with a special ability. You have one bullet for the entire game. Your goal is to help the town faction by eliminating mafia members.

{town_guidance}
{vigilante_guidance}
{context}

You are currently in the night phase. You must decide whether to use your bullet tonight. You can only use it once in the entire game.

Available targets (alive players): {', '.join(available_targets)}

Respond with a JSON object containing:
- "target": the name of the player to kill, or null to abstain
- "reasoning": a brief explanation of your choice

Example: {{"target": "Dave", "reasoning": "I'm confident they are mafia based on their behavior."}}
Example: {{"target": null, "reasoning": "I don't have enough information yet to make a confident decision. I'll wait for more evidence."}}"""
    
    else:
        prompt = f"""{rules}{context}\nYou are {player.name}. Take your action."""
    
    return prompt


def build_turn_poll_prompt(game_state, player: "Player") -> str:
    """Build prompt for players to indicate if they want to interrupt, respond, or pass.

    This is used in the round-robin discussion system. Players can:
    - Interrupt if they have urgent information that can't wait
    - Respond if the conversation is about them or they were asked something
    - Pass to skip their speaking turn this round
    - Wait for their normal turn to speak

    Args:
        game_state: Current game state
        player: The player being prompted
    """
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()

    # Get role-specific context for the player to consider
    role_context = ""
    if player.role and player.role.name == "Sheriff":
        if hasattr(player.role, 'investigations') and player.role.investigations:
            role_context = "\nYour investigation results (private):\n"
            for name, result in player.role.investigations:
                role_context += f"- {name}: {result}\n"

    prompt = f"""{rules}You are {player.name}.

{context}
{role_context}

Another player is about to speak in the discussion. You have THREE decisions to consider:

1. Do you need to INTERRUPT?
ONLY interrupt if you have something URGENT that cannot wait:
- You need to defend yourself against a direct accusation being made RIGHT NOW
- You have critical information that could change the vote (e.g., you're the Sheriff with proof someone is mafia)
- Someone is about to be wrongly lynched and you must stop it
- Your team's win/loss is at stake and you must speak NOW

Do NOT interrupt for:
- General observations or suspicions
- Responding to questions or mentions of you (use RESPOND instead)
- Minor points that can wait for your turn

2. Do you want to RESPOND?
ONLY respond if ALL of these are true:
- Your name "{player.name}" was EXPLICITLY mentioned in the last 1-2 messages
- You have something NEW to say that you haven't already said
- You are not just going to repeat a defense or accusation you already made

NEVER respond just to:
- Say "I already explained this" or repeat yourself
- Fire back "no YOU'RE suspicious" - this is lazy and unconvincing
- Defend yourself with the same argument you already used
- Get the last word in an argument you've already made your point in

If someone accuses you and you've already defended yourself, LET IT GO. Repeating yourself looks desperate. Either find a genuinely new angle or stay silent and let others judge.

RESPOND is NOT for:
- Adding your opinion when someone else is being discussed
- Speaking because you feel like you should say something
- Getting into back-and-forth arguments

3. Do you want to PASS your speaking turn this round?
Choose to pass if you have nothing NEW to add.

4. Take NO ACTION (all false)?
This is THE DEFAULT and often correct! Choose this if:
- You weren't explicitly named in the last 1-2 messages
- You were named but have nothing new to say
- You already made your point and don't need to repeat it
- You have something to say but can wait for your normal turn

PRIORITY: Interrupts > Responds > Regular turns.
- Interrupting too often looks suspicious
- Using respond when you weren't named = abuse
- Responding just to repeat yourself = waste of everyone's time

Respond with a JSON object:
- "wants_to_interrupt": true ONLY if URGENT
- "wants_to_respond": true ONLY if your name was mentioned AND you have something NEW
- "wants_to_pass": true to skip your turn

Example (urgent defense): {{"wants_to_interrupt": true, "wants_to_respond": false, "wants_to_pass": false}}
Example (named + have new info): {{"wants_to_interrupt": false, "wants_to_respond": true, "wants_to_pass": false}}
Example (named but already said your piece): {{"wants_to_interrupt": false, "wants_to_respond": false, "wants_to_pass": false}}
Example (nothing urgent, wait for turn): {{"wants_to_interrupt": false, "wants_to_respond": false, "wants_to_pass": false}}
Example (nothing to add): {{"wants_to_interrupt": false, "wants_to_respond": false, "wants_to_pass": true}}"""

    return prompt


def build_day_discussion_prompt(game_state, player: "Player", is_interrupt: bool = False, is_respond: bool = False) -> str:
    """Build prompt for day phase discussion.

    Args:
        game_state: Current game state
        player: The player speaking
        is_interrupt: Whether this is an interrupt (urgent) message
        is_respond: Whether this is a response (player was mentioned/asked)
    """
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()

    # Get player's role info and strategic guidance
    if player.role and player.role.name == "Mafia":
        role_info = f"You are {player.name}, a member of the MAFIA FACTION."
        mafia_players = game_state.get_players_by_role("Mafia")
        mafia_names = [p.name for p in mafia_players]
        role_info += f" Your fellow mafia members are: {', '.join(mafia_names)}."
        strategic_guidance = build_mafia_strategic_guidance()
    elif player.role:
        # Town-aligned roles (Town, Sheriff, Doctor, Vigilante)
        if player.role.name in ["Sheriff", "Doctor", "Vigilante"]:
            role_info = f"You are {player.name}, a {player.role.name}. You are a TOWN FACTION member with a special ability."
        else:
            role_info = f"You are {player.name}, a {player.role.name}. You are a TOWN FACTION member."
        strategic_guidance = build_town_strategic_guidance()

        if player.role.name == "Sheriff":
            strategic_guidance += build_sheriff_strategic_guidance()
            # Add Sheriff's investigation history
            if hasattr(player.role, 'investigations') and player.role.investigations:
                role_info += "\n\nYour investigation results (private knowledge):\n"
                for name, result in player.role.investigations:
                    role_info += f"- {name}: {result}\n"
        elif player.role.name == "Doctor":
            strategic_guidance += build_doctor_strategic_guidance()
            # Add Doctor's protection history
            if hasattr(player.role, 'last_protected') and player.role.last_protected:
                role_info += f"\n\nYou protected {player.role.last_protected} last night (private knowledge)."
        elif player.role.name == "Vigilante":
            strategic_guidance += build_vigilante_strategic_guidance()
            # Add Vigilante's bullet status
            if hasattr(player.role, 'bullet_used'):
                if player.role.bullet_used:
                    role_info += "\n\nYou have already used your bullet (private knowledge)."
                else:
                    role_info += "\n\nYou still have your bullet available (private knowledge)."
    else:
        role_info = f"You are {player.name}, a player."
        strategic_guidance = ""

    # Add role-specific warning about public messages
    public_warning_discussion = ""
    if player.role and player.role.name == "Mafia":
        public_warning_discussion = """
CRITICAL: Your message will be PUBLIC and visible to ALL players.
- NEVER reveal that you are Mafia or mention your team
- NEVER reference your fellow mafia members or mafia coordination
- Non-mafia players cannot see [Mafia Discussion] messages. Keep these secret.
- Speak as if you are a town member trying to find mafia
- Only reference publicly available information
"""
    elif player.role and player.role.name in ["Sheriff", "Doctor", "Vigilante"]:
        public_warning_discussion = f"""
CRITICAL: Your message will be PUBLIC and visible to ALL players.
- You are a {player.role.name} (a TOWN FACTION member with a special ability)
- If you reveal your role or mention private information (investigations, protections, kills), this is an IRREVERSIBLE PUBLIC CLAIM
- Do NOT casually drop hints about what you know or what you've done
- Only reveal your role or private information when the timing is strategically sound
- Consider: Will revealing help town more than it makes you a target? Is this the right moment?
- Once you claim {player.role.name}, you become a prime target for the mafia - make sure it's worth it
"""
    else:
        public_warning_discussion = """
IMPORTANT: Your message will be PUBLIC and visible to ALL players.
- Be careful about revealing your specific role unless strategically necessary
- Consider whether revealing private information (investigations, etc.) helps or hurts
"""

    # Day-1 specific guidance
    day1_rules = ""
    if game_state.day_number == 1:
        day1_rules = """
DAY 1 WARNING - This is the FIRST discussion. There is NO prior history.
- Do NOT ask "what did you do last night" or "who defended who" - there was no prior discussion.
- Do NOT reference voting patterns, previous behavior, or past statements - none exist yet.
- The ONLY things that happened before now: night kills (shown in PUBLIC_FACTS).
- Valid Day 1 topics: react to the night kill, ask someone their read on a player, share a gut suspicion.
- Most people haven't spoken yet. Don't pressure silence - it's Day 1.
"""

    # Output quality rules
    output_rules = """
OUTPUT QUALITY RULES:
- Your message must reference ONLY information from PUBLIC_FACTS. Never reference PRIVATE_NOTES in public.
- Do NOT simply recap what others have said without adding a NEW inference or angle.
- Avoid vague filler like "we need to find mafia" or "let's work together" or "let's discuss last night's kill" unless followed by a specific observation.
- Every message should either: make a specific observation, ask a pointed question, defend against an accusation, or express a concrete suspicion with reasoning.

ABSOLUTE FORBIDDEN TOPICS - NEVER SAY THESE:
- NEVER accuse someone of "being quiet" or "being silent" or "not speaking"
- NEVER say "you haven't said anything" or "you've been quiet" or "you've been silent"
- NEVER say "why haven't you spoken?" or "you're avoiding speaking"
- NEVER use silence/quietness as evidence of ANYTHING - it proves nothing
- NEVER pressure someone to speak more - that's not a valid suspicion
- This applies to ALL days, not just Day 1. Silence is NEVER suspicious.
- If you catch yourself about to mention someone's silence, STOP and pick a different topic.

NEVER REPEAT YOURSELF:
- If you already accused someone, don't just accuse them again with the same reasoning.
- If you already defended yourself, don't repeat the same defense. Find a NEW angle or stay silent.
- "I already said X" or "Like I said before" = you have nothing new. Don't speak.
- If someone fires back at you, don't just fire back the same thing. That's lazy.
- Back-and-forth "you're suspicious" / "no YOU'RE suspicious" is unconvincing to everyone watching.

TO ESCALATE (if you must continue a conflict):
- Bring NEW evidence or observations
- Ask a NEW pointed question they haven't answered
- Point out a specific contradiction in what they said
- Make a concrete proposal (e.g., "Let's vote on Bob now")
- If you can't do any of these, you have nothing new - stay quiet.
"""

    # Turn-type specific context
    turn_context = ""
    if is_interrupt:
        turn_context = """
YOU ARE INTERRUPTING because you indicated you have urgent information.
This should be something critical: a role reveal, defending against an accusation, or information that could change the vote.
Make your point clearly and concisely.
"""
    elif is_respond:
        turn_context = """
YOU ARE RESPONDING because you were mentioned.
You MUST have something NEW to say - not just a repeat of what you already said.
If you already defended yourself or made this accusation, DO NOT repeat it.
Either bring a new angle, new evidence, or a pointed question - or you shouldn't have responded.
Do NOT just fire back "no you're suspicious" - that's lazy and unconvincing.
"""

    prompt = f"""{rules}{role_info}
{strategic_guidance}

{context}

{public_warning_discussion}
{day1_rules}
{output_rules}
{turn_context}

You are in the day phase discussion.

It's your turn to speak. Keep it SHORT.

CRITICAL - BE BRIEF:
- Maximum 1-2 sentences. No more.
- Do NOT ramble, explain your reasoning, or ask multiple questions.
- Make ONE point, then stop.
- Write plain text only. No JSON.

CRITICAL - DON'T REPEAT (others OR yourself):
- Read what's already been said above - including your own messages.
- If you or someone else already made a point, do NOT repeat it.
- Add something NEW - a different suspicion, a new question, new evidence.
- If you have nothing new, pick a DIFFERENT topic or stay quiet.

Good examples (notice how short):
- "Bob, why did you vote for Alice yesterday?"
- "Charlie contradicted himself - first he defended Eve, now he's voting her."
- "I'm the Sheriff. I checked Bob - he's mafia."
- "That's a fair point, but I still don't trust Diana."

Bad examples (TOO LONG - don't do this):
- "Well, I've been thinking about what happened last night and I have several observations to share with everyone about the current state of the game..."

Your message (1-2 sentences MAX):"""

    return prompt


def build_day_voting_prompt(game_state, player: "Player") -> str:
    """Build prompt for day phase voting."""
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()
    
    # Get strategic guidance based on role
    strategic_guidance = ""
    if player.role and player.role.name == "Mafia":
        strategic_guidance = build_mafia_strategic_guidance()
    elif player.role:
        # Town-aligned roles
        strategic_guidance = build_town_strategic_guidance()
        
        if player.role.name == "Sheriff":
            strategic_guidance += build_sheriff_strategic_guidance()
        elif player.role.name == "Doctor":
            strategic_guidance += build_doctor_strategic_guidance()
        elif player.role.name == "Vigilante":
            strategic_guidance += build_vigilante_strategic_guidance()
    
    alive_players = game_state.get_alive_players()
    alive_names = [p.name for p in alive_players if p.name != player.name]
    
    # Add role-specific warning about public explanations
    public_warning = ""
    if player.role and player.role.name == "Mafia":
        public_warning = """
CRITICAL: Your vote explanation will be PUBLIC and visible to ALL players. 
- NEVER reveal that you are Mafia or mention your team
- NEVER reference your fellow mafia members or mafia coordination
- Speak as if you are a town member trying to find mafia
- Only reference publicly available information (discussion, voting patterns, behavior)
- Your explanation should sound like a concerned town member, not a mafia member
- Example of what NOT to say: "As a Mafia member..." or "My mafia allies..." or "I'm mafia so..."
"""
    elif player.role and player.role.name in ["Sheriff", "Doctor", "Vigilante"]:
        public_warning = f"""
CRITICAL: Your vote explanation will be PUBLIC and visible to ALL players.
- You are a {player.role.name} (a TOWN FACTION member with a special ability)
- If you reveal your role or mention private information (investigations, protections, kills), this is an IRREVERSIBLE PUBLIC CLAIM
- Do NOT casually drop hints about what you know or what you've done in your vote explanation
- Only reveal your role or private information when the timing is strategically sound
- Consider: Will revealing help town more than it makes you a target? Is this the right moment?
- Once you claim {player.role.name}, you become a prime target for the mafia - make sure it's worth it
- Your vote explanation should only reference publicly available information unless you're intentionally making a strategic reveal
"""
    else:
        public_warning = """
IMPORTANT: Your vote explanation will be PUBLIC and visible to ALL players.
- Do NOT reveal your specific role (Sheriff, Doctor, Vigilante) unless strategically necessary
- Do NOT reveal private information (investigation results, protection targets, etc.) unless you choose to share it
- Only reference publicly available information unless you're intentionally revealing something
"""
    
    # Build role info with faction clarity
    if player.role and player.role.name == "Mafia":
        role_description = f"You are {player.name}, a member of the MAFIA FACTION."
    elif player.role:
        if player.role.name in ["Sheriff", "Doctor", "Vigilante"]:
            role_description = f"You are {player.name}, a {player.role.name}. You are a TOWN FACTION member with a special ability."
        else:
            role_description = f"You are {player.name}, a {player.role.name}. You are a TOWN FACTION member."

        # Add special role private knowledge
        if player.role.name == "Sheriff":
            if hasattr(player.role, 'investigations') and player.role.investigations:
                role_description += "\n\nYour investigation results (private knowledge):\n"
                for name, result in player.role.investigations:
                    role_description += f"- {name}: {result}\n"
        elif player.role.name == "Doctor":
            if hasattr(player.role, 'last_protected') and player.role.last_protected:
                role_description += f"\n\nYou protected {player.role.last_protected} last night (private knowledge)."
        elif player.role.name == "Vigilante":
            if hasattr(player.role, 'bullet_used'):
                if player.role.bullet_used:
                    role_description += "\n\nYou have already used your bullet (private knowledge)."
                else:
                    role_description += "\n\nYou still have your bullet available (private knowledge)."
    else:
        role_description = f"You are {player.name}, a player."

    # Day-1 specific guidance
    day1_rules = ""
    if game_state.day_number == 1:
        day1_rules = """
DAY 1 VOTING GUIDELINES - Limited information available:
- On Day 1, you have minimal public information. Your vote explanation must cite a SPECIFIC and CONCRETE public observation.
- Valid Day 1 reasons include: evasive answers, contradicting themselves, bandwagoning, deflecting questions, hedging language.
- Do NOT cite "hasn't spoken" or "being quiet" - on Day 1 most people haven't had a chance to speak yet.
- If you cannot identify a concrete public reason, you should either:
  1. Cast a "pressure vote" (vote for someone to force a reaction) and label it as such.
  2. Vote "abstain" and explain you lack sufficient public information.
- Do NOT invent reasons or claim private knowledge you don't have.
"""

    # Output quality rules for voting
    output_rules = """
VOTE EXPLANATION RULES:
- Your explanation must reference ONLY information from PUBLIC_FACTS. Never reference PRIVATE_NOTES.
- You MUST name a specific player or explicitly say "abstain".
- Your reason must be CONCRETE and OBSERVABLE (e.g., "Bob contradicted himself about X" not just "Bob seems suspicious").
- Avoid vague explanations like "gut feeling" or "seems off" without a specific public observation to back it up.

ABSOLUTE FORBIDDEN VOTE REASONS - NEVER USE THESE:
- NEVER vote for someone because they "haven't spoken" or "were quiet" or "were silent"
- NEVER cite silence as a reason for your vote - it proves NOTHING
- NEVER say "they haven't contributed" as a vote reason
- Silence is NOT evidence. Find a REAL reason or abstain.
"""

    prompt = f"""{rules}{role_description}
{strategic_guidance}

{context}

{public_warning}
{day1_rules}
{output_rules}

You are in the voting phase. You must vote on who to lynch. You can vote for any alive player or abstain.

Available targets: {', '.join(alive_names)} or "abstain"

Your explanation will be announced publicly to all players. Write it from a public perspective, as if you're speaking to the whole town. Reference only PUBLIC_FACTS.

Respond with a JSON object containing:
- "vote": the name of the player to vote for, or "abstain"
- "explanation": a brief, PUBLIC explanation of your vote (1-2 sentences) citing a SPECIFIC public observation.

Example: {{"vote": "Alice", "explanation": "Alice deflected when asked about the night kill and hasn't given a clear opinion."}}
Example (pressure): {{"vote": "Bob", "explanation": "Pressure vote on Bob to see how he responds."}}
Example (abstain): {{"vote": "abstain", "explanation": "No one has shown clear suspicious behavior yet in public discussion."}}"""

    return prompt


def build_mafia_discussion_prompt(game_state, player: "Player", previous_messages: List[Dict]) -> str:
    """Build prompt for mafia night discussion (before voting)."""
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()
    mafia_guidance = build_mafia_strategic_guidance()

    mafia_players = game_state.get_players_by_role("Mafia")
    mafia_names = [p.name for p in mafia_players]

    messages_info = ""
    if previous_messages:
        messages_info = "\nPrevious discussion messages:\n"
        for msg in previous_messages:
            messages_info += f"- {msg['player']}: {msg['message']}\n"

    alive_players = game_state.get_alive_players()
    alive_names = [p.name for p in alive_players]

    # First night specific guidance
    day0_rules = ""
    if game_state.day_number == 0:
        day0_rules = """
NIGHT 1 GUIDELINES - Limited information available:
- This is the very start of the game. You have no information on any of the players besides your fellow mafia members.
- Do not make assumptions or guess prior context. There is no prior context.
- It's ok to choose a first night target randomly.
"""

    prompt = f"""{rules}You are {player.name}, a member of the Mafia. Your fellow mafia members are: {', '.join(mafia_names)}.

{mafia_guidance}
{context}
{messages_info}
{day0_rules}

This is the mafia discussion phase. You're talking privately with your fellow mafia members about who to kill tonight.

Alive players who could be targeted: {', '.join(alive_names)}

Share your thoughts on who to target and why.

Keep it under 60 words. Complete your thought - don't trail off.

Your message (plain text, no JSON):"""

    return prompt


def build_mafia_vote_prompt(game_state, player: "Player", previous_votes: List[Dict], discussion_messages: List[Dict] = None) -> str:
    """Build prompt for mafia night voting (after discussion)."""
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()

    mafia_players = game_state.get_players_by_role("Mafia")
    mafia_names = [p.name for p in mafia_players]

    # Show discussion messages
    discussion_info = ""
    if discussion_messages:
        discussion_info = "\nMafia discussion (just concluded):\n"
        for msg in discussion_messages:
            discussion_info += f"- {msg['player']}: {msg['message']}\n"

    # Show previous votes
    votes_info = ""
    if previous_votes:
        votes_info = "\nVotes so far:\n"
        for vote in previous_votes:
            target = vote.get("target", "abstain")
            votes_info += f"- {vote['player']} voted for {target}\n"

    alive_players = game_state.get_alive_players()
    alive_names = [p.name for p in alive_players]

    prompt = f"""{rules}You are {player.name}, a member of the Mafia. Your fellow mafia members are: {', '.join(mafia_names)}.

{context}
{discussion_info}
{votes_info}

It's time to vote on who to kill tonight. Based on the discussion, choose your target.

Available targets: {', '.join(alive_names)}

Respond with a JSON object containing ONLY:
- "target": the name of the player to kill, or null to abstain

Example: {{"target": "Bob"}}"""

    return prompt


def build_role_discussion_prompt(game_state, player: "Player", role_type: str, available_targets: List[str]) -> str:
    """Build prompt for role's thinking/discussion phase (before action)."""
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()
    town_guidance = build_town_strategic_guidance()

    if role_type == "doctor":
        role_guidance = build_doctor_strategic_guidance()
        last_protected = None
        if hasattr(player.role, 'last_protected'):
            last_protected = player.role.last_protected
        constraint = f" Remember: You cannot protect {last_protected} again (you protected them last night)." if last_protected else ""

        prompt = f"""{rules}You are {player.name}, the Doctor. You are a TOWN FACTION member.

{town_guidance}
{role_guidance}
{context}

It's the night phase. Think through who you should protect tonight.{constraint}

Alive players: {', '.join(available_targets)}

Think through who needs protection most and why.

Keep it under 60 words. Complete your thought - don't trail off.

Your thoughts (plain text, no JSON):"""

    elif role_type == "sheriff":
        role_guidance = build_sheriff_strategic_guidance()

        # First night specific guidance
        day0_rules = ""
        if game_state.day_number == 0:
            day0_rules = """
NIGHT 1 - Limited information:
- This is the start of the game. You have no information on any players.
- It's ok to choose a first investigation target randomly.
"""

        prompt = f"""{rules}You are {player.name}, the Sheriff. You are a TOWN FACTION member.

{town_guidance}
{role_guidance}
{context}
{day0_rules}

It's the night phase. Think through who you should investigate tonight.

Alive players: {', '.join(available_targets)}

Think through who to investigate and why.

Keep it under 60 words. Complete your thought - don't trail off.

Your thoughts (plain text, no JSON):"""

    elif role_type == "vigilante":
        role_guidance = build_vigilante_strategic_guidance()
        bullet_status = "You have already used your bullet." if (hasattr(player.role, 'bullet_used') and player.role.bullet_used) else "You still have your bullet available."

        prompt = f"""{rules}You are {player.name}, the Vigilante. You are a TOWN FACTION member.

{town_guidance}
{role_guidance}
{context}

It's the night phase. Think through whether you should use your bullet tonight.
{bullet_status}

Alive players: {', '.join(available_targets)}

Think through whether to use your bullet and on whom.

Keep it under 60 words. Complete your thought - don't trail off.

Your thoughts (plain text, no JSON):"""

    else:
        prompt = f"You are {player.name}. Think through your action."

    return prompt


def build_role_action_prompt(game_state, player: "Player", role_type: str, available_targets: List[str], previous_discussion: str = "") -> str:
    """Build prompt for role's action decision (after discussion)."""
    context = build_game_context(game_state, viewing_player=player)
    rules = build_game_rules()

    discussion_context = f"\nYour previous reasoning:\n{previous_discussion}\n" if previous_discussion else ""

    if role_type == "doctor":
        last_protected = None
        if hasattr(player.role, 'last_protected'):
            last_protected = player.role.last_protected
        constraint = f"\nYou CANNOT protect {last_protected} (protected last night)." if last_protected else ""

        prompt = f"""{rules}You are {player.name}, the Doctor. You are a TOWN FACTION member.

{context}
{discussion_context}
Now choose who to protect tonight.{constraint}

Available targets: {', '.join(available_targets)}
You can also protect yourself.

Respond with a JSON object containing ONLY:
- "target": the name of the player to protect, or null to abstain

Example: {{"target": "Alice"}}"""

    elif role_type == "sheriff":
        prompt = f"""{rules}You are {player.name}, the Sheriff. You are a TOWN FACTION member.

{context}
{discussion_context}
Now choose who to investigate tonight.

Available targets: {', '.join(available_targets)}

Respond with a JSON object containing ONLY:
- "target": the name of the player to investigate, or null to abstain

Example: {{"target": "Bob"}}"""

    elif role_type == "vigilante":
        prompt = f"""{rules}You are {player.name}, the Vigilante. You are a TOWN FACTION member.

{context}
{discussion_context}
Now decide whether to use your bullet tonight.

Available targets: {', '.join(available_targets)}

Respond with a JSON object containing ONLY:
- "target": the name of the player to kill, or null to save your bullet

Example: {{"target": "Charlie"}}
Example: {{"target": null}}"""

    else:
        prompt = f"{rules}You are {player.name}.\n\n{context}\n\nChoose your target."

    return prompt


def build_postgame_discussion_prompt(game_state, player: "Player") -> str:
    """Build prompt for postgame discussion."""
    rules = build_game_rules()

    # Build full game context - player can see everything now
    context = build_public_facts(game_state, viewing_player=player)

    # Build a special context showing all roles
    all_roles = ""
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_roles += f"- {p.name}: {role_text} ({status})\n"

    winner = "Town" if game_state.winner == "town" else "Mafia"

    prompt = f"""{rules}
The game is over. {winner} wins!

ROLE REVEAL:
{all_roles}

=== FULL GAME LOG ===
{context}

You are {player.name} ({player.role.name.lower() if player.role else 'unknown'}).

This is the postgame discussion. Now that all roles are revealed, share your thoughts on the game.

Possible conversation topics:
- What you got right, what you got wrong
- How you used your role and abilities, if you had them
- Standout plays by yourself or others
- Major blunders or surprises
- Anything you disagree with regarding what others have said during this postgame discussion phase

Keep your message brief (1-3 sentences). Be conversational and reflective.

Your postgame comment (plain text, no JSON):"""

    return prompt


def build_mvp_vote_prompt(game_state, player: "Player") -> str:
    """Build prompt for MVP voting."""
    rules = build_game_rules()

    # Build full game context - player can see everything now
    context = build_public_facts(game_state, viewing_player=player)

    # Build player list with roles
    all_players = ""
    for p in game_state.players:
        role_text = "mafia" if p.team == "mafia" else p.role.name.lower()
        status = "alive" if p.alive else "dead"
        all_players += f"- {p.name}: {role_text} ({status})\n"

    winner = "Town" if game_state.winner == "town" else "Mafia"
    other_players = [p.name for p in game_state.players if p.name != player.name]

    prompt = f"""{rules}
The game is over. {winner} wins!

ROLE REVEAL:
{all_players}

=== FULL GAME LOG ===
{context}

You are {player.name}. Vote for the MVP (Most Valuable Player) of this game.

Consider:
- Who made the best plays?
- Who had the biggest impact on the outcome?
- Who played their role exceptionally well?

You CANNOT vote for yourself. Choose from: {', '.join(other_players)}

Respond with a JSON object containing:
- "target": the name of the player you're voting for as MVP
- "reason": a brief explanation of why they deserve MVP (1-2 sentences)

Example: {{"target": "Alice", "reason": "Her sheriff investigations turned the game around for town."}}"""

    return prompt


def build_sheriff_post_investigation_prompt(game_state, player: "Player", target: str, result: str) -> str:
    """Build prompt for sheriff's reflection after seeing investigation result."""
    rules = build_game_rules()
    context = build_game_context(game_state, viewing_player=player)
    sheriff_guidance = build_sheriff_strategic_guidance()

    # Build summary of all investigations
    investigations_summary = ""
    if hasattr(player.role, 'investigations') and player.role.investigations:
        for inv_target, inv_result in player.role.investigations:
            investigations_summary += f"- {inv_target}: {inv_result.upper()}\n"

    result_upper = result.upper()

    prompt = f"""{rules}You are {player.name}, the Sheriff. You are a TOWN FACTION member with a special ability.

{sheriff_guidance}

{context}

You just completed your investigation.

Your investigation results so far:
{investigations_summary}
You have just learned that {target} is {result_upper}!

React briefly to this result (1-3 sentences). Consider:
- Does this confirm or contradict your suspicions?
- How does this information fit with what you've observed?
- What might you do with this information tomorrow? (reveal it, keep it secret, etc.)

Your reaction (plain text, no JSON):"""

    return prompt

