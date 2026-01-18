
# Roadmap (in order):

## UI second pass

UI right now is not ideal.  not everything is visible on the same screen.  Exceptions/errors aren't visible except in logs.
Discussion Status window is pretty broken.  Game events can be cleaned up a little and some redundant stuff can be removed.

## Better gambits

Encourage the AIs to do interesting or weird plays.  I have lots of ideas for this but none are very good yet.

## Personalities

Rather than just rely on the default model personalities I want to give the AIs some character.  The challenge is not just telling the AIs to roleplay or pretend to be silly at the expense of good gameplay, but rather to embody the character and play to their strengths.
An easy way might be to give them strategy cards like "You should be aggressive..." or "You should be cautious" or "You should try to be a leader".

## UI third pass

instead of a simple webpage with a game event log and such, make a UI like a visual novel.  Stretch goal

# New roles

- Mason (town sub faction, identical to villager except all masons know each other, and meet every night in secret like mafia do)
- Survivor (third party faction; goal is to be alive when the game ends.  Can win alongside any other faction including mafia as long as that faction doesn't strictly require their death)
- Executioner (third party faction.  Randomly assigned a town-aligned target at the start of the game, the executioner's goal is to convince the town to lynch his/her target. If the target dies any other way the executioner becomes a jester or a survivor (determined by rules configuration))
- Grandma (town aligned role.  Immune to all causes of death except lynching.  Anybody who visits her at night will instead die (unless saved by a doctor).  This requires mechanics for "visits", which means that e.g. mafia will need to decide a designated killer each round they decide to kill someone, and various other roles like sheriff, doctor, and vigilante will need to "visit" somebody when they do their night actions)
- Tracker (town aligned role.  every night can use their night action to follow someone at night and see who they visit.  Tracking someone is counted as a "visit" as well)
- Medium (town aligned role.  Every night, can ask a dead player one yes or no question and receive an answer: yes, no, or unknown)
- Amnesiac (third party role.  Every night, once per game, they can select a dead player and assume their role and win condition.  This includes mafia for example, allowing them to join the mafia team, or special town roles.  the amnesiac cannot win without assuming a dead player's role first;  they have no win condition on their own.)
- escort (town aligned roleblocker.  Can visit someone each night, preventing them from using their night ability or visiting someone, which can stop killings, investigations, or other actions.  Requires visit mechanics which requires mafia to decide a designated killer)
