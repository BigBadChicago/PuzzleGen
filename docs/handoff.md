# PuzzleGen Handoff

First thing a new conversation reads. Verify repository claims directly (file
sizes, what a lexicon resolves to, whether a build runs) rather than trusting a
filename or commit message; this project has been burned by stale committed
artifacts more than once.

Last verified: 2026-10-10, against the repo on the user's computer.

## FIRST THING: push the pending commit

One local commit is not on origin: `e02f7ca` (the `.gitattributes` line-ending
fix). The linked-computer shell has no GitHub push credentials, so it could not
be pushed. Run, in a terminal where GitHub is authenticated (Codespace or local):

`cd ~/Documents/Code/PuzzleGen && git push origin main`

origin is at `4cd9e1b` (seeds + fish depth-8, already pushed, so coverage A and
B are reproducible there). After the push, origin also has the line-ending fix
and the repo is fully current.

## Where things stand

Phase 7 (game 1, the grouping game) is functionally closed. The project is at a
deliberate fork: it is becoming a multi-game engine, and the next work is the
first shared subsystem plus the first second game.

### Resolved (all committed; commit ids noted)

1. **Gap 2 (every board identical) was a code defect, not content thinness.**
   The prior handoff said the generator offered one candidate and told the next
   session NOT to touch candidate selection. Wrong, and verified wrong. Root
   cause: `_ordered` in `puzzlegen/games/grouping/generate.py` used a fixed
   total order over content properties with candidate id as the final
   tie-break. Nothing varied by day, and the engine takes the first candidate
   that verifies (a lookup, not a choice), so every day served at one size
   produced a byte-identical board. Fixed by (a) round-robin draining of
   hidden-group candidate streams with a per-group share, and (b) a day-derived
   shuffle inside each rank (`rng.derive("candidate-order").shuffled(...)`)
   before the stable sort, so the day key reaches selection while enrichment and
   temptation still rank. Determinism preserved. Tests:
   `tests/unit/test_grouping_variety.py`. (commit `453be84`)

