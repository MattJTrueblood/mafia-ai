"""
Night action abstraction for handling visits and action resolution.

This module provides the infrastructure for:
- Tracking who visits whom during night actions
- Role blocking (Escort)
- Visit consequences (Grandma)
- Visit tracking (Tracker)
- Proper action ordering and resolution
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set
from enum import Enum


class ActionType(Enum):
    """Types of night actions."""
    KILL = "kill"           # Mafia kill, Vigilante kill
    PROTECT = "protect"     # Doctor protection
    INVESTIGATE = "investigate"  # Sheriff investigation
    BLOCK = "block"         # Escort roleblock
    TRACK = "track"         # Tracker following
    VISIT = "visit"         # Generic visit (for future roles)


@dataclass
class NightAction:
    """
    Represents a single night action.

    Attributes:
        actor: Name of the player performing the action
        target: Name of the target player (None if abstaining)
        action_type: Type of action being performed
        priority: Resolution order (lower = earlier). Default priorities:
            - block: 10 (blocks happen first)
            - protect: 20
            - track: 30
            - investigate: 40
            - kill: 50 (kills happen last)
        is_visit: Whether this action counts as "visiting" the target
        blocked: Whether this action was blocked by an Escort
        data: Additional role-specific data
    """
    actor: str
    target: Optional[str]
    action_type: ActionType
    priority: int = 50
    is_visit: bool = True
    blocked: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Set default priorities based on action type if not explicitly set
        default_priorities = {
            ActionType.BLOCK: 10,
            ActionType.PROTECT: 20,
            ActionType.TRACK: 30,
            ActionType.INVESTIGATE: 40,
            ActionType.KILL: 50,
            ActionType.VISIT: 50,
        }
        if self.priority == 50 and self.action_type in default_priorities:
            self.priority = default_priorities[self.action_type]


@dataclass
class NightActionResult:
    """Result of a night action after resolution."""
    action: NightAction
    success: bool
    message: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


class NightActionCollector:
    """
    Collects and manages night actions for resolution.

    Usage:
        collector = NightActionCollector()
        collector.add_action(NightAction(...))
        collector.add_action(NightAction(...))
        results = collector.resolve(game_state)
    """

    def __init__(self):
        self.actions: List[NightAction] = []

    def add_action(self, action: NightAction):
        """Add a night action to the collector."""
        self.actions.append(action)

    def add_kill(self, actor: str, target: Optional[str], kill_type: str = "mafia"):
        """Convenience method for adding a kill action."""
        self.add_action(NightAction(
            actor=actor,
            target=target,
            action_type=ActionType.KILL,
            is_visit=True,
            data={"kill_type": kill_type}
        ))

    def add_protection(self, actor: str, target: Optional[str]):
        """Convenience method for adding a protection action."""
        self.add_action(NightAction(
            actor=actor,
            target=target,
            action_type=ActionType.PROTECT,
            is_visit=True,
        ))

    def add_investigation(self, actor: str, target: Optional[str]):
        """Convenience method for adding an investigation action."""
        self.add_action(NightAction(
            actor=actor,
            target=target,
            action_type=ActionType.INVESTIGATE,
            is_visit=True,
        ))

    def add_block(self, actor: str, target: Optional[str]):
        """Convenience method for adding a roleblock action."""
        self.add_action(NightAction(
            actor=actor,
            target=target,
            action_type=ActionType.BLOCK,
            is_visit=True,
        ))

    def add_track(self, actor: str, target: Optional[str]):
        """Convenience method for adding a track action."""
        self.add_action(NightAction(
            actor=actor,
            target=target,
            action_type=ActionType.TRACK,
            is_visit=True,
        ))

    def get_actions_by_type(self, action_type: ActionType) -> List[NightAction]:
        """Get all actions of a specific type."""
        return [a for a in self.actions if a.action_type == action_type]

    def get_visitors_to(self, target: str) -> List[str]:
        """Get all players who visited a specific target (unblocked visits only)."""
        return [
            a.actor for a in self.actions
            if a.target == target and a.is_visit and not a.blocked
        ]

    def get_visit_target(self, actor: str) -> Optional[str]:
        """Get who a specific player visited (if anyone)."""
        for action in self.actions:
            if action.actor == actor and action.is_visit and not a.blocked:
                return action.target
        return None

    def resolve(self, game_state) -> List[NightActionResult]:
        """
        Resolve all collected night actions.

        Resolution order:
        1. Apply blocks (Escort) - mark actions as blocked
        2. Check visit consequences (Grandma kills visitors)
        3. Collect tracking results
        4. Apply protections
        5. Apply kills (filtered by protections)
        6. Apply investigations

        Returns list of NightActionResult objects.
        """
        results: List[NightActionResult] = []

        # Sort actions by priority
        sorted_actions = sorted(self.actions, key=lambda a: a.priority)

        # Phase 1: Apply blocks
        blocked_players = self._apply_blocks()

        # Mark blocked actions
        for action in self.actions:
            if action.actor in blocked_players:
                action.blocked = True

        # Phase 2: Get protected players (only from unblocked protections)
        protected_players = self._get_protected_players()

        # Phase 3: Process visit consequences (for Grandma, etc.)
        visit_results = self._process_visit_consequences(game_state)
        results.extend(visit_results)

        # Phase 4: Process tracking (for Tracker role)
        track_results = self._process_tracking()
        results.extend(track_results)

        # Phase 5: Process investigations
        investigate_results = self._process_investigations(game_state)
        results.extend(investigate_results)

        # Phase 6: Process kills (protected players survive)
        kill_results = self._process_kills(protected_players, game_state)
        results.extend(kill_results)

        return results

    def _apply_blocks(self) -> Set[str]:
        """Apply roleblock actions and return set of blocked player names."""
        blocked = set()
        for action in self.actions:
            if action.action_type == ActionType.BLOCK and action.target:
                blocked.add(action.target)
        return blocked

    def _get_protected_players(self) -> Set[str]:
        """Get set of protected player names from unblocked protection actions."""
        protected = set()
        for action in self.actions:
            if (action.action_type == ActionType.PROTECT
                and action.target
                and not action.blocked):
                protected.add(action.target)
        return protected

    def _process_visit_consequences(self, game_state) -> List[NightActionResult]:
        """
        Process consequences of visits (e.g., Grandma killing visitors).

        This is a hook for future roles. Currently returns empty list.
        Override or extend for Grandma implementation.
        """
        results = []
        # Future: Check for Grandma role and process visitor deaths
        # grandmas = [p for p in game_state.players if p.role.name == "Grandma" and p.alive]
        # for grandma in grandmas:
        #     visitors = self.get_visitors_to(grandma.name)
        #     for visitor in visitors:
        #         # Grandma kills the visitor
        #         ...
        return results

    def _process_tracking(self) -> List[NightActionResult]:
        """Process Tracker actions to determine who targets visited."""
        results = []
        for action in self.actions:
            if action.action_type == ActionType.TRACK and action.target and not action.blocked:
                # Find what the tracked player visited
                tracked_target = None
                for other_action in self.actions:
                    if (other_action.actor == action.target
                        and other_action.is_visit
                        and other_action.target
                        and not other_action.blocked):
                        tracked_target = other_action.target
                        break

                results.append(NightActionResult(
                    action=action,
                    success=True,
                    data={"tracked_visit": tracked_target}
                ))
        return results

    def _process_investigations(self, game_state) -> List[NightActionResult]:
        """
        Process investigation actions.

        Note: The actual result (innocent/guilty) is determined by rules.py
        and should be called separately. This just tracks that investigations
        occurred.
        """
        results = []
        for action in self.actions:
            if action.action_type == ActionType.INVESTIGATE and action.target and not action.blocked:
                results.append(NightActionResult(
                    action=action,
                    success=True,
                    data={"investigated": action.target}
                ))
        return results

    def _process_kills(self, protected_players: Set[str], game_state) -> List[NightActionResult]:
        """Process kill actions, filtering out protected players."""
        results = []
        killed_players = set()  # Track to avoid double-kills

        for action in self.actions:
            if action.action_type == ActionType.KILL and action.target and not action.blocked:
                target = action.target

                if target in protected_players:
                    results.append(NightActionResult(
                        action=action,
                        success=False,
                        message=f"{target} was protected.",
                        data={"protected": True}
                    ))
                elif target in killed_players:
                    results.append(NightActionResult(
                        action=action,
                        success=False,
                        message=f"{target} was already killed.",
                        data={"already_dead": True}
                    ))
                else:
                    killed_players.add(target)
                    results.append(NightActionResult(
                        action=action,
                        success=True,
                        message=f"{target} was killed.",
                        data={"kill_type": action.data.get("kill_type", "unknown")}
                    ))

        return results

    def clear(self):
        """Clear all collected actions."""
        self.actions = []


def create_collector_from_phase_data(phase_data: Dict) -> NightActionCollector:
    """
    Create a NightActionCollector from existing phase_data structure.

    This is a bridge function to integrate with the current system.
    It reads mafia_votes, protected_players, and vigilante_kills from phase_data
    and creates corresponding NightAction objects.
    """
    collector = NightActionCollector()

    # Add mafia kill (from majority vote)
    mafia_target = phase_data.get("mafia_kill_target")
    if mafia_target:
        # Find a mafia player to be the "actor" (for visit tracking)
        mafia_votes = phase_data.get("mafia_votes", [])
        actor = mafia_votes[0]["player"] if mafia_votes else "Mafia"
        collector.add_kill(actor, mafia_target, kill_type="mafia")

    # Add doctor protections
    protected_players = phase_data.get("protected_players", [])
    for target in protected_players:
        # The doctor who protected is stored elsewhere, use generic for now
        collector.add_protection("Doctor", target)

    # Add vigilante kills
    vigilante_kills = phase_data.get("vigilante_kills", [])
    for vig_data in vigilante_kills:
        collector.add_kill(
            vig_data.get("vigilante", "Vigilante"),
            vig_data.get("target"),
            kill_type="vigilante"
        )

    return collector
