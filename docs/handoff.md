# PuzzleGen Handoff

First thing a new conversation reads. Verify repository claims directly (file
sizes, what a lexicon resolves to, whether a build runs) rather than trusting a
filename or commit message; this project has been burned by stale committed
artifacts more than once. Last session found two: a coverage table in this file
that disagreed with the artifact it claimed to summarise, and a lexicon file
named for a root it does not hold.

Last verified: 2026-10-12, against the full suite and a freshly rebuilt
symbolic snapshot, after the truncation gate landed.

## Where things stand

Five games are installed and every suite is green. Games one and two are the
lexical pair; games three, four and five are over content that is not words,
which is what phase 9 was for.

Nothing is pending on origin beyond the branch this session pushed.

**The platform documents exist, in `docs/platform/`.** Sixteen documents written
against `main`: product vision, numbered requirements with whether each is
built, the cartridge model, the developer guide, certification, versioning,
content policy, developer terms, privacy, the threat model, fairness,
accessibility, operations, release, roadmap and a glossary, plus
`open-questions.md`, which records the owner's decisions and the options each
one beat.

**Eight product decisions are settled and should not be re-asked.** Five slots
per player; a cached future day stored encrypted with window wide keys; an
account merge the player chooses, with the unchosen history retained; general
audience with a strict privacy posture; free for now, revisited before the
first outside cartridge is certified; a retraction notice on next open with no
push; logs kept thirty days with milestones promoted into records; and a
curated content file permitted where no declaration expresses the content,
under the four conditions in `platform/content-policy.md`. Each is in the
requirement it binds and in `decision-log.md` with its reasoning.

**The engine's complexity ceiling moved, and the engine side is built.**
Protocol `1.3.0` adds three declared capabilities: a second and third round of
content questions that may depend on the earlier rounds' results, boards made
of stages whose gating the engine verifies rather than believes, and difficulty
reported per axis with the banded score recomputed from the axes. Additive, so
all five games load and generate with no edits.
`PUZZLE_FORMAT_VERSION` is now `3`. Two things to know before building on it:
**no shipped game is staged yet**, so the capability is proved only by
`tests/unit/test_staging.py` and the chained fixture game beside it; and **a
staged game may not declare a witness**, which the descriptor refuses, because
a witness's costs are counted over one unconstrained space and that is the
wrong space for every stage after the first. Per stage witnesses and a hint
walk that picks the active stage are specified and unbuilt. The design is
`architecture.md` phase 10 and the reasoning is `decision-log.md`.

**All of that platform document work is decided and unbuilt.** The player's slot library, the
thin client, the encrypted cache window, the merge flow, the retraction notice,
log retention and the content file conditions are requirements with no code
behind them. `platform/roadmap.md` orders them, and its reading is that the
player facing layer is now the bottleneck: five games and no client means the
platform's flexibility is unproven against a real player.

Last full run: **3,933 passing, 0 failing** (2,098 platform, 733 grouping, 248
letters, 270 figures, 288 numbers, 296 digits), plus 2,096 passing with every
game uninstalled, the 2 differences being the guards that refuse to let the
boundary suite pass vacuously. That second run is what keeps the platform's
independence from a game an actual property rather than a claim.

### The games

| directory | game id | content | ships |
|---|---|---|---|
| `games/grouping` | `grouping` | lexical, WordNet plus an overlay | `build/days-30.json` |
| `games/letters` | `missing_letters` | lexical, its own curated taxonomy | -- |
| `games/figures` | `shape_trio` | simple objects, generated | `build/shape-trio-days-30.json` |
| `games/numbers` | `odd_number_out` | numbers, generated | `build/odd-number-out-days-30.json` |
| `games/digits` | `digit_lock` | digits, generated | `build/digit-lock-days-30.json` |

### Two snapshots, and only one of them is expensive

