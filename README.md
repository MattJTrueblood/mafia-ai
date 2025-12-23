# Mafia AI

A mafia game where AIs play against each other, powered by OpenRouter.

## Roles

Starting roles:
- Mafia
- Town
- Sheriff
- Doctor
- Vigilante

Other roles can be added later.

## MVP Features

- User starts the game.  Each player is an LLM model.  Roles are distributed secretly
to each model.
- The game is divided between day and night phases.
    -Day phase:
        -The events of the night are revealed to all surviving players
        -Discussion phase:  The surviving players may discuss their next actions freely.  The players should take turns speaking.  Likely there should be a limit to how much they can say at a time.  There should be some sort of a priority method for who gets to talk next;  not just round robin;  for example, if a player urgently thinks they need to say something, they should be able to speak over another player who has less urgent things to say.  e.g. if a player is accused of being mafia, they should likely get to respond promptly.  Some method should be used to determine priority;  not sure what.  There should be a timer, after which discussion should finish.
        -Voting phase:  The surviving players will vote on who to lynch.  They may output a short explanation of their vote, and then must publicly vote by calling a structured output or tool api.  They may vote on a player or abstain.  Votes and explanations are done sequentially, so that e.g. an unexpected earlier vote might change who a later player decides to vote for.
    -Night phase:
        -The mafia gather to vote on who to kill next.  The vote is done similarly to the voting in the day phase, with e.g. tool usage or structured outputs
        -The doctor decides who to protect.  They cannot protect the same person twice in a row.  They can choose to protect themselves or abstain as well.  That person cannot be killed if they are targeted that night (by the mafia OR the vigilante).  Again, using the api.  
        -The sheriff decides who to investigate.  Whether that player is mafia or town is revealed to them.  Again using the api.
        -The vigilante decides who to kill if they wish.  They have one bullet for the entire game and can choose to abstain any night they wish.

Other common rules variations may be configurable.

### Approach

This project is an attempt to use vibe coding to make a functional product
