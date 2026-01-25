"""Context builder for prompt templates."""

import os
from typing import Dict, Any, List

from llm.prompts.template_manager import get_template_manager
from game.rules import DEFAULT_RULES


class ContextBuilder:
    """Builds context data for template rendering."""

    MAX_SCRATCHPAD_ENTRIES = 5
    TIMING_LABELS = {
        "day_start": "Day Start",
        "pre_vote": "Pre-Vote",
        "night_start": "Night Start"
    }

    def __init__(self, game_state):
        self.game_state = game_state
        self.template_manager = get_template_manager()

    def build_context(self, player, phase=None, **extra):
        """Build complete context for rendering templates."""
        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
        return {
            'game_rules': self._get_game_rules(),
            'game_log': self._get_game_log(player),
            'private_info': self._get_private_info(player),
            'player_name': player.name,
            'role_name': player.role.name if player.role else None,
            'role_team': player.role.team if player.role else None,
            'day_number': self.game_state.day_number,
            'phase': phase or self.game_state.phase,
            'rules': rules,  # Make rules available to all templates
            **extra
        }

    def _get_game_rules(self):
        """Render game rules from template."""
        # Get unique role names in this game
        roles_in_game = set(p.role.name for p in self.game_state.players if p.role)
        # Use game-specific rules if available, otherwise fall back to defaults
        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
        return self.template_manager.render('partials/rules.jinja2', {
            'rules': rules,
            'roles_in_game': roles_in_game
        })

    def _get_game_log(self, player):
        """Get game log filtered by player visibility.

        When context pruning is enabled, past days are shown as summaries
        instead of full event logs.
        """
        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES

        # Use summarized log if context pruning is enabled
        if rules.enable_context_pruning:
            return self._build_summarized_log(player)

        # Original behavior: full event log
        return self._build_full_log(player)

    def _build_full_log(self, player):
        """Build full game log with all visible events (original behavior)."""
        from llm.prompts import get_visible_events, format_event_for_prompt

        alive_players = self.game_state.get_alive_players()
        alive_names = [p.name for p in alive_players]

        log = f"\n=== CURRENT GAME STATE ===\n"
        log += f"Day {self.game_state.day_number}, {self.game_state.phase} phase\n"
        log += f"Alive players: {', '.join(alive_names)}\n"

        visible_events = get_visible_events(self.game_state, player)

        if visible_events:
            log += "\nGame log (chronological):\n"
            for event in visible_events:
                formatted = format_event_for_prompt(event)
                log += f"- {formatted}\n"

        log += "\n=== END GAME STATE ===\n"
        return log

    def _build_summarized_log(self, player):
        """Build game log with past days summarized for context pruning."""
        from llm.prompts import get_visible_events, format_event_for_prompt

        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
        current_day = self.game_state.day_number
        current_phase = self.game_state.phase

        alive_players = self.game_state.get_alive_players()
        alive_names = [p.name for p in alive_players]

        log = f"\n=== CURRENT GAME STATE ===\n"
        log += f"Day {current_day}, {current_phase} phase\n"
        log += f"Alive players: {', '.join(alive_names)}\n"

        # Get all visible events
        visible_events = get_visible_events(self.game_state, player)

        # Separate events by day
        events_by_day = {}
        for event in visible_events:
            day = event.get("day", 1)
            if day not in events_by_day:
                events_by_day[day] = []
            events_by_day[day].append(event)

        # Build log with summaries for past days, full events for current day/night
        log += "\n"

        for day in sorted(events_by_day.keys()):
            # Check if this day has been summarized and is in the past
            has_summary = self.game_state.is_day_summarized(day)
            is_past_day = day < current_day or (day == current_day and current_phase == "night")

            if has_summary and is_past_day:
                # Use summary for this day
                player_summary = self.game_state.get_player_day_summary(day, player.name)
                if player_summary:
                    log += f"=== DAY {day} SUMMARY ===\n"
                    if player_summary.get("discussion_summary"):
                        log += f"Discussion:\n{player_summary['discussion_summary']}\n"
                    if player_summary.get("vote_summary"):
                        log += f"Votes:\n{player_summary['vote_summary']}\n"
                    if player_summary.get("night_summary"):
                        log += f"{player_summary['night_summary']}\n"
                    log += "\n"
                else:
                    # No summary for this player - fall back to raw events
                    log += self._format_day_events(day, events_by_day[day])
            else:
                # Show full events for current day/night or unsummarized days
                day_events = events_by_day[day]
                if day_events:
                    # Group into day and night events
                    day_phase_events = [e for e in day_events if e.get("phase") == "day"]
                    night_phase_events = [e for e in day_events if e.get("phase") == "night"]

                    if day_phase_events:
                        log += f"=== DAY {day} ===\n"
                        for event in day_phase_events:
                            formatted = format_event_for_prompt(event)
                            log += f"- {formatted}\n"
                        log += "\n"

                    if night_phase_events:
                        log += f"=== NIGHT {day} ===\n"
                        for event in night_phase_events:
                            formatted = format_event_for_prompt(event)
                            log += f"- {formatted}\n"
                        log += "\n"

        log += "=== END GAME STATE ===\n"
        return log

    def _format_day_events(self, day, events):
        """Format raw day events as a string."""
        from llm.prompts import format_event_for_prompt

        result = ""
        day_events = [e for e in events if e.get("phase") == "day"]
        night_events = [e for e in events if e.get("phase") == "night"]

        if day_events:
            result += f"=== DAY {day} ===\n"
            for event in day_events:
                formatted = format_event_for_prompt(event)
                result += f"- {formatted}\n"
            result += "\n"

        if night_events:
            result += f"=== NIGHT {day} ===\n"
            for event in night_events:
                formatted = format_event_for_prompt(event)
                result += f"- {formatted}\n"
            result += "\n"

        return result

    def _get_private_info(self, player):
        """Render player's private role information from template."""
        if not player or not player.role:
            return ""

        role_name = player.role.name.lower()
        context = self._build_role_context(player)

        return self.template_manager.render(
            f'partials/private_info/{role_name}.jinja2',
            context
        )

    def _build_role_context(self, player):
        """Build context dict for role-specific template rendering."""
        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
        context = {
            'player_name': player.name,
            'scratchpad_entries': self._get_scratchpad_entries(player),
            'rules': rules  # Make rules available to all private_info templates
        }

        role = player.role
        role_name = role.name

        if role_name == "Mafia":
            mafia_players = self.game_state.get_players_by_role("Mafia")
            godfather_players = self.game_state.get_players_by_role("Godfather")
            consort_players = self.game_state.get_players_by_role("Consort")
            context['mafia_names'] = [p.name for p in mafia_players + godfather_players + consort_players]
            # Also show unconverted Consigliere separately
            consigliere_players = [p for p in self.game_state.get_players_by_role("Consigliere")
                                   if not p.role.has_converted]
            context['consigliere_names'] = [p.name for p in consigliere_players]

        elif role_name == "Godfather":
            mafia_players = self.game_state.get_players_by_role("Mafia")
            godfather_players = self.game_state.get_players_by_role("Godfather")
            consort_players = self.game_state.get_players_by_role("Consort")
            context['mafia_names'] = [p.name for p in mafia_players + godfather_players + consort_players]
            # Also show unconverted Consigliere separately
            consigliere_players = [p for p in self.game_state.get_players_by_role("Consigliere")
                                   if not p.role.has_converted]
            context['consigliere_names'] = [p.name for p in consigliere_players]
            context['investigation_immunity_used'] = getattr(role, 'investigation_immunity_used', False)
            # Determine immunity status for display
            rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
            if context['investigation_immunity_used']:
                context['immunity_status'] = "used"
            elif rules.godfather_requires_other_mafia:
                context['immunity_status'] = "conditional"
            else:
                context['immunity_status'] = "active"

        elif role_name == "Miller":
            context['false_positive_used'] = getattr(role, 'false_positive_used', False)

        elif role_name == "Sheriff":
            context['investigations'] = getattr(role, 'investigations', [])

        elif role_name == "Doctor":
            context['last_protected'] = getattr(role, 'last_protected', None)

        elif role_name == "Vigilante":
            context['bullet_used'] = getattr(role, 'bullet_used', False)
            context['bullets_remaining'] = 0 if context['bullet_used'] else rules.vigilante_bullets

        elif role_name == "Mason":
            mason_players = self.game_state.get_players_by_role("Mason")
            context['mason_names'] = [p.name for p in mason_players]

        elif role_name == "Tracker":
            context['tracking_results'] = getattr(role, 'tracking_results', [])

        elif role_name == "Escort":
            context['block_history'] = getattr(role, 'block_history', [])

        elif role_name == "Consort":
            # Consort sees all mafia members
            mafia_players = self.game_state.get_players_by_role("Mafia")
            godfather_players = self.game_state.get_players_by_role("Godfather")
            consort_players = self.game_state.get_players_by_role("Consort")
            context['mafia_names'] = [p.name for p in mafia_players + godfather_players + consort_players]
            # Also show unconverted Consigliere separately
            consigliere_players = [p for p in self.game_state.get_players_by_role("Consigliere")
                                   if not p.role.has_converted]
            context['consigliere_names'] = [p.name for p in consigliere_players]
            context['block_history'] = getattr(role, 'block_history', [])

        elif role_name == "Consigliere":
            # Consigliere sees all mafia members (including other consiglieres)
            mafia_players = self.game_state.get_players_by_role("Mafia")
            godfather_players = self.game_state.get_players_by_role("Godfather")
            consort_players = self.game_state.get_players_by_role("Consort")
            consigliere_players = self.game_state.get_players_by_role("Consigliere")
            context['mafia_names'] = [p.name for p in mafia_players + godfather_players + consort_players + consigliere_players]
            context['has_converted'] = getattr(role, 'has_converted', False)

        elif role_name == "Executioner":
            context['target'] = getattr(role, 'target', 'Unknown')
            context['has_won'] = getattr(role, 'has_won', False)
            rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES
            context['fallback_role'] = rules.executioner_becomes_on_target_death

        elif role_name == "Amnesiac":
            context['has_remembered'] = getattr(role, 'has_remembered', False)

        elif role_name == "Medium":
            context['seance_history'] = getattr(role, 'seance_history', [])

        return context

    def _get_scratchpad_entries(self, player):
        """Get formatted scratchpad entries for template.

        When context pruning is enabled, only show current day's entries
        (scratchpad becomes short-term memory).
        """
        if not hasattr(player, 'scratchpad') or not player.scratchpad:
            return []

        rules = getattr(self.game_state, 'rules', None) or DEFAULT_RULES

        # Filter entries based on pruning rules
        if rules.enable_context_pruning:
            # Only show current day's entries
            current_day = self.game_state.day_number
            filtered = [e for e in player.scratchpad if e.get("day") == current_day]
        else:
            # Original behavior: last N entries
            filtered = player.scratchpad[-self.MAX_SCRATCHPAD_ENTRIES:]

        entries = []
        recent = list(reversed(filtered[-self.MAX_SCRATCHPAD_ENTRIES:]))

        for entry in recent:
            timing = entry.get("timing", "?")
            entries.append({
                'day': entry.get("day", "?"),
                'timing_label': self.TIMING_LABELS.get(timing, timing),
                'note': entry.get("note", "")
            })

        return entries
