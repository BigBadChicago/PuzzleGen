# PuzzleGen Handoff

This file is overwritten at the end of every conversation in the Puzzle
Universe project. It is the first thing a new conversation reads. It answers
three questions: where things stand, what you need to do before the next
conversation starts, and what the next conversation should do first.

## Where things stand

- Phases 1 through 6 are complete. Phase 7's batch list is complete: 1b, 1c,
  1d, 1a, and batches 2 through 6. Batch 7 is open and described below.
- 1757 tests pass on Python 3.14 and 1747 on 3.12, with 7 and 17 skipped
  respectively. The skips are the tests needing `wn`, its lexicon data, or the
  built snapshot; they run in the Codespace.
- **A real snapshot exists.** Five WordNet roots at depth 6, real MiniLM
  embeddings, real corpus frequencies: 14,720 entities, 6,526 categories,
  10,219 frequency scores, 15,393 relationships, 36,639 records activated.
  `docs/snapshot-build.md` is the runbook and its figures are measured rather
  than estimated.
- The overlay seed was re-authored against that snapshot. Fifteen categories
  of ten, 144 unique entities, and **all 144 sit in both a lexical and an
  overlay category**, against ten of 144 in the first seed.
- `puzzlegen/games/grouping/` is complete: descriptor, content, generate,
  assemble, verify, difficulty, play, plugin. Five broken fixtures live in
  `tests/support/broken_grouping.py` and are failing by design.
- Python 3.14 is the Codespace's version and the suite is clean on it. Python
  3.13 and later turn an unclosed sqlite connection into a failure in an
  unrelated test, which cost two rounds to diagnose;
  `tests/unit/test_connection_hygiene.py` now catches it at the command that
  caused it.

## Manual steps required before the next conversation begins

1. Nothing blocking. The snapshot is built and committed, and the suite is
   green.
2. If `content/graph.sqlite` needs rebuilding, delete it first. A build merges
   into whatever the store already holds, and an older sense can win over the
   one the export files produce; `tools/build_snapshot.py` now refuses rather
   than discovering this at its last step.
3. To run the snapshot tests, set `PUZZLEGEN_SNAPSHOT_TESTS=1`. They are opt-in
   because they were slow, not because they are unimportant. Batch 7's first
   measurement should be whether they still are.

## What the next conversation must do first

Before any phase work: ask whether the manual steps above are complete. Accept
exactly one of two replies:

- **`Steps Complete`** — proceed with batch 7 as scoped below.
- **`Detour {filename.md}`** — stop, read the named file (must be attached to
  the chat or a path under `docs/` in the repository), and ask what to do with
  it before touching phase work. Do not resume phase work until the detour is
  explicitly closed.

If the reply is neither of these two forms, ask for it to be restated as one of
them. Do not guess which was intended.

## Batch 7: a real board, or a measured reason there is none

The day resolves and the game does not yet produce a board. This is the whole
of batch 7, and it is a content-shape problem rather than a bug.

**What was measured on 2026-09-28, group size 5:**

| Step | Before batch 7's fixes | After |
|---|---|---|
| One overlay `find_groups` | 215 s, 0 groups | under 1 s, 5 groups |
| Whole day, three requirements | timed out past 280 s | 3.9 s |
| `visible_groups` | — | 40 groups |
| `hidden_groups` | — | 24 groups |
| `hidden_groups_two_axis` | — | 0 groups, all 24 rejected `NO_SECOND_AXIS` |
| Candidates generated | — | **0** |

Two content service fixes are already in and tested: group search enumerates
within categories rather than across the pool, and membership edges are read
once per service rather than once per entity.

**The open problem, now measured rather than guessed.** Instrumenting the
combination search moved the failure twice and landed on a structural one.

| Fix | What the day then said |
|---|---|
| counters added | `not_disjoint=120`: the pool returned forty groups that were different five-subsets of the same few categories |
| round robin across categories | `shortlist_too_small=24`: only 19 distinct categories among forty groups, and fewer than four of them contain a hidden member |

The numbers behind the second row:

- The visible pool covers 102 tiles of 14,720.
- The best hidden group is covered **1 of 5** by that pool.
- 144 overlay entities are touched by 157 lexical categories, out of 6,511.