2. **`generate_days.py` reported the drawn size, not the served size.** A day
   tries its drawn size first, then falls back; the tool read the draw, so a run
   leaning entirely on fallback looked like full size coverage (the "30/30
   across sizes 5-9" claim rested on this). Fixed to read
   `outcome.puzzle.payload["group_size"]` and count drawn vs served separately.
   (commit `453be84`)

3. **The lemma-collision report was a false-alarm generator.** It claimed a
   later sense of a merged category loses its parents; the builder unions them
   and refuses only a cycle-closing edge. 14 overlay words were reported as
   losing a parent; a real build showed 0 do. `tools/report_lemma_collisions.py`
   rewritten to mirror the builder (union parents, refuse only
   ancestor/descendant edges, follow file order, union hypernym edges across
   files, loop guard). New test `tests/unit/test_collision_report_matches_builder.py`
   builds the same files both ways and fails if they disagree. (commit `e9313ed`)

4. **New lexicon roots committed and pushed (commit `4cd9e1b`).** Seven roots
   that close most overlay coverage gaps were generated in a prior session but
   never committed, so the repo could not reproduce `coverage-A` or
   `coverage-B`. Now on origin: `mollusk`, `crustacean`, `cutting_implement`,
   `tableware`, `power_tool`, `color`, `drug_of_abuse`, plus `fish` re-exported
   at depth 8 (617 synsets, was 498 at depth 6; the only root truncated at depth
   6). `wordnet-shellfish` was dropped (it holds food senses, not the animals;
   `mollusk` + `crustacean` replace it). `docs/candidate-roots.md` documents the
   full analysis and a second batch for the remaining 22 unsupplied overlay
   words. `docs/rebuild-with-new-roots.sh` reproduces the A/B builds.

5. **Line endings fixed (commit `e02f7ca`, NOT yet pushed; see top).** Added
   `.gitattributes` (`* text=auto eol=lf` + binary guards + `*.sh` LF), so the
   Windows checkout (CRLF) no longer shows every tracked text file as modified
   against the LF blobs. Also widened `.gitignore` sqlite rule from `*.sqlite3`
   to `*.sqlite*` (so `content/graph.sqlite` can never be committed by
   accident), added `*.bak`, untracked the stray `.gitignore.bak`. Working tree
   is clean. The ~50 files still showing `w/crlf` in `git ls-files --eol` are
   the working copy only; git stores LF and converts on commit. Do not "fix"
   them.

### The A/B coverage result (the gate)

Build A = 16 base roots + mollusk, crustacean, cutting_implement, tableware,
power_tool. Build B = A + color + drug_of_abuse. Difficulty MEDIUM. Distinct
hidden categories reaching all four homes, by board size:

```
          size5  size6  size7  size8  size9
build A     1      3      4      3      3
build B     1      3      4      3      4
```

**Recommendation: ship A.** It unblocks board variety across sizes 6-9 with four
working hidden categories. **Hold B**: its only unique wins (`thing that can be
smoked`, `word that is also a person's name`) ride on `color`, which introduces
a real defect. Confirmed in coverage-B: for the hidden group `word that is also
a color`, the visible homes include `chromatic color`, `pink` and `red`. A board
asking the player to find the colour word, with one visible group literally the
colours, is broken. The generator's leak check misses it because the visible
group is a sibling category, not the hidden category itself.

## Open decisions (what the next conversation resolves)

1. **The color leak.**
   - Rule (recommended): a visible group may not be an ancestor or descendant of
     the hidden category. General, prevents future leaks of this class.
   - Blacklist: exclude `chromatic color` and children as visible groups only
     for the colour hidden group. Narrow, leaves the hole open for the next
     overlapping pair.
   Neither has a test yet; the choice determines the test.

2. **Ship A, yes/no.** If yes, A is the gate build and game 1 is closed.

3. **The surface form subsystem (the fork).** The user decided this is an
   engine-level subsystem, not a helper, bigger than one game. It defines what
   the letters of an entity are (case folding, diacritics, spaces/hyphens,
   multi-word collapse), which surface form counts (primary lemma vs a shorter
   true synonym, e.g. `bowed instrument` for `bowed stringed instrument`), a
   stable inspectable entity-to-letters mapping, and a precomputed index
   (character queries run per candidate every daily build). Sits beside the
   content graph, adds no new content-service operation, games call it.
   `missing_letters` is its first client. **Pending: build the subsystem before
   implementing missing_letters (platform-first, the user's lean), or inline and
   extract later.**

4. **`missing_letters` (second game, first subsystem client).** Specified in
   `missing-letters-requirements.docx` (delivered to the user; fold into `docs/`
   if wanted in-repo). Settled: SOUND_INCOMPLETE with a stated search bound and a
   pinned word list; a mark must occur in its own tile's word; multi-word
   category names permitted with common names favored and shorter-synonym
   substitution allowed; when difficulty wants more blanks than the data forces,
   leave a could-have-been-marked tile unmarked to manufacture the extra blank
   (and record per board which blanks were forced vs manufactured); at least two
   blanks always; free anagram; one mark per tile max; not every tile marked.
   Game id `missing_letters`. No coverage tool yet; an analogue of
   `measure_coverage.py` is the natural next artifact before implementation.

5. **Second batch of roots** (22 still-unsupplied overlay words: beaver, broom,
   cheese, coconut, comb, drill, erica, hemp, hoe, holly, hop, hops, iris,
   jasmine, mate, mower, onion, rake, ram, rodent, rush, walnut). Candidates and
   ids in `docs/candidate-roots.md` section 5, led by `tool` (recovers 6) and
   `rodent`. `mate` (the beverage) is unreachable by any root; it needs an
   explicit synset id or removal from the overlay.

## Standing cautions

- **The recurring failure mode is a diagnostic that recomputes a value instead
  of reading what the engine actually did.** Three instances: a hand-copied
  threshold that drifted, `generate_days.py` reading the drawn size, the
  collision report's parent-loss logic. When a tool and the engine could
  disagree about a number, make the tool import the real constant or read the
  real output, never re-derive it.
- Never glob `content/seeds/wordnet-*.lexicon.json`; name exact roots.
  `wordnet-mini` is an 8-synset test fixture a glob would wrongly include.
- Line endings are now handled by the committed `.gitattributes`. `git status`
  on the Windows checkout is clean. If a mass of modified text files appears
  again, the attributes file was lost or bypassed; do not commit that churn.
- Git on the linked-computer shell has no commit identity by default (set
  repo-local to the user) and no push credentials, and cannot delete its own
  `.git` lock/temp files without folder delete permission (grant it at the start
  of a git-heavy session) or stale `.lock` files make later git commands fail.
- The test suite needs Python 3.11+ (`StrEnum`). The device shell has only 3.10
  and the repo `.venv` is a Windows venv, so the suite cannot run from the
  linked-computer shell; run it in the Codespace. Last full run: 2,262 passing,
  3 expected content-gap failures (overlay words still unsupplied, the content
  tracker in `test_overlay_seed.py`, correct to stay red until the second root
  batch lands).

## Reading order

1. What the person says now.
2. Files attached to the conversation.
3. Project memory and docs.
4. Repository files (lowest priority; can be stale).

## Response conventions

No preamble, recap, or filler. Decisions and open questions in lists at the end.
Every multi-option choice gets a plain-language explanation plus a benefits
versus costs weighing before any recommendation. State anything over ~300 lines
before producing it and wait. CLI commands on a single line, no continuations.
No dashes except within a word.
