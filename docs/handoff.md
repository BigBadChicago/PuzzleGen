# PuzzleGen Handoff

First thing a new conversation reads. Verify repository claims directly (file
sizes, what a lexicon resolves to, whether a build runs) rather than trusting a
filename or commit message; this project has been burned by stale committed
artifacts more than once. Last session found two: a coverage table in this file
that disagreed with the artifact it claimed to summarise, and a lexicon file
named for a root it does not hold.

Last verified: 2026-10-10, against a fresh clone built and tested from scratch.

## Where things stand

Game 1 is closed. Ship A is decided and A is the gate build. The second batch of
lexicon roots is exported, committed and measured. The next work is game 2 and
the subsystem under it.

Nothing is pending on origin: `main` is current.

### Where the work happens: the cloud, not a local checkout

The repository is the environment. A Codespace or any container built from
`.devcontainer/` gets Python 3.13, the platform and every game installed, and
can run the whole suite and rebuild a snapshot with no local toolchain. That is
now the canonical place to work. A checkout on a personal machine is a
convenience mirror: pull it or delete it, nothing depends on it.

The reasons are concrete rather than preference. The suite needs Python 3.11 or
later for `StrEnum`, and the Windows checkout had 3.10 with a Windows `.venv`.
Git on the linked-computer shell has no push credentials and cannot delete its
own `.git` lock files, so a `pull` there half applies and then wedges. And none
of the heavy inputs are local: lexicons come from a GitHub release, frequency
from pypi, and coverage needs no model at all.

CI runs in two workflows. `tests.yml` runs the platform suite **with no game
installed**, then every suite with the games installed, on Python 3.11 and
3.13; the first step is what keeps the platform's independence from a game an
actual property rather than a claim. `coverage.yml` rebuilds build A2 and fails
if `build/coverage-A2.json` or the collision report has changed, which retires
the last form of this project's recurring bug: a committed number that no
longer matches what the code produces. It pins `wordfreq`, because frequency
bands decide which entities can be tiles and an upstream release would
otherwise look like a content regression.

A coverage measurement needs no network and no embedding model. Verified: a
snapshot built with `--dev-embeddings` plus a frequency table exported on the
spot reproduces `build/coverage-A.json` byte for byte, because coverage is
structural and applies no embedding gate. Only a published snapshot needs real
vectors. WordNet data for `tools/export_wordnet.py` comes from the GitHub
release (`globalwordnet/english-wordnet`, 2024 edition) and loads with
`wn.add(path)`; `en-word.net` itself is not reachable from a sandboxed runner,
and `docs/rebuild-with-batch2.sh` therefore skips any root whose lexicon is
already committed.

Last full run: **2,281 passing, 0 failing** (1,568 platform, 713 game one), plus
1,566 passing with the game uninstalled, the 2 differences being the guards
that refuse to let the boundary suite pass vacuously.

### Resolved this session (all committed)

0. **Games are distributions now, and the cloud is the environment.** Game one
   moved to `games/grouping/` with its own pyproject, package, tests, tools and
   content, advertised through the `puzzlegen.games` entry point group;
   `GameRegistry.discover()` registers what is installed. `--overlay` lost its
   default in `tools/build_snapshot.py`, because the overlay is one game's
   taxonomy. `puzzlegen/testing` is new: the fixtures a game's tests need, as a
   pytest plugin, so a game's test setup is one short conftest. Two new
   invariants state the property: the platform imports no game and holds no
   game id as a literal, checked against what is installed. `.devcontainer/`
   and two CI workflows make a cloud container the place this is built and
   measured. Details in `docs/architecture.md` phase 8.


1. **Ship A, and a correction to this file.** The A/B table a previous handoff
   carried (build A giving 1, 3, 4, 3, 3 working hidden categories across group
   sizes 5 to 9) is not what the committed artifact says. `build/coverage-A.json`
   says 5, 1, 0, 0, 0, and a rebuild reproduces it byte for byte. Build A
   unblocked size 5 only. Sizes 6 to 8 are unblocked by batch 2, below.

