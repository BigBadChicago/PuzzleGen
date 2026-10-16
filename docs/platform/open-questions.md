# Decisions, and what is still open

The eight questions the platform documents were waiting on. Seven are answered
and are now recorded in the documents they affect; the eighth is open and the
analysis it was waiting for is at the end.

Answered 2026-10-16 by the project owner.

## Answered

### 1. A player holds 5 cartridges

Five slots, as a platform constant, separate from `DEFAULT_MAX_ACTIVE = 4`,
which is the operator's generation budget. A cap can be raised without a
migration and cannot be lowered without taking something away, so five is a
floor on ambition rather than a ceiling on it.

Recorded in `platform-requirements.md` L1.

### 2. A day cached ahead of its date is cached encrypted

The client keeps its rolling five day window, and a day ahead of today is
stored encrypted with the keys for the whole window delivered on each refresh.
The offline promise survives, and reading tomorrow's board early costs more
than curiosity.

What this commits to: a key delivery path on every refresh, covering the whole
cached window rather than one day, so a player who goes offline across a day
boundary can still open the day they already hold.

Recorded in `platform-requirements.md` O6 and `release-process.md`.

### 3. An account merge is the player's choice

Not the longer streak, and not a silent union. When a provider subject is
already bound to another player, the platform raises `MergeRequired` as it does
today and then asks the player which history continues, showing them enough to
decide: each side's games, streaks and day counts.

What this commits to, and it is more than the other two options would have
cost: a player facing flow, which means a screen, a decision that can be
deferred without locking either account, and a record of what was chosen. The
upside is that nothing is ever discarded by a rule the player did not see.

The unchosen history is retained, not deleted, so a wrong choice is recoverable.

Recorded in `platform-requirements.md` I7 and `roadmap.md`.

### 4. General audience, with a strict privacy posture

The app is declared general audience, and the privacy design is kept strict
enough that the distinction mostly does not matter: no advertising identifier,
no third party analytics, no location, nothing collected by a cartridge, and
no personal data beyond what a streak needs.

Still true, and unchanged by this answer: a lawyer reviews this before the first
store submission, not after. The declaration is the cheap half; the market by
market obligations are not.

Recorded in `privacy-and-data.md`.

### 5. Free, for now

No monetisation. The consequence that matters is a deadline rather than a
feature: an author who ships under free terms and is later handed commercial
ones has a real grievance, so the decision gets revisited before the first
outside cartridge is certified, not after.

Recorded in `developer-terms.md` and `roadmap.md`.

### 6. A retraction shows an in-app notice on next open

No push notification. The notice carries the reason and what changed, which is
what turns a silently restored streak from an apparent data loss into a
correction the player can read.

Recorded in `platform-requirements.md` D9 and `operations-runbook.md`.

### 7. Logs are kept thirty days, and milestones are promoted

Thirty days for server logs. Anything worth keeping longer is promoted into a
record rather than left in a log: a milestone, a streak event, a retraction, a
merge choice. The rule has a consequence worth stating, because it is the one
that makes thirty days safe: when a value will matter in three months, writing
it to a log is the bug, and promoting it is the fix.

Recorded in `privacy-and-data.md` and `operations-runbook.md`.

## Open

### 8. May a third party cartridge ship a curated content file?

The owner's inclination is yes, with conditions, and the conditions as stated
describe a different kind of data than a content file is. The distinction is
the whole of this question, so it is set out before the recommendation.

**What a curated content file is.** A data file shipped inside the cartridge,
listing the actual items its boards are built from. The platform has one
already: `games/letters/content/letters.curated.json` is a hand built taxonomy
of thirty categories with six to twelve common members each, and the game that
reads it spells a category's name from its own members' letters.

**What a declaration is instead.** A few lines naming a slice of the governed
graph, which the platform rebuilds from the graph itself. Nothing is trusted,
because nothing is shipped: the content is reproduced on the platform's side
from content the platform governs.

**What a file buys an author.** Content the graph does not hold, and content
organised the way a player thinks rather than the way a source does. This is not
hypothetical here. It was measured on the letters game: the letter matching is
never the binding constraint, membership is, and at medium frequency one WordNet
category in 7,050 had six or more eligible members, because a category's members
are its hyponyms and a frequency filter cuts across that structure rather than
along it. No import produces a set organised by what a player knows. The
curated file exists because measurement forced it, not because it was easier.

**What a file costs the platform.** Four things. The content is ungoverned, so
nobody checked its provenance, licence, freshness or its reachable strings. The
board's quality cannot be traced back to the graph. The content cannot be
regenerated, only trusted, which removes the one check that does not depend on a
reviewer's attention. And it weakens the sentence the whole platform is
organised around: one governed body of facts, many lenses. A cartridge carrying
its own corpus is a separate game that happens to run here.

**Where the owner's conditions land.** "The data does not enter the engine"
cannot hold for content: the generator reads it to build a board, so content
that never reached the engine could not produce a puzzle. "Erased when the game
is removed, and no caching of old game data without approval" is the right rule,
and it is the right rule for a different thing: anything a cartridge would
accumulate on a player's device or about a player. Today a cartridge has no
storage, no player identifier and no device access at all, so that rule is
already satisfied by absence rather than by policy, and it is worth writing down
so it stays satisfied.

One tension to be explicit about, because answer 1 and answer 3 depend on it:
"erased when the game is removed" must not reach a player's play history.
Removing a cartridge frees the slot and keeps the history, and re-adding it
restores it. Two different owners of two different kinds of data: the cartridge
owns its content, the platform owns the player's record.

**Recommendation.** Yes, with four conditions, all of them checkable:

1. The file is reviewed at certification as content, not as code, against
   `content-policy.md`, including its reachable strings.
2. The file is pinned to the cartridge version and its hash is recorded in every
   manifest generated from it, so a board can be traced to the exact bytes it
   came from.
3. The file is served by the platform and is read only at runtime. A cartridge
   never writes to it, and nothing a cartridge produces persists on a device.
4. Every approved file is logged as a missing generator, with what the content
   is and why no declaration expresses it. The log is the input to building the
   generator that makes the next such file unnecessary.

Condition 4 is the one that keeps the doctrine intact. A file serves one game; a
generator serves every later game that wanted the same shape. Approving files
without recording why is how a platform wakes up with thirty corpora and one
graph nobody reads.

**What is still needed from the owner:** confirmation of those four conditions,
and in particular whether condition 2, a hash in every manifest, is worth the
coupling it adds between a cartridge's content and the published day.
