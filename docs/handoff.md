# PuzzleGen Handoff

This file is overwritten at the end of every conversation in the Puzzle
Universe project. It is the first thing a new conversation reads. It answers
three questions: where things stand, what you need to do before the next
conversation starts, and what the next conversation should do first.

**You do not need to read the rest of this conversation's transcript to
continue this work.** Everything a new conversation needs is in this file,
plus `docs/decision-log.md`'s "Phase 7, continued" section (why each choice
was made) and `docs/architecture.md`'s "Phase 7, continued" section (how each
piece works). Read those two sections before re-deciding anything that looks
unresolved below; the reasoning may already be settled.

## Where things stand: phase 7's content goal is met, with two open gaps

The handoff this file replaced said phase 7 closes when a real board
generates end to end. **That happened.** On a snapshot built from this
project's sixteen WordNet export roots, with real (non-development)
embeddings covering the words involved, a real day's pipeline — the same
`GenerationPipeline` production uses, verifier included — produced a board
that was unique, COMPLETE, and scored on-target for its requested difficulty.
The user independently confirmed 30 of 30 days generating a board across
every supported size (5 through 9), committed to the repository, and pushed.

This was not a small fix. Getting here required a sequence of real
discoveries, in order, each hiding the next:

1. The original grouping rule (direct category membership) cannot produce a
   good visible group from an imported dictionary hierarchy — it was
   structurally guaranteed to find either "no groups" or "one word with many
   names" (`synonym_density.py` demonstrates this is universal, not
   occasional). Fixed by adding a second grouping mode, `SIBLINGS`
   (distinct children of one parent), used for game 1's lexical queries.
2. Picking which synonym represents a child needed a rule (Option R: the
   overlay's own tagged word wins, else the category's own name).
3. The uniqueness verifier's partition-validity check was nearly vacuous on
   real ancestry-bearing tiles (ten "craft" tiles could be split 126 ways,
   all silently "valid"). Fixed by requiring mutually incomparable group
   labels, not just non-identical ones.
4. Two separate search-order bugs (the same root cause, in two places): an
   unspread `itertools.combinations` meant the first N offers of any large
   pool never varied enough to be disjoint. Fixed in both the content
   service's group search and the game's own four-group quadruple search
   (`covering_quadruples`).
5. The temptation floor (requiring the four visible groups to share family
   resemblance) was blocking real, already-authored overlay categories. The
   user chose **Option B**: lower the floor to zero, treat the whole lexical
   taxonomy as one pool, and report a `relatedness` confidence (0 to 1) on
   every board instead of refusing cross-domain ones.
6. A category-merge bug was found and fixed: two senses of one word (e.g.
   `viola` the plant vs. the instrument), exported under different roots,
   were merging into one category that kept only the *first* import's
   parent, silently discarding the other sense's ancestry. Fixed to union
   both parents, which required adding a cycle check that did not previously
   exist (parents could not be added after construction before this change).
7. A day now tries every supported board size in order (its own drawn size
   first, then the rest), not just one, so a day whose preferred size the
   content cannot serve falls back instead of failing outright.

Full reasoning for every one of these is in the decision log; exact mechanics
are in the architecture doc. **Do not re-derive any of this from scratch** —
check those two files first.

## Two things are still open, and both are content/build-process work, not design work

### Gap 1: the committed repository cannot reproduce the 30/30 result

Checked directly, this session: `content/graph.sqlite` as committed is a
24,576-byte stub, not a real database. `content/seeds/embeddings.json` does
not cover every committed lexicon root — confirmed missing or incomplete for
`planet`, `sport`, and parts of `fish`, `insect`, `reptile`, `flower`, `herb`,
`flavorer`, `kitchen` at the point this was checked. The user's Codespace
evidently had locally-regenerated derived files that were never fully
committed at each push.

**Fix, to run in the Codespace, in order:**