`docs/rebuild-with-batch2.sh` builds **A2**, the lexical snapshot games one and
two read. It needs WordNet exports, a frequency table and an embedding model,
and it is slow for good reasons.

`docs/build-symbolic-snapshot.sh` builds the **symbolic** snapshot games
three, four and five read. It needs none of those: the content is three
declarations on a command line, the build takes seconds, and it reproduces
byte for byte. 181 entities, 1,758 facts, 40 categories, no words in it at
all.

That difference is why `scripts/ci.sh symbolic` can do what the lexical
pipeline cannot: rebuild the snapshot *and* regenerate all three committed day
runs on every change, failing if any moved. Verified 2026-10-12: a fresh
build reproduces all three runs byte for byte.

### Where the work happens: the cloud, not a local checkout

The repository is the environment. A Codespace or any container built from
`.devcontainer/` gets Python 3.13, the platform and every game installed, and
can run the whole suite and rebuild a snapshot with no local toolchain. That is
the canonical place to work. A checkout on a personal machine is a convenience
mirror: pull it or delete it, nothing depends on it.

The reasons are concrete rather than preference. The suite needs Python 3.11 or
later for `StrEnum`, and the Windows checkout had 3.10 with a Windows `.venv`.
Git on the linked-computer shell has no push credentials and cannot delete its
own `.git` lock files, so a `pull` there half applies and then wedges. And none
of the heavy inputs are local: lexicons come from a GitHub release, frequency
from pypi, and coverage needs no model at all.

CI runs in three workflows. `tests.yml` runs the platform suite **with no game
installed**, then every suite with the games installed, on Python 3.11 and
3.13; the first step is what keeps the platform's independence from a game an
actual property rather than a claim. `coverage.yml` rebuilds build A2 and fails
if `build/coverage-A2.json` or the collision report has changed, which retires
the last form of this project's recurring bug: a committed number that no
longer matches what the code produces. It pins `wordfreq`, because frequency
bands decide which entities can be tiles and an upstream release would
otherwise look like a content regression. `symbolic.yml` does the same for the
symbolic snapshot and the three day runs.

### Resolved this session (all committed)

0. **A game could publish an answer nothing verified, and now cannot.** Found
   by probing the gates with a dishonest verifier rather than by reading the
   code: a real `shape_trio` board with its stated trio replaced by three tile
   ids not on the board, reported with `solutions=()` and
   `solutions_truncated=True`, was accepted. `shape_trio` is `EXACTLY_ONE` with
   `COMPLETE` verification, the strictest combination here. Two rules close it:
   a truncated list naming no solution is a protocol error, and the comparison
   runs against whatever was named. Measured cost to the five games: zero, and
   a test asserts that zero rather than remarking on it. `PhantomSolutionGame`
   is the sixth broken fixture, the first whose board is sound and whose report
   is the lie. Reasoning in `docs/decision-log.md` part 6.

   Worth carrying forward as method: this was the fourth promise found to be
   weaker than its prose, and all four were found by attacking the engine
   rather than reading it. The other three are listed under open decision 4.

1. **The platform can hold content that is not words, and three games prove
   it.** Four platform changes and three game distributions; details in
   `docs/architecture.md` phase 9 and `docs/decision-log.md` part 5. The short
   version: `puzzlegen/symbols` is a peer of `puzzlegen.surface` and owns the
   glyph, the spoken label and the ordinal; `puzzlegen/providers/generated.py`
   holds two providers whose content is a declaration rather than a file;
   `ContentQuery` gained a value window and a value ordering, honoured only by
   the operations that select entities one at a time; and
   `tools/build_snapshot.py` no longer requires a lexicon.

   What did not change is worth as much: the query layer, the policy service,
   the pipeline, the verifier, the uniqueness contracts and the session and
   share layers were already domain-neutral and needed nothing. Phase 3's "the
   graph is not about words" held.