The overlay is one percent of the graph, so visible groups sampled from the
lexicon almost never contain overlay members. A board needs four visible groups
that between them hold all five hidden members; the current pool holds one.

**What was then built, and what it moved.** `ContentQuery` gained
`intersects_taxonomy` with `minimum_intersecting_members` (default 1), and the
visible query now requires each group to carry at least one overlay member.

| State | Visible groups | Generation |
|---|---|---|
| no constraint | 40, in 19 categories | `shortlist_too_small=24` |
| every member in the overlay | **0** | nothing to try |
| at least one member | 11 | `shortlist_too_small=22`, `not_disjoint=10` |

The strict reading is impossible and the measurement says why: **no lexical
category in the snapshot contains even three overlay words**, and only seven
contain two. Game 1's overlay content was authored one lexical double meaning
at a time, so its members are scattered across near-leaf WordNet categories
like `violin` and `heather` rather than clustered. This is a fact about game
1's specific overlay content, not about the overlay mechanism: `"overlay"` is
a general cross-cutting taxonomy type, stored and queried like any other, and
`intersects_taxonomy` is a generic query constraint with no wordplay built
into it. A future game with a differently-authored overlay (or a non-lexical
one entirely) would not inherit this clustering problem; it is specific to how
these 144 words were chosen.

**Where batch 7 now stands.** The frequency-filter measurement was run and it
is not the fix. It is part of the problem, but a small part, and the
structural constraint underneath it does not go away when it is removed.

Measured directly: a lexical category is usable for a board only if it has at
least `group_size` total members **and** at least one of them is also an
overlay entity. Counting those categories against the real snapshot, with no
frequency filter applied at all:

| group_size | usable categories |
|---|---|
| 5 | 10 |
| 6 | 5 |
| 7 | 3 |
| 8 | 1 |
| 9 | 0 |

A board needs four *disjoint* usable categories whose overlay members between
them cover one whole hidden group. Ten is already too few for that (the batch
7c combinatorial search showed as much), and it gets worse, not better, as
group size grows. This holds before any frequency band is applied — dropping
`difficulty_band` from the visible query does not change these counts, because
frequency was never removing overlay-touching categories at anywhere near this
rate (142 of 144 overlay entities pass the MEDIUM band on their own). The
frequency filter does make a secondary dent — it drops the size-5 count from
10 to 3 — so it is worth loosening regardless, but it is not the blocker.

The real lever is the same one flagged in the last handoff, now with no
remaining ambiguity about whether it is necessary:

**Grow the overlay so more of its categories cluster with 5+ lexical
members.** The proposer exists for this and has never been run against real
content. With real embeddings now present it can suggest lexicon words for
existing overlay categories, and words drawn from the lexicon land in
categories that already have members — which is exactly what the count above
needs more of.

```bash
python tools/propose_overlay.py --db content/graph.sqlite \
    --batch overlay-2026-09-29 \
    --manifest content/proposals/overlay-2026-09-29.json
python tools/review.py queue --db content/graph.sqlite \
    --manifest content/proposals/overlay-2026-09-29.json
```

Each proposal needs ten accepts on ten separate days to promote, so this is
slow and should start now rather than after more code is written. Loosening
the frequency filter is still worth doing in parallel — it is cheap and it was
measured to help a little — but budget the overlay-growth work as the thing
that actually unblocks generation.

## Also open, and agreed but not built

- **A similarity opt-out on `ContentQuery`.** Agreed in conversation and not
  yet implemented. The overlay's members are unrelated by construction and a
  game needs a way to say so. Note that the 20,000 rejections that prompted
  this were mislabelled: they were `NO_SHARED_CATEGORY`, not a similarity gate,
  which is now a separate reason code. The opt-out is still worth having before
  a game needs it; it is no longer urgent.
- **Rerun the seven commands if the lexicons change.** `terms.txt` and
  `texts.txt` are derived from the merge, not from the files, so re-exporting a
  lexicon invalidates `frequency.json` and `embeddings.json`.
- **The embeddings file is 43 MB.** Compact format at four decimals, about half
  of what indented JSON cost. A larger snapshot will need splitting or a binary
  format before it reaches GitHub's 100 MB limit.
