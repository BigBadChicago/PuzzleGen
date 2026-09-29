# PuzzleGen Handoff

This file is overwritten at the end of every conversation in the Puzzle
Universe project. It is the first thing a new conversation reads. It answers
three questions: where things stand, what you need to do before the next
conversation starts, and what the next conversation should do first.

## Where things stand

- **`docs/decision-log.md` records why, not just what.** Every real decision
  point from this conversation (content pipeline choices, the overlay
  re-authoring, the axis switch design, the tolerance rule, the game 2
  ranking, both conceptual corrections) is there with its options and
  reasoning. Read it before re-deciding something that looks unresolved; the
  reasoning may already be settled.
- Phases 1 through 6 are complete. Phase 7's code is complete: the batch list
  (1b, 1c, 1d, 1a, 2 through 6), plus batch 7's content-service fixes. Phase 7's
  *content* goal, a real board generating from real content, is not complete,
  and cannot finish inside a conversation. See "The one thing left in phase 7"
  below; it is an external, multi-day task, not a code task.
- 1769 tests pass on Python 3.14 and 1759 on 3.12, with 7 and 17 skipped
  respectively. The skips need `wn`, its lexicon data, or the built snapshot;
  they run in the Codespace.
- **A real snapshot exists and is committed.** Five WordNet roots at depth 6,
  real MiniLM embeddings, real corpus frequencies: 14,720 entities, 6,526
  categories, 10,219 frequency scores, 15,393 relationships, 36,639 records
  activated. `docs/snapshot-build.md` is the runbook; its figures are measured.
- The overlay seed was re-authored against that snapshot: 15 categories of 10,
  144 unique entities, all 144 sitting in both a lexical and an overlay
  category. This is still not enough for a board to generate; see below.
- `puzzlegen/games/grouping/` is complete: descriptor, content, generate,
  assemble, verify, difficulty, play, plugin. Five broken fixtures in
  `tests/support/broken_grouping.py` fail by design, proving the engine's
  gates actually reject bad games.
- The content service now supports `intersects_taxonomy` /
  `minimum_intersecting_members` on `ContentQuery`, and this is documented as
  a general storage-and-query pattern, not a game-1-specific one. See "The
  primary-plus-crosscutting storage pattern" near the top of phase 3 in
  `architecture.md`, and the comment on `intersects_taxonomy` in
  `content/query.py`. Read that section before designing game 2; it includes
  a worked non-word example.
- Python 3.14 is the Codespace's version and the suite is clean on it. Python
  3.13+ turns an unclosed sqlite connection into a failure in an unrelated
  test; `tests/unit/test_connection_hygiene.py` catches this at the command
  that caused it, going forward.

## The one thing left in phase 7: not enough words cluster together

A board needs four visible groups whose members, between them, hold all five
members of one hidden overlay group. Measured directly against the real
snapshot: a lexical category is usable at all only if it has at least
`group_size` total members **and** at least one member also in the overlay.

| group_size | usable categories |
|---|---|
| 5 | 10 |
| 6 | 5 |
| 7 | 3 |
| 8 | 1 |
| 9 | 0 |

Ten is not enough to assemble four disjoint ones, and it gets worse as group
size grows. This is true with no frequency filtering at all; frequency is a
small secondary factor (it drops the size-5 count from 10 to 3), not the
cause. The cause is that the overlay's 144 words were each chosen for a single
double meaning, one at a time, so they scatter across near-leaf WordNet
categories (`violin`, `heather`) instead of clustering into categories that
already hold five or more members.

**The fix is to grow the overlay with words the proposer suggests from the
lexicon itself**, because those land inside categories that already have
members, which is exactly what the count above needs more of. This cannot be
rushed: `puzzlegen/content/review.py`'s design requires ten accepts on ten
separate days before a proposed word activates. That is a deliberate
anti-abuse property of the review system, not a bug, and it is why this task
has to happen outside a single conversation.

### External task checklist (do this between conversations, not in one)

1. **Run the proposer**, once, now:
   ```bash
   python tools/propose_overlay.py --db content/graph.sqlite \
       --batch overlay-2026-09-29 \
       --manifest content/proposals/overlay-2026-09-29.json
   ```
2. **Review and accept**, using the manifest to see why each candidate was
   suggested:
   ```bash
   python tools/review.py queue --db content/graph.sqlite \
       --manifest content/proposals/overlay-2026-09-29.json
   ```
3. **Repeat step 1 with a new `--batch` id on nine more separate days**, each
   time reviewing and accepting the candidates that genuinely belong. A word
   needs ten accepted days to activate; a batch run twice in one day still
   only counts as one day toward that count.
4. **After the tenth day, rerun the category-count measurement** (the table
   above) to check whether enough categories now have 5+ members. If not,
   keep proposing; if so, rerun a day's generation end to end and confirm a
   board actually assembles, verifies unique, and scores.
5. **Only once a real board generates end to end**, phase 7 is actually done.
   That is the trigger for phase 8, not the passage of ten days by itself.

### If the wait is unacceptable

Two options exist if ten real days is too slow, both discussed and both
carrying a real cost, not free shortcuts:

- **Lower the accept threshold** (`ACCEPT_THRESHOLD` / `ReviewService`'s
  `threshold` argument) for this one growth push, then restore it. Weakens the
  anti-abuse property for whatever window it is lowered.
- **Hand-author more overlay words directly** (skip the proposer, edit
  `content/seeds/overlay.curated.json` the way the current 144 were chosen),
  choosing words specifically for cluster size this time: check a candidate
  word's lexical category size *before* adding it, not after. Faster, but
  loses the "a human vetted this against real content" property the proposer
  gives you.

Neither was chosen; both are recorded here so the choice is made deliberately
rather than by default.

