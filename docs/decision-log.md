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

**Correction, re-measured against the committed file:** `embeddings.json` is
20.7 MB, not 43 MB. The 43 MB figure was carried forward inside the session
that produced this entry and never checked against what reached git. The
decision itself is unaffected (one line per vector at four decimals is still
the chosen format, and the drift measurement behind four decimals still
stands); only the reported size was wrong. Recorded here rather than
overwritten, because a number stated as measured and then silently replaced
teaches the next reader nothing about why to re-check the others.

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

## Phase 7, continued: sibling grouping, Option R, Option B, and a reproducibility gap

A second session picked up phase 7 after the handoff below was written for the
first one. This section records everything decided since: it does not
replace anything above, and the guidance at "How to use this file" applies to
all of it exactly as it does to the first session's entries.

### Direct membership was the wrong grouping rule for an imported hierarchy

**Decision:** how a "visible group" should be defined, once measurement
showed the existing rule produced almost nothing usable.

**The finding that forced this:** `_group_combinations` grouped entities by
*direct* `is_a` membership in one category. Measured against the real
snapshot, this works only when a single WordNet synset has five or more
lemmas of its own. Two tools were built to check this and both confirmed the
same thing: `boat` has one lemma ("boat"); `canoe`, `kayak`, `dory` are each
their own sibling synset, not members of `boat`. The handful of categories
that did clear five members were synonym piles (`baby buggy` with nine names
for one pram) or merge-collision artifacts (`jackal` with four species merged
onto one name). Neither is a usable "five different things" visible group.
`synonym_density.py` was built specifically to prove this is universal, not
occasional: every row it can ever produce is one concept with many names, by
definition of what a WordNet synset is.

**Options considered:**
- Keep direct membership, and only ever use hand-curated taxonomies (number
  games, symbol games) for future content.
- Change grouping to **sibling-hyponymy**: a group is "distinct children of
  one parent" rather than "direct members of one category." `fish` becomes a
  usable parent because `salmon`, `trout`, `herring` are its children, each
  contributing one tile.
- Grow the overlay and lexicon indefinitely under the old rule, accepting
  that usable categories would always be rare synonym clusters.

**Chosen:** sibling-hyponymy, added as a second mode (`GroupingMode.SIBLINGS`)
alongside the original (`GroupingMode.SHARED_CATEGORY`), which stays the
default. `ContentQuery.grouping` selects between them. Game 1 now sets
`VISIBLE_GROUPING = SIBLINGS` for its lexical queries; any future game whose
taxonomy is hand-authored (a number game's `parity`/`prime` categories, built
the way `overlay.curated.json` already is) keeps the default, because a
hand-curated category never had the synonym-pile problem in the first place —
a human filling "even numbers" by hand produces exactly the right shape on
the first try. This boundary condition (import-derived vs. hand-curated) is
the one to re-check before assuming the fix generalizes to a new game.

**Why:** measured directly, the sibling rule turns categories with three to
four own-lemmas into categories with dozens of children: `stringed instrument`
went from one unusable direct-member count to 13 direct children. The fix is
general — it lives in the shared `ContentService`, not in game 1's code — so
any future game reading an imported hierarchy inherits it automatically.

### Which word stands for a child: first lemma, overridable by the overlay's own tag (Option R)

**Decision:** under sibling grouping, a parent's "children" are categories,
not words. Something has to pick the one word shown as the tile for each
child.

