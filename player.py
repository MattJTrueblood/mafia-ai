"""AI Player implementation for the Mafia game."""
import time
from typing import Dict, List, Optional, Any
from models import Player, GameState, Vote, Action, Role
from openrouter_client import OpenRouterClient
from prompts import (
    get_role_system_prompt,
    build_player_context,
    get_discussion_prompt,
    get_voting_prompt,
    get_night_action_prompt,
    get_vote_tool_schema,
    get_night_action_tool_schema
)


class AIPlayer:
    """Represents an AI-controlled player in the game."""
    
    def __init__(self, player: Player, client: OpenRouterClient):
        """Initialize an AI player."""
        self.player = player
        self.client = client
        self.system_prompt = get_role_system_prompt(player.role)
        self.conversation_history: List[Dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]
    
    def get_discussion_message(
        self,
        game_state: GameState,
        recent_messages: List[Dict],
        priority_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Generate a discussion message.
        
        Returns:
            Dict with "content" (message text) and "priority" (urgency score)
        """
        context = build_player_context(self.player, game_state)
        prompt = get_discussion_prompt(context, recent_messages)
        
        # Add priority assessment prompt if needed
        if priority_context:
            prompt += f"\n\nPriority context: {priority_context.get('description', '')}"
        
        messages = self.conversation_history + [
            {"role": "user", "content": prompt}
        ]
        
        try:
            response_text = self.client.get_text_response(
                model=self.player.model,
                messages=messages,
                temperature=0.8,
                max_tokens=150
            )
            
            # Calculate priority (simple heuristic - can be enhanced)
            priority = self._calculate_priority(response_text, context, recent_messages)
            
            # Update conversation history
            self.conversation_history.append({"role": "user", "content": prompt})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            
            return {
                "content": response_text.strip(),
                "priority": priority,
                "player_id": self.player.player_id,
                "player_name": self.player.name
            }
        except Exception as e:
            # Fallback response on error
            return {
                "content": "I'm observing the situation carefully.",
                "priority": 1.0,
                "player_id": self.player.player_id,
                "player_name": self.player.name,
                "error": str(e)
            }
    
    def vote(
        self,
        game_state: GameState,
        current_votes: List[Vote]
    ) -> Vote:
        """
        Cast a vote in the voting phase.
        
        Returns:
            Vote object
        """
        context = build_player_context(self.player, game_state)
        prompt = get_voting_prompt(context, [v.to_dict() for v in current_votes])
        
        # Use tool calling for structured vote
        tool_schema = get_vote_tool_schema()
        messages = self.conversation_history + [
            {"role": "user", "content": prompt}
        ]
        
        try:
            tool_call = self.client.get_tool_call_response(
                model=self.player.model,
                messages=messages,
                tools=[tool_schema],
                tool_choice="auto",
                temperature=0.7
            )
            
            if tool_call and tool_call["name"] == "cast_vote":
                args = tool_call["arguments"]
                target_id = args.get("target_id")
                explanation = args.get("explanation", "")
                
                # Validate target_id
                if target_id:
                    target = game_state.get_player_by_id(target_id)
                    if not target or not target.is_alive():
                        target_id = None  # Invalid target, abstain instead
                
                vote = Vote(
                    voter_id=self.player.player_id,
                    target_id=target_id,
                    explanation=explanation,
                    phase="voting"
                )
                
                # Update conversation history
                self.conversation_history.append({"role": "user", "content": prompt})
                self.conversation_history.append({
                    "role": "assistant",
                    "content": f"I vote for {target_id or 'abstain'}: {explanation}"
                })
                
                return vote
            else:
                # Fallback: try to parse from text response
                response_text = self.client.get_text_response(
                    model=self.player.model,
                    messages=messages,
                    temperature=0.7
                )
                # Default to abstain if parsing fails
                return Vote(
                    voter_id=self.player.player_id,
                    target_id=None,
                    explanation=response_text[:100],
                    phase="voting"
                )
        except Exception as e:
            # Fallback: abstain on error
            return Vote(
                voter_id=self.player.player_id,
                target_id=None,
                explanation=f"Error occurred: {str(e)}",
                phase="voting"
            )
    
    def night_action(
        self,
        game_state: GameState,
        action_type: str
    ) -> Optional[Action]:
        """
        Perform a night action.
        
        Args:
            action_type: "mafia_kill", "protect", "investigate", or "vigilante_kill"
        
        Returns:
            Action object or None if action is invalid/abstained
        """
        # Check if player can perform this action
        if not self._can_perform_action(action_type, game_state):
            return None
        
        context = build_player_context(self.player, game_state)
        
        # Add role-specific context
        if action_type == "protect":
            context["last_protected"] = self.player.last_protected
        elif action_type == "vigilante_kill":
            context["has_used_vigilante_bullet"] = self.player.has_used_vigilante_bullet
        
        prompt = get_night_action_prompt(context, action_type)
        tool_schema = get_night_action_tool_schema(action_type)
        
        if not tool_schema:
            return None
        
        messages = self.conversation_history + [
            {"role": "user", "content": prompt}
        ]
        
        try:
            tool_call = self.client.get_tool_call_response(
                model=self.player.model,
                messages=messages,
                tools=[tool_schema],
                tool_choice="auto",
                temperature=0.7
            )
            
            if tool_call:
                tool_name = tool_call["name"]
                args = tool_call["arguments"]
                target_id = args.get("target_id")
                reason = args.get("reason", "")
                
                # Validate target
                if target_id:
                    target = game_state.get_player_by_id(target_id)
                    if not target or not target.is_alive():
                        target_id = None
                
                # Special validation for doctor
                if action_type == "protect" and target_id == self.player.last_protected:
                    target_id = None  # Cannot protect same person twice in a row
                
                # Special validation for vigilante
                if action_type == "vigilante_kill":
                    if self.player.has_used_vigilante_bullet:
                        target_id = None  # Already used bullet
                    elif target_id:
                        self.player.has_used_vigilante_bullet = True
                
                # Special handling for doctor
                if action_type == "protect" and target_id:
                    self.player.last_protected = target_id
                
                action = Action(
                    actor_id=self.player.player_id,
                    action_type=action_type,
                    target_id=target_id,
                    timestamp=time.time()
                )
                
                # Update conversation history
                self.conversation_history.append({"role": "user", "content": prompt})
                self.conversation_history.append({
                    "role": "assistant",
                    "content": f"I choose to {action_type} {target_id or 'abstain'}: {reason}"
                })
                
                return action
            else:
                # Fallback: abstain
                return Action(
                    actor_id=self.player.player_id,
                    action_type=action_type,
                    target_id=None,
                    timestamp=time.time()
                )
        except Exception as e:
            # Fallback: abstain on error
            return Action(
                actor_id=self.player.player_id,
                action_type=action_type,
                target_id=None,
                timestamp=time.time()
            )
    
    def _can_perform_action(self, action_type: str, game_state: GameState) -> bool:
        """Check if the player can perform a specific action."""
        if not self.player.is_alive():
            return False
        
        if action_type == "mafia_kill":
            return self.player.role == Role.MAFIA
        elif action_type == "protect":
            return self.player.role == Role.DOCTOR
        elif action_type == "investigate":
            return self.player.role == Role.SHERIFF
        elif action_type == "vigilante_kill":
            return self.player.role == Role.VIGILANTE and not self.player.has_used_vigilante_bullet
        
        return False
    
    def _calculate_priority(
        self,
        message: str,
        context: Dict,
        recent_messages: List[Dict]
    ) -> float:
        """
        Calculate priority score for discussion message.
        
        Higher priority = more urgent (should speak sooner).
        """
        priority = 1.0  # Base priority
        
        message_lower = message.lower()
        
        # Increase priority if responding to accusation
        if any(word in message_lower for word in ["accuse", "suspicious", "mafia", "guilty"]):
            priority += 0.5
        
        # Increase priority if defending self
        if any(word in message_lower for word in ["i'm", "i am", "not me", "innocent", "defend"]):
            priority += 0.8
        
        # Increase priority if player was mentioned in recent messages
        player_name_lower = self.player.name.lower()
        for msg in recent_messages[-3:]:  # Check last 3 messages
            if player_name_lower in msg.get("content", "").lower():
                priority += 1.0
                break
        
        # Increase priority if making accusation
        if any(word in message_lower for word in ["vote", "lynch", "kill", "eliminate"]):
            priority += 0.3
        
        return priority
    
    def reset_conversation(self):
        """Reset conversation history (useful for new game)."""
        self.conversation_history = [
            {"role": "system", "content": self.system_prompt}
        ]

