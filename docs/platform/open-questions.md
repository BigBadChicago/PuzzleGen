# Decisions, and what is still open

The eight questions the platform documents were waiting on. All eight are
answered and recorded in the documents they affect. Nothing in this file is
pending; it is kept as the record of what was decided and why.

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

## 8. A curated content file is allowed, under four conditions

Answered yes to the four conditions below, and yes to condition 2 in
particular, which is the one that costs something.

The distinction that made this question worth asking, because the owner's
initial conditions described a different kind of data:

**A curated content file** is a data file shipped inside the cartridge, listing
the items its boards are built from. The platform has one:
`games/letters/content/letters.curated.json`, a hand built taxonomy of thirty
categories with six to twelve common members each.

**A declaration** is the alternative, and remains the default: a few lines
naming a slice of the governed graph, which the platform rebuilds itself.
Nothing is trusted because nothing is shipped.

**Why the exception is real rather than a convenience.** It was measured on the
letters game. The letter matching is never the binding constraint, membership
is, and at medium frequency one WordNet category in 7,050 held six or more
eligible members, because a category's members are its hyponyms and a frequency
filter cuts across that structure rather than along it. No import produces a set
organised by what a player knows.

**What a file costs the platform.** The content is ungoverned, so nobody checked
its provenance, licence, freshness or reachable strings. A board's quality
cannot be traced back to the graph. The content cannot be regenerated, only
trusted, which removes the one check that does not depend on a reviewer's
attention. And a cartridge carrying its own corpus is a separate game that
happens to run here.

**Why content must enter the engine.** The generator reads the file to build a
board, so content that never reached the engine could not produce a puzzle. The
rule the owner stated, erased on removal and no caching of old game data without
approval, is the right rule for a different thing: anything a cartridge would
keep on a player's device or about a player. Today a cartridge has no storage,
no player identifier and no device access, so it holds by absence, and it is now
written down so it stays held.

The separation that answers 1 and 3 depend on: removing a cartridge erases the
cartridge's content and keeps the player's play history, which re-adding
restores. The cartridge owns its content; the platform owns the player's record.

### The rule

1. The file is reviewed at certification as content, against
   `content-policy.md`, including the strings the generator can reach from it.
2. The file is pinned to the cartridge version and its hash is recorded in every
   manifest generated from it.
3. The file is served by the platform and read only at runtime. A cartridge
   never writes to it, and nothing a cartridge produces persists on a device.
4. Every approved file is logged as a missing generator, with what the content is
   and why no declaration expresses it.

**What condition 2 costs, accepted deliberately.** A hash in the manifest is a
manifest change, so building it costs a `PUZZLE_FORMAT_VERSION` bump, and a
client that cannot read the new version refuses the day rather than rendering it
partially. What it buys is that a board traces to the exact content bytes behind
it, so a retraction can name the content that caused it rather than the game
that shipped it.

Condition 4 is what keeps the doctrine intact. A file serves one game; a
generator serves every later game that wanted the same shape. Approving files
without recording why is how a platform ends up with thirty corpora and a graph
nobody reads.

Recorded in `content-policy.md` part three, `platform-requirements.md` C7 and
C10 to C13 and L9, `cartridge-model.md`, `publishing-and-certification.md`,
`game-developer-guide.md`, `privacy-and-data.md` and
`compatibility-and-versioning.md`.