2. **Three contracts that had never been exercised now are.** `shape_trio` is
   the first game to promise `COMPLETE` verification over a real combinatorial
   space (220 triples at twelve tiles, all enumerated). `digit_lock` is the
   first to declare `UNIQUE_DEDUCTION`, and the first whose generator stops on
   "exactly one code survives" rather than "the secret survives" -- which is
   what that contract means and differs precisely when a board is not
   deducible. `odd_number_out` is the first game whose content has a scale.

3. **Two invented numbers, caught by measuring.** `shape_trio`'s hard band
   originally targeted a decoy density of 0.38, which is above anything either
   board size produces, so a hard day would have ranked its candidates by an
   unreachable number. And the difficulty cut points were first fitted over
   every candidate the generator offers, which is the right sample for game two
   and the wrong one here: these bands change only the generator's ordering, so
   over all candidates the best possible pair bands 43% correctly. Fitted over
   the engine's own search window the same model bands 90%. Both are recorded
   with their measurements in the descriptors.

4. **A fixture that was wrong in the way fixtures are.** The figure tests'
   padding figures were hand-picked to "look unrelated" and one of them closed
   a second trio with a member of the seed trio: three circles whose fill,
   count and colour were each all different. Looks-unrelated is not a property
   the rule knows about. They are now produced by the same construction the
   generator uses, and a test asserts the property on the built board.

## Open decisions (what the next conversation resolves)

1. **The colour leak is still open, and the rule that was chosen cannot close
   it.** Unchanged from the last session and still the clearest piece of work
   available on game one. Measured, not argued: build B with the category-leak
   gate in place still produces the board `word that is also a color` over
   `airplane`, `finch`, `citrus`, `pink`. Overlay categories are taxonomy
   roots, so `overlay.color` has no relation to the lexical `color` at all, and
   the `pink` group fills cleanly from words the curator never tagged as
   colours. The giveaway is the visible label, not a stray tile.
   The fix is to supply the missing link as content: an optional `anchor` on an
   overlay category naming the lexical category it means (`overlay.color` ->
   `category:color`, oewn-04963771-n). The rule then becomes the decided one
   and is general: a visible group may not be the anchor, nor an ancestor or
   descendant of it. Work: a curated-schema field, builder support, the 14
   overlay categories reviewed for an anchor each, the gate extended, tests.
   Not blocking: build A ships without `color`.

2. **Nothing on the symbolic side is red, and three things are thin.** Stated
   as candidates rather than as work that needs doing.

   The `odd_number_out` difficulty model cannot discover that a familiar
   property on a tight board is harder than its tier says, because obscurity
   carries the largest weight and is a step function of the tier. The same
   shape of limit applies to `digit_lock`. Both are the right trade until
   there is play data, and both are recorded in their descriptors.

   `odd_number_out`'s obscure tier is thin: ten two-digit numbers are
   triangular and five are Fibonacci, so hard boards are mostly triangular.
   Widening the window would fix it and would cost the property that makes
   digit count universal, so it is a real trade rather than an oversight.

   `digit_lock` could state facts the digits taxonomy already holds and this
   release does not use -- `digit_sum`, `is_square` -- which would widen the
   easy band's lever. `PROPERTY_KINDS` is read rather than written down in
   the places that count it, so a fourth kind is a change in one module.

3. **The subprocess sandbox is still unbuilt.** Unchanged, and now with five
   games rather than two behind the import-time invariants. The sandbox
   remains the stronger mechanism for code that never enters the repository.
   `docs/external-games.md` is the design work, and its conclusion is that the
   sandbox is not the next thing to build: the import invariants do not
   constrain a hostile author at all, which is measured there, so the
   correctness gates come first. The truncation gate above was the first of
   them. Three remain, named in that document as F2, F3 and F4.