```bash
# 1. Confirm the exact, final list of lexicon roots to use. See "stray files"
#    note below first -- several files in content/seeds/ are NOT meant to be
#    part of a real build.

python tools/build_snapshot.py --write-inputs build/ \
    --lexicon content/seeds/wordnet-<root1>.lexicon.json \
    --lexicon content/seeds/wordnet-<root2>.lexicon.json \
    ... \
    --overlay content/seeds/overlay.curated.json

python tools/export_frequency.py --terms build/terms.txt \
    --out content/seeds/frequency.json
python tools/export_embeddings.py --texts build/texts.txt \
    --out content/seeds/embeddings.json

rm content/graph.sqlite
python tools/build_snapshot.py --db content/graph.sqlite --label <today's date> \
    --now $(date -u +%Y-%m-%dT%H:%M:%S+00:00) \
    --frequency content/seeds/frequency.json \
    --embeddings content/seeds/embeddings.json \
    --lexicon content/seeds/wordnet-<root1>.lexicon.json \
    ... (the same exact list as step 1)

python tools/generate_days.py --db content/graph.sqlite --start 2026-10-01 \
    --days 30 --json build/days-30.json
git add content/graph.sqlite content/seeds/frequency.json \
    content/seeds/embeddings.json build/days-30.json
git commit -m "Regenerate derived files from the full lexicon set; reproducible 30/30"
git push
```

**`content/graph.sqlite` must either be committed in full (verify it is not
gitignored as `*.sqlite`; if it is, change the `.gitignore` rule from
`*.sqlite3` to also exclude nothing broader than that, or use Git LFS) or not
committed at all with the runbook corrected to say so.** A 24 KB stub
masquerading as the real file is worse than no file, because it looks correct
at a glance.

**Stray/duplicate lexicon files, found this session, should be removed before
the rebuild above:** `content/seeds/` currently also contains
`wordnet-bird1.lexicon.json` (exact duplicate of `wordnet-bird.lexicon.json`),
`wordnet-bird3.lexicon.json` (root resolved to "dame," 1 synset — almost
certainly a mis-resolved export, not bird-related), `wordnet-bird5.lexicon.json`
(root "shuttlecock," 1 synset — same problem), `wordnet-carnivore1.lexicon.json`
(exact duplicate of `wordnet-carnivore.lexicon.json`), and
`wordnet-carnivore2.lexicon.json` (1 synset — broken/partial). The canonical,
correctly-resolved files (`wordnet-bird.lexicon.json`,
`wordnet-carnivore.lexicon.json`, `wordnet-herb.lexicon.json`, and presumably
the rest) were checked and are intact. This is the third time this project has
hit a "wrong file in the directory" incident (`architecture.md`'s "things
phase 7 got wrong" section records the first two); it is worth adding a
permanent habit — never glob `wordnet-*.lexicon.json`, always name the exact
roots — rather than only cleaning up this instance:

```bash
git rm content/seeds/wordnet-bird1.lexicon.json \
       content/seeds/wordnet-bird3.lexicon.json \
       content/seeds/wordnet-bird5.lexicon.json \
       content/seeds/wordnet-carnivore1.lexicon.json \
       content/seeds/wordnet-carnivore2.lexicon.json
```

Confirm the exact current canonical list with:
```bash
for f in content/seeds/wordnet-*.lexicon.json; do
  python -c "
import json
d = json.load(open('$f'))
names = {s['id']: s['name'] for s in d['synsets']}
print('$f', '->', names.get(d.get('root'), d.get('root')), len(names), 'synsets')
"
done
```
A row with a tiny synset count or a root name that doesn't match the
filename is almost certainly another stray and should be investigated before
inclusion.

### Gap 2: every generated board is currently identical

Confirmed against the user's own committed `build/days-30.json`: all 30 days
produced the exact same hidden group (`word that is also a color`) and the
exact same score. **This is not a bug in the day-to-day randomization** — it
is correctly wired and was specifically checked. The cause, confirmed by
direct measurement: the generator currently offers **exactly one candidate**
on a real day, so there is nothing for the day's own RNG to choose among.

This will resolve as more overlay categories become feasible — the
`measure_coverage.py` output already shows this number rising across the
session (1 → 4 feasible categories at size 5). It is a content problem, not a
code problem. **Do not attempt to fix this by changing candidate selection or
ordering logic** — the mechanism is correct and was verified correct; the
input to it is thin.

**What to close next, in order of apparent leverage** (from the most recent
`measure_coverage.py --difficulty medium` output; re-run it fresh before
trusting these specifics, since the lexicon set may have changed):

1. **`thing with teeth`** — reaches 3 of 4 needed homes. Absent:
   `beaver`, `chainsaw`, `comb`, `jigsaw`, `rake`, `rodent`. A `rodent` export
   (for `beaver`, `rodent` itself) and checking whether `hand_tool`'s depth
   reaches `chainsaw`/`jigsaw`/`comb`/`rake` (they may simply be deeper than
   the current export depth, not absent from WordNet) are the two things to
   check first.
2. **`word that is also a person's name`** — reaches 3 of 4. Absent:
   `erica`, `hazel`, `heather`, `holly`, `iris`. The `flower` root should
   supply at least `iris` and `holly`; check why they didn't land (possibly a
   depth or sense-resolution issue, the same class as the `herb.n.01` vs.
   `herb.n.02` ambiguity resolved earlier this session).
