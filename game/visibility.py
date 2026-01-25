"""
Group-based visibility system for managing private communication channels.

This module provides infrastructure for:
- Mafia private chat (existing functionality, now generalized)
- Mason private chat (town members who know each other)
- Future group-based visibility (Lovers, Coven, etc.)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Union


@dataclass
class VisibilityGroup:
    """
    Represents a group of players who can see each other's private messages.

    Attributes:
        name: Unique identifier for the group (e.g., "mafia", "masons", "lovers")
        members: Set of player names in this group
        team: Optional team association (for filtering in rules)
        can_communicate: Whether members can send messages visible only to group
    """
    name: str
    members: Set[str] = field(default_factory=set)
    team: Optional[str] = None
    can_communicate: bool = True

    def add_member(self, player_name: str):
        """Add a player to this group."""
        self.members.add(player_name)

    def remove_member(self, player_name: str):
        """Remove a player from this group."""
        self.members.discard(player_name)

    def has_member(self, player_name: str) -> bool:
        """Check if a player is in this group."""
        return player_name in self.members

    def get_visibility_list(self) -> List[str]:
        """Get list of member names for event visibility."""
        return list(self.members)


class VisibilityManager:
    """
    Manages visibility groups for a game.

    Automatically creates a "mafia" group from players with mafia team.
    Supports arbitrary groups for Masons, Lovers, etc.
    """

    def __init__(self):
        self.groups: Dict[str, VisibilityGroup] = {}

    def create_group(self, name: str, team: Optional[str] = None,
                     can_communicate: bool = True) -> VisibilityGroup:
        """Create a new visibility group."""
        group = VisibilityGroup(name=name, team=team, can_communicate=can_communicate)
        self.groups[name] = group
        return group

    def get_group(self, name: str) -> Optional[VisibilityGroup]:
        """Get a group by name."""
        return self.groups.get(name)

    def get_or_create_group(self, name: str, team: Optional[str] = None,
                            can_communicate: bool = True) -> VisibilityGroup:
        """Get existing group or create new one."""
        if name not in self.groups:
            return self.create_group(name, team, can_communicate)
        return self.groups[name]

    def add_player_to_group(self, group_name: str, player_name: str):
        """Add a player to a group."""
        group = self.get_or_create_group(group_name)
        group.add_member(player_name)

    def remove_player_from_group(self, group_name: str, player_name: str):
        """Remove a player from a group."""
        group = self.get_group(group_name)
        if group:
            group.remove_member(player_name)

    def get_player_groups(self, player_name: str) -> List[str]:
        """Get all groups a player belongs to."""
        return [
            name for name, group in self.groups.items()
            if group.has_member(player_name)
        ]

    def get_visibility_for_group(self, group_name: str) -> List[str]:
        """Get visibility list for a group."""
        group = self.get_group(group_name)
        return group.get_visibility_list() if group else []

    def can_player_see_group_message(self, player_name: str, group_name: str) -> bool:
        """Check if a player can see messages for a group."""
        group = self.get_group(group_name)
        return group.has_member(player_name) if group else False

    def initialize_from_players(self, players: List) -> None:
        """
        Initialize standard groups based on player roles.

        Currently creates:
        - "mafia" group from all mafia team members (Mafia, Godfather, Consort, Consigliere)
        - "masons" group from Mason role players
        """
        # Create mafia group - includes all mafia-aligned roles
        # All mafia know each other's identities (including undercover Consigliere)
        mafia_group = self.get_or_create_group("mafia", team="mafia")
        for player in players:
            if player.team == "mafia":
                mafia_group.add_member(player.name)

        # Create masons group (if any Mason players exist)
        mason_players = [p for p in players if p.role and p.role.name == "Mason"]
        if mason_players:
            mason_group = self.get_or_create_group("masons", team="town")
            for player in mason_players:
                mason_group.add_member(player.name)

    def resolve_visibility(self, visibility: Union[str, List[str]]) -> List[str]:
        """
        Resolve visibility specification to list of player names.

        Args:
            visibility: Can be:
                - "all": returns ["all"]
                - "public": returns ["public"]
                - group name (e.g., "mafia"): returns group members
                - list of player names: returns as-is

        Returns:
            List of player names who can see the event, or ["all"]/["public"]
        """
        if isinstance(visibility, str):
            if visibility in ("all", "public"):
                return [visibility]
            # Check if it's a group name
            group = self.get_group(visibility)
            if group:
                return group.get_visibility_list()
            # Otherwise treat as single player name
            return [visibility]
        return visibility


def get_mafia_visibility(game_state) -> List[str]:
    """
    Get visibility list for mafia-only events.

    This is a bridge function for existing code.
    Uses VisibilityManager if available, falls back to direct lookup.

    Includes all mafia team members: Mafia, Godfather, Consort, and Consigliere.
    """
    if hasattr(game_state, 'visibility_manager'):
        return game_state.visibility_manager.get_visibility_for_group("mafia")

    # Fallback for backwards compatibility
    return [
        p.name for p in game_state.players
        if p.role and p.role.name in ("Mafia", "Godfather", "Consort", "Consigliere")
    ]


def get_mason_visibility(game_state) -> List[str]:
    """
    Get visibility list for mason-only events.

    Uses VisibilityManager if available, falls back to direct lookup.
    """
    if hasattr(game_state, 'visibility_manager'):
        return game_state.visibility_manager.get_visibility_for_group("masons")

    # Fallback for backwards compatibility
    return [
        p.name for p in game_state.players
        if p.role and p.role.name == "Mason"
    ]


def filter_events_by_visibility(events: List[dict], player_name: str,
                                 game_state) -> List[dict]:
    """
    Filter events based on what a specific player can see.

    Args:
        events: List of event dicts with 'visibility' key
        player_name: Name of player to filter for
        game_state: GameState for group lookups

    Returns:
        List of events visible to the player
    """
    visible = []

    for event in events:
        visibility = event.get("visibility", "all")

        if visibility in ("all", "public"):
            visible.append(event)
        elif isinstance(visibility, list):
            if player_name in visibility:
                visible.append(event)
        elif isinstance(visibility, str):
            # Check if it's a group name
            if hasattr(game_state, 'visibility_manager'):
                manager = game_state.visibility_manager
                if manager.can_player_see_group_message(player_name, visibility):
                    visible.append(event)

    return visible
