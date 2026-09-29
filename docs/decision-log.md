# Phase 7 decision log

A record of every real decision point from the phase 7 conversation: what was
being decided, what the options were, which one was chosen, and why. This is
not a bug list (those live in `handoff.md`'s "got wrong" section) and it is
not a technical reference (that is `architecture.md`). This file exists so
that a later reader, human or Claude, can see why a choice was made instead of
having to reconstruct the reasoning from the code.

Organized by the shape of the decision, not strictly by the order it happened
in conversation.

## Part 1: Content pipeline and snapshot decisions

### Deduplicating a category's parents instead of rejecting the duplicate

**Decision:** when two source synsets resolve to one category by name (Open
English WordNet has two distinct `galley` synsets, a ship's kitchen and a
rowed ship, both hypernyms of `monoreme`), what should happen to the resulting
duplicate parent edge.

**Options considered:**
- Reject the record outright (the original behavior, and what a depth 6 export
  actually hit).
- Deduplicate parents by id, keeping the first one seen.
- Keep both, allowing a duplicate parent id in the list.

**Chosen:** deduplicate by id, first seen wins.

**Why:** category identity in this system is the canonical name, so two source
nodes with the same first lemma are one category by the system's own rule.
Rejecting the duplicate parent edge was rejecting a consequence of a rule
already in place. Keeping both would claim the same edge twice. A second,
related bug was found while fixing this: the normalizer was also emitting two
separate `Category` records that happened to share an id, letting write order
silently decide which gloss survived. Both were fixed together: one merged
record per id, one parent id per distinct category.

### Refusing to build into a database that already holds content

**Decision:** what should `tools/build_snapshot.py` do when pointed at a
database file that already has entities and categories in it from a previous
build.

**Options considered:**
- Merge silently (the original behavior).
- Warn but proceed.
- Refuse by default, with an explicit override flag for the rare case where
  merging is intended.

**Chosen:** refuse by default; `--reuse-db` overrides it.

**Why:** a merge resolves each shared lemma against whatever record is already
in the store, so an older build's sense of a word can silently win over the
one the current export files would produce. This is exactly what happened
when a depth 6 build ran against a database still holding a depth 3 snapshot:
the embedding table (built fresh from the depth 6 files) and the graph (partly
depth 3) disagreed, and the failure surfaced as a `KeyError` naming five
unrelated plant words, twenty minutes into the build. Refusing up front, with
a message naming exactly what is already in the file, turns a twenty minute
mystery into an immediate, readable stop.

### Compact embeddings format at four decimal places

**Decision:** how to write the embedding table file, given that the naive
`json.dumps(..., indent=2)` form scales badly.

**Options considered:**
- Keep indented JSON (the original format).
- One line per vector, still JSON, at the original six decimal places.
- One line per vector at four decimal places.
- A binary format (not pursued).

**Chosen:** one line per vector, four decimal places, sorted keys for
reproducibility.

**Why:** indentation costs about 6.4 kB per vector purely in whitespace; at
14,720 entities that is roughly 94 MB, close to GitHub's 100 MB file limit.
One line per vector, still at six decimals, would have helped but not enough.
Four decimals was checked against the actual math rather than assumed safe:
measured worst case cosine drift over 200 random 384 dimension vector pairs
was about 1.3 in 10,000, and every similarity threshold in this engine is set
to two decimal places, so the rounding changes no comparison's outcome. Result
was about 43 MB for the real snapshot, roughly half the indented size.

### A separate rejection reason for "no shared category" versus "too far apart"

**Decision:** whether a group search that finds no shared category among its
candidate members should report the same reason as a group rejected for being
semantically too spread out.

**Options considered:**
- Keep reusing `SEMANTIC_DISTANCE_TOO_HIGH` for both cases (the original
  behavior).
- Add a distinct `NO_SHARED_CATEGORY` reason.

**Chosen:** the distinct reason.

**Why:** the two failures need opposite fixes. A distance rejection wants a
looser similarity threshold; a missing shared category wants different
content entirely. Sharing one code sent an investigation toward the wrong
knob for an afternoon before the actual cause (a search algorithm problem, not
a threshold problem) was found.

### Enumerating candidate groups by category first, not by scanning the whole pool

**Decision:** how the content service should search a pool of entities for
groups that share a category.

**Options considered:**
- `itertools.combinations` over the whole pool, testing each combination for a
  shared category (the original behavior).
- Group entities by category first, then enumerate combinations only within
  each category.

**Chosen:** group by category first.

**Why:** measured directly against the real snapshot, a 144 entity overlay
pool in 15 categories has 480 million combinations of five, of which 3,780
actually share a category. A combination budget of 20,000 reached none of the
answers and reported the emptiness as a fact about the content, when it was a
fact about the search order. One overlay query went from 215 seconds and zero
groups to under one second and five groups.

### Round robin across categories instead of draining one category at a time

**Decision:** once grouping by category, in what order to hand combinations
back to the caller when a limit (such as "40 groups") is requested.

**Options considered:**
- Drain each category's combinations fully before moving to the next.
- Round robin: take one combination from each category in turn, cycling
  through.

**Chosen:** round robin.

**Why:** draining meant a caller asking for 40 groups got 40 different
five-subsets of the same handful of categories, since a ten member category
alone has 252 combinations of five. Every four of those forty overlapped, so a
game needing four *disjoint* groups had nothing usable. The first real day's
generation attempt failed with `not_disjoint=120`, all 120 offered
combinations rejected for exactly this reason. Round robin raised the number
of distinct categories represented in a 40 group offer from effectively one or
two to 19.

### `intersects_taxonomy` as "at least one member," not "every member"

**Decision:** how strict the new cross-taxonomy query constraint should be,
once it was clear the visible query needed some way to require overlap with
the overlay taxonomy.

**Options considered:**
- Require every member of a group to also belong to the second taxonomy.
- Require at least one member (configurable via
  `minimum_intersecting_members`, default 1).

**Chosen:** at least one, defaulting to one.

**Why:** measured directly, no lexical category in the real snapshot contains
even three overlay words; only seven contain two. Requiring every member
returned zero groups outright, an empirically impossible bar. Requiring one is
also what the board actually needs: each visible group gives up exactly one
tile to the hidden group, not all five. The field kept its stricter option
available (a caller can still ask for more) because a different, richer
overlay might one day support it.

## Part 2: Overlay content decisions

### Exporting WordNet at depth 6 instead of depth 3

**Decision:** how deep into each WordNet root's hyponym tree to export.

**Options considered:**
- Depth 3 (the original choice, sized to keep the snapshot small).
- Depth 5 or 6 (deeper, larger).
- Adding more root categories at the original depth instead of going deeper.

**Chosen:** depth 6, same five roots.

**Why:** depth 3 turned out to hold almost entirely taxonomic abstractions
rather than words a player would recognize. Measured directly: only 194
single word entities at Zipf frequency 3.5 or higher existed across the whole
depth 3 export (3,229 entities total). Depth 6 raised that to 424 recognizable
words, out of 14,720 entities, for 4.4 times the synset count from the same
five roots. Going deeper on existing roots was cheaper and more targeted than
adding new roots at depth 3, which would have widened coverage without fixing
the vocabulary-richness problem.

### Re-authoring the overlay seed against the real lexicon

**Decision:** what to do about the first overlay seed, written before any real
snapshot existed, once measurement showed only 10 of its 144 words were
actually present in the exported vocabulary.

**Options considered:**
1. Add more WordNet export roots until the missing words are covered.
2. Re-author the overlay from words the existing exports already contain.
3. Grow the overlay ten times larger so the ratio of covered words improves.
4. Change the board's rule so the hidden group need not be drawn from the
   visible groups at all.

**Chosen:** option 2 first (re-author against the depth 6 exports), with
option 1 (more roots, done as depth, see above) staged in only if needed
later.

**Why (weighed at the time):**
- Option 1 alone was measured and rejected as the primary fix: covering all
  134 missing words required 33 additional roots and about 17,000 more
  synsets, an 11 times larger snapshot, and 108 of the 134 words turned out to
  have two or more noun senses each (one, `key`, had 15), meaning a person
  would have had to disambiguate over a hundred words by hand regardless.
- Option 3 (grow the overlay) doesn't fix a word-choice problem, it just makes
  the mismatch bigger in absolute terms while leaving the ratio the same.
- Option 4 gives up the mechanic the engine exists to test: a hidden group
  that is a second reading of the same visible tiles, not a separate list.
- Option 2 was nearly free (the vocabulary already existed) and directly
  fixed what was actually broken: word choice, not export size.

**Result:** re-authored to 15 categories of 10, all 144 members verified
present in the real exports, all 144 sitting in both a lexical category and an
overlay category (versus 10 of 144 in the original seed).

### Choosing shared-part and shared-word-form as the two overlay axis types

**Decision:** what kind of double meaning the 15 overlay categories should be
built from.

**Options considered:**
- Only shared physical parts (things with teeth, things with a shell).
- Only shared word forms (a word that is also a color, also a verb).
- A mix of both kinds.

**Chosen:** a mix: 11 categories cross multiple lexical domains (shared parts
like teeth, wings, a blade, a handle, strings, a shell; shared word forms like
a color, a verb, a person's name), with 4 single domain categories kept for
their word play value alone (wheels, floats, names, blown).

**Why:** a shared part is a fact about the thing; a shared word form is a fact
about the label. The two fail differently for a player, and a board built from
only one kind reads the same way every day. Mixing them was a deliberate
choice to keep the puzzle format from becoming predictable.

### American spelling over British

**Decision:** whether the overlay's word choices (`colour`/`color`,
`harbour`/`harbor`) should follow British or American convention.

**Chosen:** American, applied retroactively to the seed and its tests.

**Why:** to pre-empt the lemma collision report flagging a spelling mismatch
against the WordNet export, which uses American forms; changing it early was
cheaper than discovering and fixing it after a report flagged it.

## Part 3: Game engine design decisions

### `UNIQUE_UP_TO_TOLERANCE` rather than `EXACTLY_ONE`

**Decision:** which uniqueness contract game 1 should declare.

**Options considered:**
- `EXACTLY_ONE`, claiming a single unambiguous solution exists.
- `UNIQUE_UP_TO_TOLERANCE`, claiming uniqueness once genuinely
  indistinguishable equivalent solutions are collapsed.

**Chosen:** `UNIQUE_UP_TO_TOLERANCE`.

**Why:** a grouping board can have genuinely equivalent solutions by
construction: swapping two tiles that each belong to both of their groups (in
terms of what the graph can tell them apart by) produces a different labeled
partition that is not a different puzzle. `EXACTLY_ONE` would be a false
promise. The tolerance contract requires the verifier to report exactly how
many solutions it collapsed (`collapsed_solutions`), which is what lets the
engine tell a genuine collapse apart from a game that never checked.

### `SOUND_INCOMPLETE` with a search bound, not `COMPLETE`

**Decision:** what completeness claim the verifier makes about its partition
search.

**Options considered:**
- Claim `COMPLETE` always, since most boards do finish their search.
- Claim `SOUND_INCOMPLETE` with a declared bound, and only report `COMPLETE`
  per puzzle when the search actually exhausted itself.

**Chosen:** `SOUND_INCOMPLETE` with `search_bound = 2,000,000`.

**Why:** the widest supported boards (36 tiles, four groups of nine) can
legitimately exceed any reasonable budget. Declaring `COMPLETE` as the game's
blanket claim and then returning a partial search on an overrun would be a
protocol violation, not an edge case. The weaker, honest declaration lets an
individual day still report `COMPLETE` when its search genuinely finished, and
a day that overruns is correctly refused publication by the uniqueness gate,
since an unexamined branch is exactly where a second solution would hide.

### The tolerance collapsing rule is signature based and deliberately narrow

**Decision:** what makes two solutions "the same puzzle" for the purpose of
the tolerance contract.

**Options considered:**
- Collapse any two solutions that assign the same set of tiles overall,
  regardless of which categories justify each group (too permissive; would
  hide real ambiguity).
- Collapse only solutions differing by tiles with byte identical distinguishing
  category signatures (a tile's categories, minus whatever every tile on the
  board shares).
- No collapsing at all (too strict; would reject boards with harmless
  incidental symmetry).

**Chosen:** signature based collapsing, keyed on each tile's distinguishing
categories.

**Why:** the design goal was narrow on purpose. Two tiles with genuinely
identical distinguishing signatures are indistinguishable to a player using
only what the graph knows, so swapping them is the same puzzle. A wider rule
risks quietly absorbing a real second solution and letting an ambiguous board
falsely claim uniqueness, which is the exact failure the whole contract exists
to prevent. Verified directly: a board where two tiles differ by even one
distinguishing category is correctly reported as having more than one
solution and correctly fails the contract; only true signature matches
collapse.

### The axis switch is derived from a feasibility check, not scheduled

**Decision:** how the game should decide when to stop asking for taxonomic
groups and switch to asking for the hidden group.

**Options considered:**
- Switch after a fixed count (for example, always after the fourth correct
  group).
- Derive the switch by asking, after every correct move, whether the tiles
  still on the board can be partitioned taxonomically at all, and switching
  the moment they cannot.

**Chosen:** derived, via a feasibility check (`still_partitionable`).

**Why (confirmed explicitly by the user after being described):** a scheduled
switch bakes in an assumption about exactly when the taxonomic axis runs out,
which in practice is after the fourth group for correct play but is not
guaranteed by anything structural. The derived version is correct regardless
of board shape and generalizes without a special case. The check is
existence-only (stops at the first found partition, `max_solutions=1`) so the
common case is cheap; the expensive direction (proving no partition exists)
is also the one that triggers the switch, so the cost lands exactly where the
interesting event happens. A budget overrun during this check is treated as
"no partition found" rather than "probably yes," because answering "probably
yes" on the strength of not having looked would make the axis switch fire on
some replays of a session and not others, exactly the failure
`DeterminismError` exists to catch.

### Difficulty is four weighted features with board size normalized out

**Decision:** how to compute a difficulty score that is comparable across the
supported board sizes (5 to 9 tiles per group).

**Options considered:**
- Score directly from raw counts (states examined, tile overlap counts),
  accepting that wider boards would then score harder purely from size.
- Normalize every feature against what the same board shape would produce at
  its easiest or at random, then weight the normalized features.

**Chosen:** normalization, with four features: `cross_group_pull` (0.45),
`hidden_evenness` (0.20), `signature_thinness` (0.20), `search_cost` (0.15).

**Why:** an unnormalized score would put every wide board in the hardest band
for a reason that has nothing to do with the puzzle itself, which would make
the difficulty label stop meaning anything a player could feel. `search_cost`
in particular is normalized as a log ratio against an expected-states baseline
computed from the board's own group size, specifically because raw search
cost differs by orders of magnitude between a five and a nine tile group.
Weights are reasoned rather than fitted (confidence is deliberately reported
as low, 0.4) and are explicitly flagged for recalibration once real boards
can be scored and sanity checked by a person actually playing them.
`search_cost`'s weight of 0.15 was specifically kept rather than dropped to
zero, on the reasoning that it is the least trusted feature but removing it
outright throws away a real (if weak) signal before any real data exists to
judge it by.

### No `near_miss` share token

**Decision:** whether the share artifact vocabulary should include a token
for "one tile away" outcomes, matching the `near_miss` state symbol shown live
on the board.

**Chosen:** no. `near_miss` stays a state symbol (shown live) but is not a
share token.

**Why:** a share artifact is built from the move ledger with payloads already
stripped, so it can see that a submission was wrong but never see how wrong.
Declaring a share token that nothing in the data pipeline could ever actually
emit would be a word in the vocabulary with nothing behind it.

## Part 4: Content-gap resolution decisions (batch 7)

### Closing the overlay-to-lexicon intersection gap

**Decision:** with the measured finding that a board needs four visible
groups collectively covering all five members of a hidden group, and that the
existing pool essentially never produces that (best measured coverage was one
member out of five), how to close the gap.

**Options considered:**
1. Add a query constraint (`intersects_taxonomy`) so visible groups can be
   asked to contain overlay members directly, making the intersection the
   query's job instead of luck's.
2. Two-phase generation: pick the hidden group first, then query specifically
   for visible groups containing each of its members.
3. Grow the overlay enough that the intersection stops being rare (the ratio
   problem: 144 words against 14,720 entities).
4. Change the board rule so the hidden group need not be drawn from the
   visible groups.

**Chosen:** option 1, implemented and measured; found necessary but not
sufficient on its own; option 3 (via the proposer, growing clustered overlay
content) identified as the remaining necessary step, to be pursued as an
external, multi day task rather than more code. Option 2 was named as the
fallback if option 1 could not express the constraint (it could, so option 2
was not built). Option 4 was explicitly rejected.

**Why:** option 1 was the smallest change that could plausibly work and
generalizes to any future game with the same shape of requirement, so it was
tried first. Once built and measured, it raised the usable visible group pool
from effectively nothing to 11, but still not enough: a board needs four
*disjoint* usable categories, and direct measurement showed only 10 lexical
categories in the entire snapshot have both five or more members and an
overlap word at group size 5, dropping to zero at group size 9. This is a
structural fact about the current overlay's word choices (each chosen for one
isolated double meaning, scattered across near-leaf categories), not
something a smarter query can route around. Option 4 was rejected because it
gives up the exact mechanic (a hidden group that is a second reading of the
visible tiles) the whole engine exists to demonstrate.