- **`[project.scripts]` in `pyproject.toml` points at a module that does not
  exist.** `pip install -e .` puts a `puzzlegen` command on the path that fails
  on invocation. Remove the two lines or build the CLI.
- **`search_cost` at weight 0.15** in `difficulty.py` is the feature trusted
  least. Revisit once real boards score.
- **Client delivery is decided but unbuilt.** A rolling five days, the current
  day plus two past and two ahead, refreshed after a played game while
  connected or on next connection. A corrected day always overwrites a cached
  one: a correction is by definition more correct than what it replaces, and a
  client holding a known-wrong board is worse than a client that re-downloads.
  Reconciling a device offline more than two days is deferred, deliberately,
  and is not a blocker for a first release.

## Beyond phase 8: the shared-graph thesis and what it commits to

Two things were decided in conversation and belong here because they change
what "done" means for the project, not just for game 1.

**Five games is the validation target for the shared-graph design**, not one.
Game 2 alone tests whether a second plugin can be built without touching the
engine or game 1; it does not test whether the graph and engine generalize
across genuinely different game *shapes*. That test needs several games, and
the number chosen is five.

**The engine must support number and math games, and games built on symbols
and images, not only word and language games.** This is a real constraint on
phase 8 and beyond, and it should shape decisions made there rather than being
discovered after the fact:

- The current graph schema (`Entity`, `Category`, `Relationship`, embeddings,
  frequency bands) was designed around lexical content. A number game's
  "entities" are integers or numeric relationships (primes, multiples,
  sequences); a symbol or image game's "entities" may have no
  `canonical_name` worth showing as text at all, and may need a different
  kind of provider than `WordNetLexiconProvider` or `CuratedJSONProvider`.
- `EntityView`, `GroupView`, and the similarity/frequency machinery in
  `content/service.py` are the parts most likely to assume "content is a
  word." Whichever game is built after game 1 that is *not* a word game will
  be the first real pressure test of that assumption, and it should probably
  come before game 5, not after — finding out the schema needs to change once
  four word-adjacent games already depend on it is expensive.
- This does not mean redesigning the graph now. It means: when choosing game
  2 or game 3, deliberately pick one that is not a word game, specifically to
  surface what the content layer is missing, rather than picking the next
  easiest word game and deferring the harder question to game 5.
- The primary-plus-crosscutting storage pattern itself is not game 1's and is
  not a game at all. It is a data model: an entity can belong to categories in
  a primary taxonomy and, separately, to categories in any number of other
  taxonomies that cut across the primary one, with no requirement that either
  side be about words. See the new section near the top of phase 3 in
  `architecture.md` for the full definition and a worked number-game example.
  Game 1 is the first consumer of that storage, and it happens to build a
  "spot the hidden pile" word puzzle on top of it. A future number or symbol
  game consumes the identical storage and query shape
  (`intersects_taxonomy`, `FIND_INTERSECTING_GROUPS`) with its own taxonomies
  and its own game mechanic, inheriting nothing from game 1 except the storage
  plumbing. Earlier drafts of this file, and earlier code comments, described
  the pattern using game-1-specific language ("hidden group," "word play,"
  "second meaning"); read those as describing game 1's particular use of the
  pattern, never as the definition of the pattern itself.

## Things phase 7 got wrong that are worth not repeating

- A package that exports a function shadowing a submodule breaks
  `from . import <name>` at the first call, with a message that does not
  mention shadowing. `plugin.py` imports functions directly for this reason.
- The engine's announcement events and placeholders are closed sets. A
  descriptor that invents either is refused at registration, and the test that
  catches it is the one that runs the real gate rather than re-checking the
  properties by hand.
- Three separate incidents came from files not landing at the right path, twice
  because two batches shipped files with the same name in different
  directories. Copy by full path.

## Standing project conventions (apply regardless of phase)

- No preamble, no recap, no filler; decisions and open questions stated in
  lists at the end of each response.
- Every multi-option question or choice gets a plain-language explanation
  (assume a middle-school reading level) plus a benefits-versus-costs weighing
  for each option, before any recommendation.
- Files longer than ~300 lines: state what is about to be produced and wait for
  confirmation before generating it.
- Reading order for any single reply: conversation, then attachments, then this
  project's file context, then the connected repository, in that priority
  order.