**Options considered:**
- **First lemma** (the exporter's own first-listed synonym) always wins. Zero
  new data, but loses overlay words that happen to be a second-listed synonym:
  `fiddle` (synonym of `violin`), `uke` (not `ukulele`), `tugboat` (not `tug`).
  Measured: nine of the original 144 overlay words were lost this way.
- **Most frequent word** wins. Measured and rejected: `car`'s most frequent
  synonym is `machine`, which is a worse tile than `car`, and about 31 percent
  of entities have no frequency score to rank by at all.
- **Option R: the word the curator tagged in the overlay wins**, falling back
  to first lemma when nothing is tagged. A word filed under two children of
  one parent (`horn` under both `cornet` and `French horn`) stands for at most
  one; the category whose own name the word carries is reserved for it, so
  tagging a synonym elsewhere can never cost another category its tile.

**Chosen:** Option R, approved by the user explicitly. Implemented once, in
`puzzlegen/content/representatives.py`'s `choose_representatives`, called by
both the content service (to build real boards) and `tools/overlay_coverage.py`
(to predict them), so the two can no longer disagree about which word is a
tile — which they had, silently, before this was centralized.

**Why:** the overlay already exists to name the word whose second meaning is
the hidden group; a curator tagging `fiddle` as "the word that stands for the
violin category, for this board's purposes" is the overlay doing exactly its
stated job, not a special case.

### The partition-validity rule was almost vacuous on real ancestry-bearing tiles

**Decision/finding:** `partition_validator` claimed to refuse two groups
"justified by the same category," but it compared only each group's single
most-specific shared category. On real content, where every tile carries its
whole ancestry (a craft tile is simultaneously `airplane`, `craft`, and
`vehicle`), two differently-labeled groups routinely share two or three
categories, and the old check missed this entirely. Measured on a real
generation attempt: ten "craft" tiles split arbitrarily into two groups of
five produced 126 "distinct solutions," because every such split was
"justified" by `{craft, vehicle}` twice over. This silently blocked every
real day (`MULTIPLE_SOLUTIONS: 126 distinct`), hidden behind a much larger
`WORD_FREQUENCY_MISMATCH` count in the failure report until the reporting
itself was fixed to show later pipeline stages first.

**Chosen fix:** a partition is valid only if its groups can be given labels,
one per group, drawn from each group's own shared categories, such that no
two labels' extensions (the actual tile sets they name, on this board) are
comparable — neither a subset of the other. This is a real constraint
satisfaction search (backtracking, fewest-option groups first for speed), not
a single-category comparison. Verified both ways: five new tests fail against
the old rule and all pass against the new one; the intended partition (one
label per group, from each group's defining parent) always has a valid
labeling because the parents were chosen not to nest in the first place.

**Why this had to be fixed rather than worked around:** it is the uniqueness
guarantee itself. A rule that under-rejects here means a published board can
be "solved" 126 different ways, which is the single most dangerous failure
mode phase 5's own decisions already named.

### Two search-order bugs, both the same shape, found by generating real days

**Finding, twice:** `itertools.combinations` varies only its last element, so
the first N offers of any large pool share almost all their earliest members.
This bit the project in two different places, discovered in sequence:

1. **Within one lexical category**, in the content service's shared-category
   candidate generator. A hidden overlay category with 14 members has 2,002
   subsets of five; the first 200 (the hidden-pool limit) all shared the same
   four earliest words, so the one subset a board needed was never offered,
   and generation failed reporting `not_disjoint` — which looks exactly like
   "no answer exists" from inside the search, when the real cause was "the
   answer was never looked at."
2. **Across the four-group quadruple search**, in `plans_for`. Raising the
   hidden-pool limit from 24 to 200 (to fix a different starvation problem,
   below) divided the per-candidate combination budget from about 166 down to
   20, and those 20 were the worst 20 by the same ordering defect.

**Chosen fix for (1):** `_spread_combinations` — cyclic windows over the
sorted pool before falling through to full enumeration, so every member leads
at least one early window and a small limit still reaches a representative
spread rather than one corner of the space. Six new tests assert mutual
disjointness among early offers, which the old order could never provide;
confirmed to fail against the old code and pass against the new.

**Chosen fix for (2):** replaced brute-force enumeration of the shortlist's
`C(16,4)` quadruples with `covering_quadruples`, which builds the four groups
*from* the hidden word that needs placing (scarcest word first) instead of
searching blindly and testing each guess. This is the same "search
proportional to the answers, not the pool" principle phase 3 already applied
to the content service's own group search. Verified against the exact failure
shape: a test reproduces the real day's budget of 20 and confirms a plan is
found, where the old enumerator needed thousands.

**Why both are recorded together:** they are the same defect recurring at two
layers, which is itself the lesson — an ordering assumption baked into one
shared Python idiom (`itertools.combinations`) will resurface anywhere a
limited "take the first N" is applied to its output, and should be checked
for on sight rather than rediscovered per call site.

### The hidden group must actually be placeable, checked once rather than discovered by search

**Finding:** the hidden-group query could return candidates containing words
the lexical taxonomy does not have at all (a word only the overlay knows).
Such a candidate can never become a board, but nothing said so until the
four-group search exhausted its budget trying anyway.

**Chosen fix:** two checks, both applied before any quadruple search starts,
not per-combination:
- The hidden query now requires `intersects_taxonomy=LEXICAL_TAXONOMY,
  minimum_intersecting_members=group_size` — every hidden word must also be
  a lexicon word. Applied as a pool pre-filter in the content service, not a
  per-subset check, because per-subset checking starves a category whose
  first valid subset comes late in enumeration order (the same shape as the
  combinations bug above).
- `generate_candidates` separately checks that every hidden word is a tile
  somewhere in the day's actual visible pool (a word can be in the lexicon
  generally but not survive that day's frequency/similarity gates), before
  spending any search budget on that hidden candidate.

**Why both and not one:** "in the lexicon" and "a tile today" are genuinely
different facts — the first is permanent, the second depends on the day's
difficulty band — and conflating them would either reject a word forever or
search for a word that can never appear today.

### The temptation floor: Option A vs. Option B, and Option B was chosen

**Decision:** whether the four visible groups must share family resemblance
(the "temptation" floor, `MINIMUM_TEMPTATION`), once `no_shared_domain`
started appearing as a real, measured blocker on actual overlay categories
(`word that is also a color`, `thing found in a kitchen`) rather than a
theoretical one.

**Options considered, explained to the user in plain terms with a diagram:**
- **Option A — keep the floor; author hidden groups within one domain.**
  Boards stay thematically coherent (the documented reason the seven-root,
  single-domain design existed). Cost: a curator must choose words whose
  homes share ancestry, which is extra authoring work and shrinks which
  overlay categories are usable until that work is done.
- **Option B — lower the floor to zero; treat the whole lexical taxonomy as
  one pool.** Any four groups, from any domains, are a valid board. Cost:
  "four unrelated piles" is a real board shape under this rule, which the
  difficulty model was not designed around, and which needs its own signal
  if a product wants to show "how related is this board" to a player or a
  curator.

**Chosen:** Option B, the user's explicit choice, with an explicit follow-on
requirement: report a relatedness confidence instead of silently publishing
unrelated boards without comment.

**What was built:** `MINIMUM_TEMPTATION` is now `0` (previously `1`),
documented as a product choice a future game can raise, not a structural
necessity. `temptation_of` (the raw pairwise-shared-ancestry count) is still
computed and still orders candidates, but a new `relatedness_of` normalizes it
to 0..1 against the most a board of that size could carry, and travels on
every candidate's payload as `relatedness`. 0 is an honest "these four piles
share nothing," not a defect; it is a ranking signal and a possible
player/curator-facing number, never a gate at the current floor value.
`tools/overlay_coverage.py` imports the same `MINIMUM_TEMPTATION` constant
the engine uses, so the coverage model and the real generator cannot disagree
about which boards the floor would allow.

**Why Option B over A:** stated by the user directly — mixing domains is the
intended design, and a confidence number is the right way to surface the
cost of that rather than refusing the content. Recorded here rather than
re-litigated: if a future conversation wants to reconsider the floor, the
diagrammed tradeoff above is the one that was weighed, and the data that
tipped it was that `no_shared_domain` was blocking multiple real,
already-authored overlay categories by the time the choice was made, not a
hypothetical one.

### The category-merge rule was changed from "first import wins" to "union the parents," with a new cycle check this requires

**Finding:** two senses of one word, exported under different roots, become
one category (category identity is the name). The first import to name that
category kept its parents; a later import of the same name added none. This
silently homed one sense in the wrong family: `viola` the plant (under
`herb`) and `viola` the instrument (under `bowed stringed instrument`) merged
into one category whose parent was whichever synset was processed first,
discarding the other sense's ancestry entirely. Measured on the real seven
(then sixteen) root export: this affected no overlay word on the committed
seven-root set purely by chance (no plant root was in that set yet), but
measured on a wider combination it affects `viola`, a word already in the
seed's `thing with strings` category.

**Chosen fix:** `SnapshotBuilder._merge_category` now unions a merging
category's parents rather than keeping only the first-seen set.

**The cost, paid explicitly rather than left implicit:**
- `CategoryRepository.put` refusing a category naming an absent parent was
  previously the *entire* cycle-prevention mechanism (architecture.md, phase
  1), with no traversal-based checker anywhere, because a parent reference
  was fixed at construction and could never be added later. That stopped
  being true: a parent can now be added to an *existing* stored category, so
  both records already exist and the existence check cannot see a cycle. One
  new traversal (`CategoryRepository.ancestor_ids` / `descendant_ids`) checks
  every added parent against the merging category's own descendants before
  writing it; a parent that would close a loop is refused and named in the
  import's warnings rather than silently written or silently dropped.
- Depth is denormalized onto every category and read by Wu-Palmer similarity
  and the "most specific shared category" rule. A category that gains a
  deeper parent changes its own depth and that of everything beneath it;
  `_repair_depths` recomputes nearest-first after any merge that adds a
  parent.

**Why this one change, not a bigger restructuring:** the alternative
(sense-based category identity instead of name-based) was considered and
rejected as out of scope — it would change every category id already minted
and require re-tagging the whole overlay seed, for a problem the union fix
solves with a much smaller, well-contained change.

### A separate rejection reason for `NOT_IN_REQUIRED_TAXONOMY` applied as a pool filter, not a per-group gate

Folded into the fixes above but worth naming on its own: when a group query's
`intersects_taxonomy` requirement is for *every* member
(`minimum_intersecting_members >= group_size`), it is now applied once, to
shrink the candidate pool, rather than checked per enumerated subset. The
per-group gate inside `_assess_group` was kept in addition (defence in depth,
per phase 3's existing rule that every gate runs even when a precise query
should already guarantee its result), proven non-redundant by a test that
disables the pool filter and confirms the per-group gate alone still refuses
an invalid subset.

### A known, currently unresolved reproducibility gap

**Finding, not yet fixed:** the committed `content/graph.sqlite` in the
repository is a 24,576-byte stub, not the real multi-ten-megabyte database a
real build produces — `*.sqlite` was never actually committed in full. The
committed `content/seeds/embeddings.json` does not cover every committed
lexicon root (confirmed missing: `planet`, `sport`, and parts of `fish`,
`insect`, `reptile`, `flower`, `herb`, `flavorer`, `kitchen` at various
points across this session, since lexicons were added over several pushes
without the embeddings/frequency regeneration step being re-run each time).
A from-scratch clone of the repository cannot currently reproduce the 30/30
board-generation result the user achieved in their Codespace, because the
Codespace's local `embeddings.json`/`frequency.json` were evidently ahead of
what was committed at each push. This is recorded as open, not resolved; see
the handoff for the exact regeneration sequence required.

### A known, currently unresolved content-starvation gap: every generated day is the same board

**Finding, not yet fixed:** confirmed directly against the user's own
committed `build/days-30.json`: all 30 generated days have the identical
hidden group (`word that is also a color`) and identical score. Traced to its
cause: the generator offered exactly **one** candidate on every day checked,
not several among which the day's own randomness could choose. The
day-to-day variety mechanism (the per-day deterministic RNG) is correctly
wired and was never the problem; it has nothing to select among because only
one overlay category is currently feasible at each board size in practice.
This is expected to resolve as more overlay categories cross the feasibility
line (the coverage tool already shows the count rising: 1 to 4 feasible
categories at size 5 across this session), not as a code change. See the
handoff for the specific categories closest to becoming feasible next.

## Phase 7, continued (session 2): the two "unresolved gaps" closed, roots committed, and the fork to a multi-game engine

The two gaps the previous section logged as unresolved are both resolved. One
of them was misdiagnosed here, and the correction is the most important entry
in this section.

### The reproducibility gap: closed

The committed `graph.sqlite` stub and the embeddings not covering every root
were real. Resolution: `graph.sqlite` is not committed at all now (it is a
build output; `.gitignore` excludes `*.sqlite*`), and the seven new-root
lexicon files plus a depth-8 `fish` were committed (commit `4cd9e1b`) so the
committed seeds reproduce `coverage-A.json` and `coverage-B.json`. The seeds,
`frequency.json` and `embeddings.json` are the reproducible inputs; the graph
is regenerated from them. `docs/snapshot-build.md` was rewritten to the current
23-root reality.

### The "every board is the same" gap: it was a code defect, and the previous entry's instruction was wrong

The previous section concluded this was content starvation ("the generator
offered exactly one candidate... expected to resolve as more overlay categories
cross the feasibility line, not as a code change") and the handoff told the next
session not to touch candidate selection. **That was wrong, and was verified
wrong by direct measurement.**

Root cause: `_ordered` in `puzzlegen/games/grouping/generate.py` sorted
candidates by a fixed total order over content properties (enriched, then
temptation, then candidate id). Nothing in that key varied by day. The engine
does not *choose* among candidates; it verifies them in list order and takes the
first that passes, which is a lookup, not a selection. So every day served at
one board size produced a byte-identical board regardless of how many hidden
groups the content supported. On a real day the generator was offered up to
twelve distinct hidden groups and the fixed order picked the same one every
time.

Two fixes:

- **Round-robin candidate accumulation with a per-group share.** `_candidates_at`
  previously drained one hidden group's plans until the candidate budget was
  spent, so later hidden groups were never reached. It now drains one plan per
  hidden group in rotation with an even per-group ceiling, so the day has
  distinct hidden groups to choose among rather than many boards wearing the
  first one.
- **A day-derived shuffle inside each rank.** `_ordered` now shuffles with
  `rng.derive("candidate-order").shuffled(...)` before a stable sort by
  enriched-then-temptation. The shuffle survives inside each equal-rank run, so
  enrichment and temptation still rank while the day key reaches selection.
  Randomness lives in the plugin's ordering, not the engine, because the engine
  performs a lookup; this keeps one source of randomness per board, which the
  old no-randomness-in-the-generator rule was protecting. Determinism is
  preserved: same day, same board.

Tests: `tests/unit/test_grouping_variety.py`. Six of its ten tests fail against
the old code and pass against the fix, which makes them regression guards rather
than decoration. (commit `453be84`)

**Standing lesson reinforced:** the previous entry's "verified correct" claim
for the selection mechanism was made against a snapshot where only one candidate
existed, where correct selection and broken selection are indistinguishable. A
"do not touch X, it is correct" instruction is only as good as the input it was
verified against.

### `generate_days.py` reported the drawn size, not the served size

A day tries its drawn board size first and falls back to other sizes if the
content cannot serve it. The tool reported the size the day *drew*, read from
`group_size_for(day)`, not the size it was *served*, which the engine already
carries in `outcome.puzzle.payload["group_size"]`. A run leaning entirely on
fallback therefore looked like full coverage across sizes 5 to 9; the "30/30
across every size" claim rested on this misread. Fixed to read the served size
and to count drawn versus served on separate axes. This is the same recompute-
instead-of-read failure mode as the collision report below. (commit `453be84`)

### The lemma-collision report was a false-alarm generator

`tools/report_lemma_collisions.py` claimed that when two senses of one word
merge into one category, the later sense loses its parents, and it listed 14
overlay words as losing a parent on the real roots. The builder does not do
that: `_merge_category` unions the parents of every same-named sense and refuses
only an edge that would make the category its own ancestor. Checked against a
real dev build, 0 of the 14 actually lose a parent; all 14 were false alarms,
including the 4 the previous section had already recorded.

The report was rewritten to mirror the builder: union parents, refuse only a
cycle-closing (ancestor or descendant) edge, follow the given file order, union
hypernym edges across files (a synset exported under two roots keeps only the
hypernyms inside each root's subtree, so letting a later file overwrite an
earlier one dropped edges and hid the very loops the report exists to show), and
a visited-set loop guard so a malformed lexicon cannot hang it. New test
`tests/unit/test_collision_report_matches_builder.py` builds the same files both
ways and fails if the report and the builder ever disagree; three of its tests
fail against the old report. (commit `e9313ed`)

### New roots, and the A/B coverage gate

Seven roots were added to close most overlay coverage gaps: `mollusk`,
`crustacean`, `cutting_implement`, `tableware`, `power_tool`, `color`,
`drug_of_abuse`, plus `fish` re-exported at depth 8 (the only root truncated at
depth 6, 498 to 617 synsets). A superseded `shellfish` root was dropped; it held
food senses, not the animals, and `mollusk` + `crustacean` replace it. The full
roots analysis, with synset ids and a second batch for the 22 still-unsupplied
words, is in `docs/candidate-roots.md`.

Two builds were measured. A = 16 base + mollusk, crustacean, cutting_implement,
tableware, power_tool. B = A + color + drug_of_abuse. Distinct hidden categories
reaching all four homes, at difficulty MEDIUM:

```
          size5  size6  size7  size8  size9
build A     1      3      4      3      3
build B     1      3      4      3      4
```

**Decision: ship A, hold B.** B's only unique wins ride on `color`, which
introduces a real defect: for the hidden group `word that is also a color`, the
visible homes include `chromatic color`, `pink` and `red`. A board asking the
player to find the colour word while one visible group is literally the colours
is broken, and the generator's leak check misses it because the visible group is
a sibling of the hidden category, not the hidden category itself. The fix (open):
a visible group may not be an ancestor or descendant of the hidden category.

### Investigative method note: measure against the engine, load OEWN directly when the package host is blocked

Several findings this session came from loading OEWN 2024 directly from its
GitHub release (`english-wordnet-2024.xml.gz`, `wn.add`) when `python -m wn
download`'s host was unreachable, then measuring candidate roots and parent
merges against a real dev build rather than against a tool's recomputed opinion.
Every "the tool says X" claim that disagreed with the engine turned out to be
the tool's error, never the engine's. When a diagnostic and the engine could
disagree about a number, make the diagnostic read the engine's real output or
import the real constant; do not re-derive it.

### The fork: a multi-game engine, a surface form subsystem, and game 2

Settled this session, recorded here because it sets project direction:

- PuzzleGen is now explicitly a multi-game engine, not one game with a graph.
  Game 1 (grouping) reasons about meaning; game 2 (`missing_letters`) reasons
  about a word's letters.
- The **surface form subsystem** is an engine-level subsystem, not a helper. It
  defines what the letters of an entity are (case folding, diacritics,
  spaces/hyphens, multi-word collapse), which surface form counts (primary lemma
  versus a shorter true synonym, e.g. `bowed instrument` for `bowed stringed
  instrument`), a stable inspectable entity-to-letters mapping, and a
  precomputed index since character queries run per candidate every daily build.
  It sits beside the content graph and adds no new content-service operation.
  `missing_letters` is its first client. Open: build the subsystem first
  (platform-first, the user's lean) or inline and extract later.
- **`missing_letters`** is specified in full in `docs/missing-letters-requirements.md`.
  Settled: SOUND_INCOMPLETE verification with a stated search bound and a pinned
  word list; a mark must occur in its own tile's word; multi-word category names
  permitted with common names favored and shorter-synonym substitution allowed;
  when difficulty wants more blanks than the data forces, leave a
  could-have-been-marked tile unmarked to manufacture the extra blank (recording
  per board which blanks were forced versus manufactured); at least two blanks
  always; free anagram; one mark per tile max; not every tile marked. No coverage
  tool for it exists yet.

### Repository hygiene

Added `.gitattributes` (`* text=auto eol=lf` plus binary guards and `*.sh`
pinned to LF) to end the CRLF/LF churn that made the user's Windows checkout show
every tracked text file as modified. Widened the `.gitignore` sqlite rule from
`*.sqlite3` to `*.sqlite*`, added `*.bak`, and untracked the stray
`.gitignore.bak`. (commit `e02f7ca`)

## Session 3, 2026-10-10

### Ship A. Game 1's content gate is closed

Build A is the gate build: 16 base roots plus mollusk, crustacean,
cutting_implement, tableware, power_tool. Build B is held: its only unique wins
ride on `color`, which introduces the leak below. Recorded with a correction:
the previous handoff's A/B table said build A gave 1, 3, 4, 3, 3 working hidden
categories across group sizes 5 to 9. The committed artifact says 5, 1, 0, 0, 0,
and a rebuild reproduces the artifact byte for byte. Build A unblocked size 5,
not sizes 6 to 9.

### The hidden reading must be the only one on the board

Chosen over a blacklist of known overlapping pairs, as decided, and implemented
as `has_no_category_leak` in `puzzlegen/games/grouping/generate.py` with the
refusal reason `category_leak`.

The rule: no tile outside the hidden group may carry the hidden category, and no
visible group's category may be an ancestor of the hidden category. The first
clause is uniqueness of the hidden answer stated directly and it subsumes the
hierarchy case, since a visible group whose category sits under the hidden one
has `group_size` tiles all carrying it. The second clause is unreachable in
practice: a visible category every hidden word carries is also carried by hidden
words in the other three groups, which `has_no_cross_membership` refuses first.
Both are read off `EntityView.type_ids`, already the transitive closure, so the
generator needs no graph handle.

Nothing else in the system refuses this. `has_no_cross_membership` compares the
four visible categories with each other and the hidden category is deliberately
not among them. `verify.py` counts partitions on the lexical axis only and
excludes the overlay axis by design, because counting it would make every board
ambiguous by construction. Generation is therefore the only place the hidden
reading's uniqueness can be established.

Mirrored in `tools/overlay_coverage.py`, where the filler pool now excludes
hidden-category carriers and a board that can only be filled with them is
reported as `a_filler_tile_is_also_a_hidden_word`, kept distinct from
`too_few_other_tiles_to_fill_the_groups`. Cost measured on three builds (A, A2,
B): zero. Coverage is identical with and without the gate, and the gate did not
fire once on real content. It is a guard against a hole, not a change in
capacity.

Three fixtures in `tests/unit/test_overlay_coverage.py` gained a sixth cat. With
five, every filling of the cats group puts `puma` on the board, which answers
"also a verb" as well as the chosen five do, so those fixtures described boards
the gate correctly refuses. The tests are about whether a new word reaches a new
home, so the fixture gained a spare tile rather than the gate gaining an
exception.

### The colour leak is NOT closed, and the decided rule cannot close it

Measured, not reasoned: build B with the gate still reports the board
`word that is also a color` over visible groups `airplane`, `finch`, `citrus`,
`pink`. Two facts explain it.

Overlay categories are taxonomy roots. `overlay.color` has no parents and no
edge to the lexical `color` category, so no ancestor or descendant relation
exists between the hidden category and `pink` for any rule to find. The handoff's
recommended rule, read literally against the graph, has nothing to fire on.

And the `pink` group can be filled without a single extra tagged colour word:
the curator tagged 13 words as "also a colour" and the `pink` subtree holds
others that are colours in the lexical sense but carry no overlay tag. The
uniqueness clause sees no stray carrier and passes the board.

What the board gives away is the visible *label*. "Find the word that is also a
colour", with one visible group called `pink`, is unfair whether or not a sixth
tagged word is on the table.

Closing it needs the missing link supplied as content: an optional `anchor` on an
overlay category naming the lexical category it means (`overlay.color` ->
`category:color`, oewn-04963771-n). The rule then becomes the decided one and is
general: a visible group may not be the anchor, nor an ancestor or descendant of
it. Not blocking, because build A ships without `color`. Open; see the handoff.

### Batch 2 of the lexicon roots, exported and measured

Nine roots, all 22 previously unsupplied overlay words recovered, `overlay_missing`
down from 24 to 2 (`coral`, `coffee`). Feasible hidden categories by group size
go from 5, 1, 0, 0, 0 to 7, 6, 2, 1, 0. Detail, the three corrections to the
section 5 analysis, and the `wordnet-tool` mislabel are in
`docs/candidate-roots.md` section 6. Reproduced by `docs/rebuild-with-batch2.sh`.

Established while doing it, and worth keeping: a snapshot built with
`--dev-embeddings` and a freshly exported frequency table reproduces
`build/coverage-A.json` byte for byte. Coverage applies no embedding gate, so
coverage work needs no model and no network; only a published snapshot does.

### Game 2 gets its own curated taxonomy, because the lexicon has no members

Measured first: on build A2 at medium frequency, `letters_unplaceable` is zero
in every cell of 6 to 12 tiles by 2 to 4 blanks. The letter matching the
requirements doc spends four sections on is never the binding constraint. One
WordNet category in 7,050 has six or more eligible members, and the two
playable categories are `baby grand` and `police van`.

So the naming work in requirements 4.3 was the wrong lever, and shortening
names would have bought nothing. The route taken instead, decided by the
owner: pick categories a player recognises, get six to eight common words into
each, settle the name afterwards.

`games/letters/content/letters.curated.json`, thirty categories and 218
entities, imported with `--curated ...:letters`. No new mechanism: that flag
exists in `tools/build_snapshot.py` for a game's own taxonomy, and this is its
first caller. Entity identity is by lemma, so a member the graph already holds
takes one more membership rather than becoming a duplicate: the entity total
goes from 16,710 to 16,812, so 102 of the 218 members are new and 116 merged.
Separately, 111 of the 218 carry a membership outside the new taxonomy, 76 in
`wordnet`, 33 in both `wordnet` and `overlay` and 2 in `overlay` alone. The two
figures differ because merging is by lemma and does not require the entity it
merges with to carry a membership. That entity total is the only figure game 1
measures that this file moves; no group, cover or refusal changes.

All thirty are playable at six tiles and the two-blank floor, twenty at eight
tiles, none at ten or twelve. Ten and twelve are a decision about how large a
category gets, not a gap: a ceiling of eight members cannot fill them.

Worth keeping, because it was not obvious: the medium band is `COMMON` and
`UNCOMMON`, zipf 3.0 to 5.0, so a *very* common word is ineligible at medium.
`dog`, `car` and `water` are all too common to be tiles there. A frequency
filter over a lexicon subtree therefore cuts from both ends, which is part of
why it leaves almost nothing behind.

The third invariant in `tests/unit/test_letters_categories.py` -- that a
category's own members can spell its own name at two blanks -- caught three
categories as authored. `fruit` had no member holding an f, an i or a t;
`garment` none holding a g, an m or an n; `vehicle` one e and no h. All three
were fixed by changing members rather than the name, and `gemstone` and
`currency` were renamed to `gem` and `money` only after their members were
settled, both being eight letters where six tiles admit seven.