### How to grow the overlay given the review system's ten-day accept rule

**Decision:** the review system requires ten accepted days, on separate
calendar days, before a proposed word activates, as an anti-abuse property.
Growing the overlay to fix the gap above therefore cannot finish inside one
conversation. What should happen about that.

**Options considered:**
- Run the proposer and review process for real, across real separate days,
  accepting the ten-day design as the correct pace.
- Temporarily lower `ACCEPT_THRESHOLD` / the `ReviewService` threshold to
  speed this one growth push, then restore it.
- Skip the proposer and hand-author more overlay words directly, this time
  checking each candidate's lexical category size before adding it.

**Chosen:** the first option, as the primary path; the other two recorded as
available but not chosen.

**Why:** the ten-day rule exists specifically so that no single actor (human
or automated) can unilaterally promote content into the live graph. Lowering
the threshold defeats that property for whatever window it is lowered, even
temporarily. Hand-authoring again would work faster but would lose the "a
human vetted this against real, already-embedded content" property that the
proposer specifically provides, and would repeat the exact mistake (words
chosen for meaning alone, not for lexical cluster size) that caused the
original seed's coverage problem. The two alternatives are documented rather
than discarded, so that if the wait genuinely becomes unacceptable later, the
choice to bypass the safeguard is made deliberately rather than by default.