## Game 2 candidates (decided: pursue in this order)

Chosen specifically to pressure-test the "not only word games" requirement
before four more word-shaped games make a schema change expensive.

| Order | Option | Why | Schema impact |
|---|---|---|---|
| 1 | **Symbol sorting** (glyphs, no dictionary meaning) | Best effort-to-signal ratio: cheapest way to find out whether `content/service.py`'s frequency/embedding machinery can genuinely go unused, not just unused by coincidence | None |
| 2 | **Image matching** (real images as tiles) | Best pressure test, and the one explicitly required. Cannot be faked the way symbols can; forces a real answer on whether `Entity` needs an image reference field and what `PresentationModel` sends when there is no text | One new field: an image reference on `Entity` |
| 3 | **Number grouping** (Connections-shape, numeric content) | Easiest to build: reuses `puzzlegen/games/grouping/` almost unchanged, content swapped | None |
| 4 | **Math relationships** (`factor_of`, `multiple_of` as real edges, not category membership) | Tests a dimension none of the above touch: querying by relationship instead of by shared category, which nothing has done yet except `is_a` | Query layer: a new operation |
| 5 | (open) | Whichever real word game makes sense once the graph has more content | — |

This is the five-game validation set for the shared-graph thesis. Order 1 to
4 is deliberate: cheapest non-word signal first, most required pressure test
second while that signal is fresh, a safe generalization check third, and the
one architectural gap (relationship-based querying) closed fourth, before
calling the thesis validated.

## Manual steps required before the next conversation begins

1. Work through the external task checklist above. This is the actual
   blocker; nothing else is.
2. If `content/graph.sqlite` needs rebuilding for any reason, delete it first.
   A build merges into whatever the store already holds; `tools/build_snapshot.py`
   refuses rather than silently mixing an old sense in.
3. If any lexicon file changes, rerun the seven commands in
   `docs/snapshot-build.md` in order; `terms.txt` and `texts.txt` are derived
   from the merge, not from the files, so a stale pair breaks the last step.

## What the next conversation must do first

Before any phase work: ask whether the manual steps above are complete.
Accept exactly one of two replies:

- **`Steps Complete`** — check whether a board now generates end to end
  (external task checklist, step 5). If yes, phase 7 is closed; open phase 8
  and start with game 2 candidate 1 (symbol sorting) from the table above. If
  not yet, continue the external task checklist; do not start phase 8 work on
  the strength of "some days have passed" alone.
- **`Detour {filename.md}`** — stop, read the named file (must be attached to
  the chat or a path under `docs/` in the repository), and ask what to do with
  it before touching phase work. Do not resume phase work until the detour is
  explicitly closed.

If the reply is neither of these two forms, ask for it to be restated as one
of them. Do not guess which was intended.

## Also open, agreed, not built, not blocking phase 7

- **A similarity opt-out on `ContentQuery`.** Agreed in conversation, not yet
  implemented. Worth having before a game needs to say "these are deliberately
  unrelated," but no longer urgent: the 20,000 rejections that originally
  motivated this were mislabelled (they were `NO_SHARED_CATEGORY`, now its own
  reason code, not a similarity gate).
- **The embeddings file is 20.7 MB**, measured against the committed file, not
  the 43 MB this document previously claimed. Compact format at four decimals
  already halved it from indented JSON. A larger snapshot (from game 2's
  content, or from growing the overlay) will need splitting or a binary format
  before it reaches GitHub's 100 MB limit, but the headroom is roughly five
  times what this entry assumed, so it is not near-term.
- **`[project.scripts]` in `pyproject.toml` points at a module that does not
  exist.** `pip install -e .` puts a `puzzlegen` command on the path that
  fails on invocation. Remove the two lines or build the CLI.
- **`search_cost` at weight 0.15** in `difficulty.py` is the least-trusted
  difficulty feature. Revisit once a real board's score can be sanity-checked
  by a person actually playing it.
- **Client delivery is decided but unbuilt.** A rolling five days (current day
  plus two past, two ahead), refreshed after a played game while connected or
  on next connection. A corrected day always overwrites a cached one: a
  correction is by definition more correct than what it replaces. Reconciling
  a device offline more than two days is deferred deliberately and is not a
  blocker for a first release.

## Things phase 7 got wrong that are worth not repeating

- A package that exports a function shadowing a submodule breaks
  `from . import <name>` at the first call, with no error message that
  mentions shadowing. `plugin.py` imports functions directly for this reason.
- The engine's announcement events and placeholders are closed sets. A
  descriptor that invents either is refused at registration; the test that
  actually catches this runs the real gate, not a re-check of the properties
  by hand.
- Three separate incidents came from files landing in the wrong path, twice
  because two batches shipped same-named files in different directories.
  Copy by full path, always.
- The storage pattern behind game 1's hidden group (an entity in categories
  from more than one taxonomy at once) got described in game-1 language
  several times before the framing was corrected. It is not a word-play
  concept and it is not a game. It is a data model. See the phase 3 addition
  in `architecture.md`. Read `intersects_taxonomy` and its comment in
  `content/query.py` as the actual contract; treat "hidden group," "word
  play," and "second meaning" anywhere else as game 1's specific use of it.

## Standing project conventions (apply regardless of phase)

- No preamble, no recap, no filler; decisions and open questions stated in
  lists at the end of each response.
- Every multi-option question or choice gets a plain-language explanation
  (assume a middle-school reading level) plus a benefits-versus-costs
  weighing for each option, before any recommendation.
- Files longer than ~300 lines: state what is about to be produced and wait
  for confirmation before generating it.
- Reading order for any single reply: conversation, then attachments, then
  this project's file context, then the connected repository, in that
  priority order.
