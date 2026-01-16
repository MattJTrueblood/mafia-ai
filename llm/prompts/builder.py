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
        return {
            'game_rules': self._get_game_rules(),
            'game_log': self._get_game_log(player),
            'private_info': self._get_private_info(player),
            'player_name': player.name,
            'role_name': player.role.name if player.role else None,
            'role_team': player.role.team if player.role else None,
            'day_number': self.game_state.day_number,
            'phase': phase or self.game_state.phase,
            **extra
        }

    def _get_game_rules(self):
        """Render game rules from template."""
        return self.template_manager.render('partials/rules.jinja2', {'rules': DEFAULT_RULES})

    def _get_game_log(self, player):
        """Get game log filtered by player visibility."""
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
        context = {
            'player_name': player.name,
            'scratchpad_entries': self._get_scratchpad_entries(player)
        }

        role = player.role
        role_name = role.name

        if role_name == "Mafia":
            mafia_players = self.game_state.get_players_by_role("Mafia")
            context['mafia_names'] = [p.name for p in mafia_players]

        elif role_name == "Sheriff":
            context['investigations'] = getattr(role, 'investigations', [])

        elif role_name == "Doctor":
            context['last_protected'] = getattr(role, 'last_protected', None)

        elif role_name == "Vigilante":
            context['bullet_used'] = getattr(role, 'bullet_used', False)

        return context

    def _get_scratchpad_entries(self, player):
        """Get formatted scratchpad entries for template."""
        if not hasattr(player, 'scratchpad') or not player.scratchpad:
            return []

        entries = []
        recent = list(reversed(player.scratchpad[-self.MAX_SCRATCHPAD_ENTRIES:]))

        for entry in recent:
            timing = entry.get("timing", "?")
            entries.append({
                'day': entry.get("day", "?"),
                'timing_label': self.TIMING_LABELS.get(timing, timing),
                'note': entry.get("note", "")
            })

        return entries