## Part 5: Conceptual and framing corrections

These were not code decisions. They are corrections to how the system was
being described, made explicitly by the user after the underlying idea had
already been implemented, and are recorded here because the implementation
was already correct; only the explanation of it was wrong, and a wrong
explanation left uncorrected would mislead whoever reads it next.

### "Overlay" is a general storage mechanism, not a word play mechanism

**Correction:** the `intersects_taxonomy` field, and the `"overlay"` taxonomy
it was first used against, had been described in comments and documentation
using word specific language ("a second meaning," "word play," "carries a
double meaning"). This was wrong. The mechanism is general: any entity can
belong to categories from more than one taxonomy at once, and any query can
ask for the places two taxonomies' memberships overlap, with no requirement
that either taxonomy be about words at all.

**What changed as a result:** the comment on `intersects_taxonomy` in
`content/query.py`, and the corresponding section of `architecture.md`
(placed near the top of the phase 3 notes), were rewritten to lead with the
general contract and only afterward use game 1's word-based numbers as one
illustration, never as the definition. A worked non-word example (a number
game using a `parity` taxonomy intersected with a `notable_years` taxonomy)
was added specifically so the generality is demonstrated, not just asserted.

### The four-piles-plus-hidden-group pattern is a storage pattern, not a game

**Correction:** the same idea one level up. The whole "four visible piles,
one hidden pile borrowed from them" picture had been described, including in
a diagram made for an earlier explanation, as if it were inherently a game.
It is not. It is a way to store and query information: a primary
categorization plus a secondary, cross-cutting one layered on top, with no
mechanic attached. Game 1 is one thing that reads that stored pattern and
turns it into a "spot the hidden group" puzzle. A number game or an image
game could read the identical stored pattern and build something with no
resemblance to game 1's specific rules, borrowing nothing from it except the
storage and query shape.

**What changed as a result:** `architecture.md` gained an explicit definition
of the pattern, stated as a data model first, with game 1 introduced
afterward as "the first thing that reads it," never as its owner.
`handoff.md`'s "got wrong" section records this correction explicitly so a
future reader does not repeat the conflation.

## Part 6: Program-level decisions

### Five games, not one, as the validation target for the shared-graph design

**Decision:** how many games need to exist before the project's core bet
(that one shared graph and engine can serve many different games) counts as
validated.

**Options considered:**
- One additional game (game 2) is enough to prove the plugin boundary works.
- Several games are needed, specifically because a single second game only
  proves a second plugin *can* be built, not that the graph and engine
  generalize across genuinely different game shapes.

**Chosen:** five games total.

**Why:** stated directly: testing whether a second plugin can be added without
touching the engine or game 1 is a narrower question than testing whether the
architecture holds up across different kinds of games (word, number, symbol,
image, relationship based). Five was chosen as the number that would force
that broader test to actually happen rather than being declared complete
after one comfortable, word-shaped repeat of game 1.

### The engine must support number, math, symbol, and image games, decided before phase 8

**Decision:** whether non-word game types are an assumed future capability or
an explicit, binding requirement on the content layer's design going forward.

**Chosen:** explicit, binding requirement, stated ahead of phase 8 starting.

**Why:** the current schema (`Entity`, `Category`, `Relationship`, embeddings,
frequency bands) was built and tuned entirely against lexical content. Several
parts of `content/service.py` (similarity, frequency banding) were flagged as
likely to silently assume "content is a word." Deciding this now, rather than
after four more word-shaped games depend on the schema as it stands, was
explicitly reasoned as cheaper: finding out the schema needs to change is
expensive in proportion to how much already depends on it staying the same.

### Game 2 selection: which non-word game to build first, and in what order

**Decision:** given the requirement above, which specific game to build next,
among several real candidates, and in what order across the five-game target.

**Options considered and scored:**

| Option | What it is | Uses the storage as | Schema impact |
|---|---|---|---|
| **A. Number grouping** | Connections-style, but tiles are numbers. Primary categories: even/odd, prime, perfect square. Overlay: "also a year," "also a jersey number." | Identical to game 1's shape, content swapped | None |
| **B. Math relationships** | Puzzle built on `factor_of`, `multiple_of`, `consecutive_prime_of` as real relationship edges, not category membership | A different query pattern entirely: grouping by relationship, not by shared category | Query layer only (new operation) |
| **C. Symbol sorting** | Tiles are glyphs (a triangle, pi, infinity, operators). Primary: shape family (angular, round, operator). Overlay: "also used in physics." | Same shape as A, but content has no natural language meaning | None, but forces frequency and embeddings to go genuinely unused |
| **D. Image matching** | Tiles are actual images. Primary: a visual category, curator tagged. Overlay: a second visual property. | Same shape as A and C, but the entity has no text worth showing at all | One new field: an image reference on `Entity` |

**Scored against three separate questions:**
- **Easiest to build:** option A. It reuses `puzzlegen/games/grouping/`
  almost unchanged; only the content provider and the specific taxonomies
  differ.
- **Best pressure test:** option D. It is the one option that cannot be
  faked, because numbers and symbols can still limp through the current
  schema as strings in `canonical_name`. An image cannot. Building D forces
  a real answer on whether `Entity` needs an image reference field and what
  a presentation model sends when there is no text to show at all, which is
  exactly the kind of question the "not only word games" requirement above
  was meant to force before it gets expensive to answer.
- **Best ratio of pressure test to effort:** option C. Medium effort, and it
  captures most of what D captures (does the pipeline genuinely function
  with no natural frequency band or embedding, rather than merely going
  unused by coincidence) without needing a schema change.

**Chosen order across the five game target:** C, then D, then A, then B, with
a fifth slot left open for whatever real word game makes sense once the graph
has grown. B was kept in the list specifically because it is the only
candidate that tests grouping by relationship instead of by shared category,
which nothing built so far, including game 1, has ever exercised; every query
built to date groups by category membership alone.

## How to use this file

When a future decision seems to reopen something recorded here, check this
file before re-deciding it from scratch. If the same tradeoff is being
weighed again, the reasoning above should either still hold, in which case it
is worth citing rather than re-deriving, or it should have a stated reason it
no longer applies, which is itself worth recording as a new entry rather than
silently overwriting this one.