3. **`thing with strings`** — blocked by *small parents*, not missing words:
   `bowed stringed instrument` has only 4 kinds (`cello`, `viola`, `violin`,
   plus one more), one short of 5. This is the "kinds of kinds" idea the user
   raised and the assistant set aside pending a decision — it was not built.
   If closing this category matters, that idea needs to be revisited now that
   real data shows exactly where it would help (a parent one or two kinds
   short, not zero).
4. **`thing that is blown`**, **`thing that floats`**, **`thing found in a
   kitchen`** — each has `no_valid_quadruple` or `no_shared_domain` as its
   blocker now (not missing words), meaning Option B's floor-of-zero should
   already allow these; re-check with a fresh `measure_coverage.py` run
   whether they are now feasible, since the coverage numbers above were
   captured mid-session and may already be stale relative to the latest
   commit.

Run this to get a current, authoritative picture before doing anything else:
```bash
python tools/measure_coverage.py --db content/graph.sqlite --difficulty medium \
    --json build/coverage-current.json
```

## Manual steps required before the next conversation begins

1. Work through Gap 1 (reproducibility) above. This is the actual blocker to
   trusting anything else: until it's closed, no one — including a future
   conversation — can verify any further claim against the real repository
   state without rebuilding from scratch each time.
2. Re-run `tools/report_lemma_collisions.py` over the final, cleaned lexicon
   list (after removing the stray files above) and check its "overlay words
   whose second meaning lost its parent in the merge" section. The merge fix
   (union parents) means this should now report 0 affected overlay words
   regardless of what it finds, but worth confirming directly rather than
   assuming.
3. Decide whether to pursue Gap 2's item 3 (small-parent categories needing
   a "kinds of kinds" mechanism) now or defer it; it is the one open design
   question from this session that was raised, discussed, and explicitly not
   resolved.

## What the next conversation must do first

Before any further phase work: ask whether the manual steps above are
complete. Accept exactly one of two replies:

- **`Steps Complete`** — run `tools/measure_coverage.py --difficulty medium`
  and `tools/generate_days.py --days 30` fresh, from the just-rebuilt
  snapshot. If `generate_days.py` shows more than one distinct hidden group
  across 30 days, phase 7 is fully closed (reproducible, and no longer
  single-board-only); open whatever the project's next phase is. If it is
  still one board, continue closing Gap 2's categories above, in order.
- **`Detour {filename.md}`** — stop, read the named file (must be attached
  to the chat or a path under `docs/` in the repository), and ask what to do
  with it before touching phase work. Do not resume phase work until the
  detour is explicitly closed.

If the reply is neither of these two forms, ask for it to be restated as one
of them. Do not guess which was intended.

## Reading order for this project (unchanged)

1. What the person says in the current conversation.
2. Files attached to the current conversation.
3. This project's own file context (memory).
4. Files pulled from the connected GitHub repository
   (`https://github.com/BigBadChicago/PuzzleGen`).

A repository file is lowest priority because it can be stale relative to what
the person and the conversation already know — and this session found exactly
that kind of staleness twice (the committed `graph.sqlite` stub; the
committed embeddings missing roots). Always verify a repository claim (file
sizes, which roots a lexicon file actually resolves to, whether a build
actually succeeds) rather than trusting a filename or a prior commit message.

## Standing project conventions (apply regardless of phase, unchanged)

- No preamble, no recap, no filler; decisions and open questions stated in
  lists at the end of each response.
- Every multi-option question or choice gets a plain-language explanation
  (assume a middle-school reading level) plus a benefits-versus-costs
  weighing for each option, before any recommendation.
- Files longer than ~300 lines: state what is about to be produced and wait
  for confirmation before generating it.
- Never glob `wordnet-*.lexicon.json` or any other wildcard over
  `content/seeds/`; name exact files. This project has hit stray/duplicate/
  broken files in that directory three separate times.
- When a diagnostic tool and the real engine could disagree about a number
  (feasibility, candidate count, a rejection reason), make the tool import
  the real constant or function rather than hand-copying its value — this
  session found and fixed two cases where a hand-copied threshold had drifted
  from the real one.
- Before trusting any "it works" claim about committed content, check file
  sizes and actually attempt a rebuild from the committed files alone. This
  session's two open gaps were both found exactly this way.