4. **Three more promises are weaker than their prose, and one cannot be
   fixed.** Measured, not argued, in `docs/external-games.md`. `states_examined`
   is a plugin's claim and the `search_bound` every descriptor declares never
   constrains a published puzzle. Candidate consumption, payload size,
   presentation, hints and solution lists are all unbounded, and the engine has
   no timeout or memory limit anywhere. The import invariants have three
   evasions, and a fourth, an attribute walk from the granted port to the
   content service, cannot be closed by any amount of parsing: five designs for
   hiding it were attacked and all five were reached, because `__closure__`,
   `__globals__` and `__class__` are ordinary attributes. That last one is a
   ceiling on the mechanism rather than a task, and the suite's docstring
   should say so.

### The first staged cartridge is undecided

The engine can now publish a board made of gated stages and nothing uses it.
Two readings, and the choice is the owner's:

- **A new game.** A sixth cartridge built around a chain, which proves the
  capability against real content and real difficulty calibration. Costs a
  game's worth of work, and the first staged board is also the first authored
  hint ladder since the witness rule, because a staged game may not declare a
  witness.
- **A second lens over content a game already uses.** Cheaper, and it tests
  the capability where the content is known to hold up. Weaker as evidence,
  because the interesting question about stages is whether the content can
  sustain a second stage that is genuinely undecided before the first.

Either way the per stage witness is the piece to build first if the hints
matter, because an authored ladder on a staged board is the exact thing
`witness.py` exists to avoid.

## Standing cautions

- **The recurring failure mode is a diagnostic that recomputes a value instead
  of reading what the engine actually did.** Four instances now: a hand-copied
  threshold that drifted, `generate_days.py` reading the drawn size, the
  collision report's parent-loss logic, and this file's own A/B table. When a
  tool and the engine could disagree about a number, make the tool import the
  real constant or read the real output, never re-derive it. When a *document*
  and an artifact could disagree, rebuild the artifact.
- The second instance of the same shape, worth its own line: a lexicon named for
  a root it does not hold. Check `root` inside the file, not the filename.
- Never glob `content/seeds/wordnet-*.lexicon.json`; name exact roots.
  `wordnet-mini` is an 8-synset test fixture a glob would wrongly include.
- Line endings are handled by the committed `.gitattributes`. If a mass of
  modified text files appears, the attributes file was lost or bypassed; do not
  commit that churn.
- Git on the linked-computer shell has no commit identity by default and no push
  credentials, and cannot delete its own `.git` lock files without folder delete
  permission. Committing and pushing from a cloud clone works and is what this
  session did.
- Overlay categories are roots in the overlay taxonomy. Any rule phrased as
  "ancestor or descendant of the hidden category" has nothing to stand on until
  decision 1 above is done.
- A difficulty scale is calibrated over the boards the engine *reaches*, not
  over every candidate the generator offers, unless the band changes which
  boards exist. Game two is the one case where it does. Getting this wrong
  does not fail: it produces a model that bands 43% correctly and looks like a
  bad model rather than a bad sample.
- A band target has to be reachable. Both symbolic number games had a target
  outside what their content can produce in a first draft, and the symptom in
  both cases is not an error but a ranking that silently becomes a shuffle.
- `build/` is gitignored, so `git status --porcelain` says nothing about a
  committed artifact that was never committed. A check on a build artifact
  needs `git ls-files` as well, which is how the symbolic day runs are
  checked.

## Reading order

1. What the person says now.
2. Files attached to the conversation.
3. Project memory and docs.
4. Repository files (lowest priority; can be stale).

`CLAUDE.md` carries the documentation rule, which every session follows:
a decision, plan or finding is recorded when a file states it, says why and
says where it stands. `docs/documentation-rule.md` is the routing table for
which document owns which subject.

## Response conventions

No preamble, recap, or filler. Decisions and open questions in lists at the end.
Every multi-option choice gets a plain-language explanation plus a benefits
versus costs weighing before any recommendation. State anything over ~300 lines
before producing it and wait. CLI commands on a single line, no continuations.
No dashes except within a word.