2. **The hidden reading must be the only one on the board.** The chosen rule
   over a blacklist, implemented as `has_no_category_leak` in
   `puzzlegen/games/grouping/generate.py` (refusal reason `category_leak`),
   mirrored in `tools/overlay_coverage.py` (`a_filler_tile_is_also_a_hidden_word`).
   No tile outside the hidden group may carry the hidden category; no visible
   category may be an ancestor of the hidden one. Measured cost across builds A,
   A2 and B: zero. It never fired on real content; it is a guard on a hole that
   nothing else in the system covers, since the verifier excludes the overlay
   axis by design and cross-membership never sees the hidden category. Tests:
   `tests/unit/test_grouping_category_leak.py`, and `TestTheLeakGate` in
   `tests/unit/test_overlay_coverage.py`.

3. **Batch 2 of the lexicon roots.** Nine roots (dairy_product, tool, rodent,
   nut, bulbous_plant, vine, angiospermous_tree, marsh_plant, shrub), all
   committed, all exported at depth 6. Every one of the 22 previously unsupplied
   overlay words is now supplied; `overlay_missing` falls from 24 to 2 (`coral`
   wants the colour sense, `coffee` the brewed sense, and both ride on roots not
   in A). Feasible hidden categories by group size go from 5, 1, 0, 0, 0 to
   7, 6, 2, 1, 0, with two hidden categories no build had before (`thing with a
   handle`, `word that is also a person's name`). Three corrections to the
   earlier root analysis, including that `mate` is reachable after all and
   `heath` is redundant, are in `docs/candidate-roots.md` section 6. Reproduce
   with `docs/rebuild-with-batch2.sh`.

4. **`wordnet-tool.lexicon.json` was the `hand tool` subtree, not `tool`.**
   Renamed to `wordnet-hand_tool.lexicon.json`; the real `tool` subtree
   (oewn-04459089-n, 374 synsets) now holds the old name. Scripts and docs
   updated. A root list that says `tool` and means `hand tool` builds a
   different snapshot and nothing errors.

## Open decisions (what the next conversation resolves)

1. **The colour leak is still open, and the rule that was chosen cannot close
   it.** Measured, not argued: build B with the gate in place still produces the
   board `word that is also a color` over `airplane`, `finch`, `citrus`, `pink`.
   Overlay categories are taxonomy roots, so `overlay.color` has no relation to
   the lexical `color` at all, and the `pink` group fills cleanly from words the
   curator never tagged as colours. The giveaway is the visible label, not a
   stray tile.
   The fix is to supply the missing link as content: an optional `anchor` on an
   overlay category naming the lexical category it means (`overlay.color` ->
   `category:color`, oewn-04963771-n). The rule then becomes the decided one and
   is general: a visible group may not be the anchor, nor an ancestor or
   descendant of it. Work: a curated-schema field, builder support, the 14
   overlay categories reviewed for an anchor each, the gate extended, tests.
   Not blocking: build A ships without `color`, so this is what unblocks build B
   rather than what holds game 1.

2. **The surface form subsystem, then `missing_letters`.** Platform-first was
   the standing lean and nothing found since argues against it. Design is in
   `docs/architecture.md` (session 2) and `docs/missing-letters-requirements.md`
   section 2. Not yet built.

3. **`missing_letters` needs content before it needs code.** A throwaway probe
   over build A measured the real constraint, and it is not the letter matching:
   it is name length against board size. A category of L letters with b blanks
   needs L minus b marks on distinct tiles, so a board needs at least L minus b
   plus 1 tiles. At 6 tiles and 2 blanks, 21 of the 88 eligible categories work,
   and all 21 are short, obscure names (`coho`, `scup`, `myna`, `quahog`); at 8
   tiles, 27 categories are even eligible and 9 work; at 10 tiles, 17 and 3.
   Every overlay category fails at ordinary sizes, because their names run 14 to
   25 letters. So `missing_letters` targets are short, common lexical category
   names with enough frequency-filtered members, which is a content question the
   coverage tool for game 2 should answer before any generator is written. Build
   that tool first (`tools/measure_letters_coverage.py`, the analogue of
   `measure_coverage.py`), as the requirements doc already says.

4. **Nothing is red.** The content tracker in `test_overlay_seed.py` went green
   when batch 2 landed, with the qualifier above. The next content claim worth
   making is per build rather than per repository: which overlay words build A
   itself can define.

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
